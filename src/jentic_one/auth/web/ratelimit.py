"""Shared helpers for the auth surface's pre-auth rate limiters.

Shared so every anonymous auth route (``/authorize`` and the rest of the
authorization flow, the local-login form, DCR registration, the
approval-status poll, the token and revocation endpoints, the
session-continue exchange) derives the caller's identity and reaches the
shared-state backend the same way, without cross-router imports of
underscore-private names.

A pre-auth limiter keys on what the caller cannot choose freely: its network
address. Any request parameter (``client_id``, a signed blob, a token) is
caller-chosen before it has been verified, so adding it to the key would hand
a caller a fresh bucket per value. The token endpoint is the one exception: it
prefixes the body ``client_id`` to the network, so one host running many
machine clients does not squeeze them all into one bucket.
"""

from __future__ import annotations

import ipaddress

import structlog
from fastapi import Request

from jentic_one.shared.config import TrustedProxyNetwork
from jentic_one.shared.state.backend import MemoryStateBackend, SharedStateBackend
from jentic_one.shared.web.client_ip import client_ip

logger = structlog.get_logger(__name__)

#: IPv6 clients are grouped by this prefix length. One host (or one customer
#: site) typically holds at least a whole /64 and can pick any interface ID
#: inside it, so grouping by full address would let it rotate into a fresh
#: bucket per request.
IPV6_CLIENT_PREFIX = 64

#: RFC 6052 well-known NAT64 prefix: the low 32 bits are the IPv4 client.
_NAT64_WELL_KNOWN_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


def get_auth_backend(request: Request) -> SharedStateBackend:
    """Return the app's shared auth-state backend (rate limits, consent handles)."""
    backend: object = getattr(request.app.state, "auth_state_backend", None)
    if isinstance(backend, SharedStateBackend):
        return backend
    logger.warning("auth_state_backend missing from app.state, using in-memory fallback")
    return MemoryStateBackend()


def _parse_client_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a client address, tolerating a forwarded hop's ``:port`` suffix.

    Some proxies forward ``203.0.113.7:51234`` or ``[2001:db8::1]:443``;
    without stripping the port, every source port would count as its own
    client.
    """
    candidates = [value]
    if value.startswith("[") and "]" in value:
        candidates.append(value[1 : value.index("]")])
    elif value.count(":") == 1:
        candidates.append(value.split(":", 1)[0])
    for candidate in candidates:
        try:
            return ipaddress.ip_address(candidate)
        except ValueError:
            continue
    return None


def _embedded_ipv4(addr: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 client an IPv6 address stands for, if it embeds one.

    IPv4-mapped (``::ffff:a.b.c.d``, a dual-stack listener), well-known-prefix
    NAT64 (``64:ff9b::a.b.c.d``, RFC 6052), 6to4 (``2002:abcd:ef01::/48``) and
    Teredo addresses all carry the client's IPv4 address, so they are budgeted
    as that IPv4 client rather than as an IPv6 /64.

    A network-specific translation prefix (the RFC 8215 local-use
    ``64:ff9b:1::/48`` or an operator's own) cannot be told apart from a
    native IPv6 network, so it is grouped by /64 like one: behind such a
    translator (SIIT in front of an IPv6-only server), every IPv4 client
    mapped into one /64 of that prefix shares a single bucket.
    """
    if addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    if addr in _NAT64_WELL_KNOWN_PREFIX:
        return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    if addr.sixtofour is not None:
        return addr.sixtofour
    if addr.teredo is not None:
        return addr.teredo[1]
    return None


def client_network(ip: str) -> str:
    """The network a client address is budgeted under.

    An IPv4 address stands for itself, as does an IPv6 address that embeds
    one (see :func:`_embedded_ipv4`), so a dual-stack or translated path does
    not give one client two identities. Any other IPv6 address stands for its
    /64, so a host cannot rotate interface IDs within its prefix to escape
    its budget; link-local and unique-local addresses follow the same rule.
    A value that is not an IP address (no socket peer, or an unparsable
    forwarded hop) is used as-is.
    """
    addr = _parse_client_address(ip)
    if addr is None:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(addr)
        if embedded is not None:
            return str(embedded)
        network = ipaddress.IPv6Network((int(addr), IPV6_CLIENT_PREFIX), strict=False)
        return str(network)
    return str(addr)


def client_rate_limit_key(
    request: Request, trusted_proxies: tuple[TrustedProxyNetwork, ...]
) -> str:
    """The pre-auth rate-limit key for this request: the caller's client network.

    :func:`client_ip` (``X-Forwarded-For`` honoured only from a trusted
    proxy) normalised by :func:`client_network` (forwarded port dropped, an
    IPv6 address that embeds an IPv4 client reduced to that IPv4 address,
    any other IPv6 address grouped by /64).
    """
    return client_network(client_ip(request, trusted_proxies))


__all__ = [
    "IPV6_CLIENT_PREFIX",
    "client_ip",
    "client_network",
    "client_rate_limit_key",
    "get_auth_backend",
]
