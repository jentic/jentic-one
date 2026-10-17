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


def _parse_request_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a socket or forwarded address for trust matching, or None if unparsable.

    An IPv4-mapped IPv6 address (a dual-stack listener) is unmapped so it
    matches IPv4 entries; a zone ID is ignored, as network membership is.
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
    membership; the first untrusted hop is the client. An unparsable hop is
    untrusted and returned as-is.
    """
    socket_ip = request.client.host if request.client else "unknown"
    if not trusted_proxies or not _is_trusted(socket_ip, trusted_proxies):
        return socket_ip
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return socket_ip
    hops = [h.strip() for h in forwarded.split(",")]
    for hop in reversed(hops):
        if not _is_trusted(hop, trusted_proxies):
            return hop
    return socket_ip


__all__ = ["client_ip", "get_auth_backend"]
