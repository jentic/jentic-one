"""Caller-IP derivation for pre-auth rate limiters.

Lives in ``shared`` so every surface that throttles anonymous traffic (the auth
surface's OAuth doors, the ``/auth/login`` and local-login password doors)
derives the caller identity the same way without a cross-surface import.

``trusted_proxies`` entries are IP addresses or CIDR ranges (a bare address is
a ``/32`` or ``/128``). ``X-Forwarded-For`` is honoured only when the socket
peer is inside one of them, and the header is walked right-to-left so hops an
attacker prepends are never reached while a trusted proxy is still appending.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from functools import lru_cache

from fastapi import Request

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

#: Prefix length an IPv6 caller is bucketed by. One subscriber is routinely
#: handed a whole ``/64``, so a per-address key lets one host rotate through
#: 2^64 fresh buckets.
IPV6_RATE_LIMIT_PREFIX = 64


def parse_trusted_proxies(entries: Iterable[str]) -> tuple[IPNetwork, ...]:
    """Parse proxy entries (addresses or CIDRs) into networks; raise ``ValueError`` on junk."""
    networks: list[IPNetwork] = []
    for entry in entries:
        network = ipaddress.ip_network(entry.strip(), strict=False)
        if isinstance(network, ipaddress.IPv6Network) and network.prefixlen >= 96:
            mapped = network.network_address.ipv4_mapped
            if mapped is not None:
                # ``::ffff:a.b.c.d`` names an IPv4 peer; match it as one.
                network = ipaddress.ip_network(f"{mapped}/{network.prefixlen - 96}")
        networks.append(network)
    return tuple(networks)


@lru_cache(maxsize=32)
def _networks(trusted_proxies: frozenset[str]) -> tuple[IPNetwork, ...]:
    return parse_trusted_proxies(trusted_proxies)


def parse_ip(value: str) -> IPAddress | None:
    """Parse one address as it appears in a socket peer or an XFF hop.

    Accepts an optional port (``1.2.3.4:5678``, ``[2001:db8::1]:443``) because
    some proxies append one. An IPv4-mapped IPv6 address is unwrapped so a
    dual-stack listener's ``::ffff:a.b.c.d`` peer keys and matches as IPv4.
    Returns ``None`` for anything that is not an IP address.
    """
    candidate = value.strip()
    if candidate.startswith("["):
        candidate = candidate[1:].split("]", 1)[0]
    elif candidate.count(":") == 1:
        candidate = candidate.split(":", 1)[0]
    try:
        addr = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _is_trusted(addr: IPAddress | None, networks: tuple[IPNetwork, ...]) -> bool:
    if addr is None:
        return False
    return any(addr.version == net.version and addr in net for net in networks)


def client_ip(request: Request, trusted_proxies: frozenset[str]) -> str:
    """Return the real client IP, honouring XFF only from trusted reverse proxies.

    The socket peer is used as-is unless it falls inside ``trusted_proxies``.
    From a trusted peer the ``X-Forwarded-For`` hops are walked right-to-left
    and the first hop outside ``trusted_proxies`` is the client. A hop that is
    not an IP address stops the walk at the socket peer: it cannot be a real
    client, and keying on it would let whoever wrote it mint fresh buckets.
    An all-trusted chain also resolves to the socket peer.
    """
    socket_raw = request.client.host if request.client else "unknown"
    socket_addr = parse_ip(socket_raw)
    socket_ip = str(socket_addr) if socket_addr is not None else socket_raw
    if not trusted_proxies:
        return socket_ip
    networks = _networks(trusted_proxies)
    if not _is_trusted(socket_addr, networks):
        return socket_ip
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return socket_ip
    for hop in reversed(forwarded.split(",")):
        addr = parse_ip(hop)
        if addr is None:
            return socket_ip
        if not _is_trusted(addr, networks):
            return str(addr)
    return socket_ip


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
    "parse_trusted_proxies",
    "rate_limit_ip_key",
]
