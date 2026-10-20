"""Caller-IP derivation for pre-auth rate limiters.

Lives in ``shared`` so every surface that throttles anonymous traffic (the auth
surface's OAuth doors, the ``/auth/login`` and local-login password doors)
derives the caller identity the same way without a cross-surface import.

``trusted_proxies`` entries are IP addresses or CIDR networks, parsed once at
config load into ``OAuthRateLimitConfig.trusted_networks``. ``X-Forwarded-For``
is honoured only when the socket peer is inside one of them, and the header is
walked right-to-left so hops an attacker prepends are never reached while a
trusted proxy is still appending.
"""

from __future__ import annotations

import ipaddress

from fastapi import Request

from jentic_one.shared.config import TrustedProxyNetwork

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

#: Prefix length an IPv6 caller is bucketed by. One subscriber is routinely
#: handed a whole ``/64``, so a per-address key lets one host rotate through
#: 2^64 fresh buckets.
IPV6_RATE_LIMIT_PREFIX = 64


def _is_port(value: str) -> bool:
    return value.isascii() and value.isdigit()


def _strip_port(value: str) -> str:
    """Drop a forwarded hop's port: ``203.0.113.7:51234`` or ``[2001:db8::1]:443``.

    Some proxies forward the peer's source port. Left on, it would make a
    trusted proxy's hop unmatchable and give the client a new identity per
    connection. A value that is not an address with a numeric port is
    returned unchanged.
    """
    if value.startswith("[") and "]" in value:
        host, _, rest = value[1:].partition("]")
        if rest and not (rest.startswith(":") and _is_port(rest[1:])):
            return value
    elif value.count(":") == 1:
        host, _, port = value.partition(":")
        if not _is_port(port):
            return value
    else:
        return value
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return value
    return host


def _parse_request_address(value: str) -> IPAddress | None:
    """Parse a socket or forwarded address for trust matching, or None if unparsable.

    A forwarded hop arrives here without its port (see :func:`_strip_port`);
    a socket address never carries one. An IPv4-mapped IPv6 address (a
    dual-stack listener) is unmapped so it matches IPv4 entries. A zone ID
    parses but does not affect membership.
    """
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def parse_ip(value: str) -> IPAddress | None:
    """Parse one address as it appears in a socket peer or an XFF hop.

    Accepts an optional port (``1.2.3.4:5678``, ``[2001:db8::1]:443``) because
    some proxies append one. An IPv4-mapped IPv6 address is unwrapped so a
    dual-stack listener's ``::ffff:a.b.c.d`` peer keys and matches as IPv4.
    Returns ``None`` for anything that is not an IP address.
    """
    candidate = _strip_port(value.strip())
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    return _parse_request_address(candidate)


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
    was written by the trusted proxy to its right, so while the client's own
    address is untrusted, a value it sends sits further left and is never
    reached. When every hop is trusted, the client is inside a trusted
    network (and can set its identity anyway), so the leftmost hop is used
    rather than the nearest proxy's address. A hop's ``:port`` is dropped,
    an unparsable hop is untrusted and returned as-is, and empty list
    elements are skipped (RFC 9110 §5.6.1).
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


def rate_limit_ip_key(ip: str) -> str:
    """Bucket key for a caller IP: IPv4 per address, IPv6 per ``/64``."""
    addr = parse_ip(ip)
    if isinstance(addr, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{addr}/{IPV6_RATE_LIMIT_PREFIX}", strict=False))
    return str(addr) if addr is not None else ip


__all__ = [
    "IPV6_RATE_LIMIT_PREFIX",
    "client_ip",
    "parse_ip",
    "rate_limit_ip_key",
]
