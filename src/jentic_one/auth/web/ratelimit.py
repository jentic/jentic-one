"""Shared helpers for the auth surface's pre-auth rate limiters.

Shared so the anonymous DCR registration router and /authorize derive the
caller IP and
reach the shared-state backend the same way, without cross-router imports of
underscore-private names.
"""

from __future__ import annotations

import ipaddress

import structlog
from fastapi import Request

from jentic_one.shared.config import TrustedProxyNetwork
from jentic_one.shared.state.backend import MemoryStateBackend, SharedStateBackend

logger = structlog.get_logger(__name__)


def get_auth_backend(request: Request) -> SharedStateBackend:
    """Return the app's shared auth-state backend (rate limits, consent handles)."""
    backend: object = getattr(request.app.state, "auth_state_backend", None)
    if isinstance(backend, SharedStateBackend):
        return backend
    logger.warning("auth_state_backend missing from app.state, using in-memory fallback")
    return MemoryStateBackend()


def _strip_port(value: str) -> str:
    """Drop a forwarded hop's port: ``203.0.113.7:51234`` or ``[2001:db8::1]:443``.

    Some proxies forward the peer's source port. Left on, it would make a
    trusted proxy's hop unmatchable and give the client a new identity per
    connection. A value that is not an address with a numeric port is
    returned unchanged.
    """
    if value.startswith("[") and "]" in value:
        host, _, rest = value[1:].partition("]")
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return value
    elif value.count(":") == 1:
        host, _, port = value.partition(":")
        if not port.isdigit():
            return value
    else:
        return value
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return value
    return host


def _parse_request_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a socket or forwarded address for trust matching, or None if unparsable.

    The value has had any port removed by :func:`_strip_port`. An IPv4-mapped
    IPv6 address (a dual-stack listener) is unmapped so it matches IPv4
    entries. A zone ID parses but does not affect membership.
    """
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _is_trusted(value: str, trusted_proxies: tuple[TrustedProxyNetwork, ...]) -> bool:
    addr = _parse_request_address(value)
    if addr is None:
        return False
    return any(addr in network for network in trusted_proxies)


def client_ip(request: Request, trusted_proxies: tuple[TrustedProxyNetwork, ...]) -> str:
    """Extract the real client IP, honoring XFF only from trusted reverse proxies.

    ``trusted_proxies`` is the pre-parsed
    ``OAuthRateLimitConfig.trusted_networks``. The socket address and each
    ``X-Forwarded-For`` hop (walked right to left) are trusted by network
    membership; the first untrusted hop is the client. Every hop examined
    was written by the trusted proxy to its right, so a value the client
    sends sits further left and is never reached. When every hop is trusted,
    the client itself is inside a trusted network and the leftmost hop is its
    address. A hop's ``:port`` is dropped, an unparsable hop is untrusted and
    returned as-is, and empty list elements are skipped (RFC 9110 §5.6.1).
    Repeated ``X-Forwarded-For`` lines are read as one comma-joined list
    (RFC 9110 §5.3), so a proxy that adds its own line cannot be outranked by
    a line the client sent first.
    """
    socket_ip = request.client.host if request.client else "unknown"
    if not trusted_proxies or not _is_trusted(socket_ip, trusted_proxies):
        return socket_ip
    forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
    hops = [_strip_port(h.strip()) for h in forwarded.split(",") if h.strip()]
    if not hops:
        return socket_ip
    for hop in reversed(hops):
        if not _is_trusted(hop, trusted_proxies):
            return hop
    return hops[0]


__all__ = ["client_ip", "get_auth_backend"]
