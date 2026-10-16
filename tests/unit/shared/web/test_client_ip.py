"""Unit tests for caller-IP derivation behind trusted reverse proxies (#1441).

Pins that ``trusted_proxies`` CIDR ranges match (the Helm docs configure
``10.0.0.0/8``), that an untrusted socket's ``X-Forwarded-For`` is ignored, and
that IPv6 callers are bucketed per ``/64``.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from jentic_one.shared.web.client_ip import (
    client_ip,
    parse_ip,
    parse_trusted_proxies,
    rate_limit_ip_key,
)


def _request(socket_ip: str | None, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    scope: dict[str, object] = {"type": "http", "headers": headers}
    if socket_ip is not None:
        scope["client"] = (socket_ip, 1234)
    return Request(scope)


@pytest.mark.parametrize(
    ("socket_ip", "xff", "trusted", "expected"),
    [
        # No trusted proxies: the socket peer, XFF ignored.
        ("10.1.2.3", "203.0.113.9", frozenset(), "10.1.2.3"),
        # A CIDR entry matches the socket peer, so the forwarded client wins.
        ("10.1.2.3", "203.0.113.9", frozenset({"10.0.0.0/8"}), "203.0.113.9"),
        # A bare-IP entry still matches exactly.
        ("10.1.2.3", "203.0.113.9", frozenset({"10.1.2.3"}), "203.0.113.9"),
        # Untrusted socket: a spoofed XFF is ignored.
        ("198.51.100.7", "203.0.113.9", frozenset({"10.0.0.0/8"}), "198.51.100.7"),
        # Right-to-left: attacker-prepended hops are never reached.
        ("10.1.2.3", "1.1.1.1, 203.0.113.9, 10.9.9.9", frozenset({"10.0.0.0/8"}), "203.0.113.9"),
        # All-trusted chain resolves to the socket peer.
        ("10.1.2.3", "10.4.4.4, 10.5.5.5", frozenset({"10.0.0.0/8"}), "10.1.2.3"),
        # A malformed hop stops the walk at the socket peer (no caller-minted keys).
        ("10.1.2.3", "203.0.113.9, not-an-ip", frozenset({"10.0.0.0/8"}), "10.1.2.3"),
        # Trusted socket without XFF.
        ("10.1.2.3", None, frozenset({"10.0.0.0/8"}), "10.1.2.3"),
        # A hop carrying a port is still an address.
        ("10.1.2.3", "203.0.113.9:5555", frozenset({"10.0.0.0/8"}), "203.0.113.9"),
        # IPv4-mapped IPv6 socket peer matches an IPv4 range.
        ("::ffff:10.1.2.3", "203.0.113.9", frozenset({"10.0.0.0/8"}), "203.0.113.9"),
        # IPv6 proxy range.
        ("fd00::1", "2001:db8::5", frozenset({"fd00::/8"}), "2001:db8::5"),
    ],
)
def test_client_ip(socket_ip: str, xff: str | None, trusted: frozenset[str], expected: str) -> None:
    assert client_ip(_request(socket_ip, xff), trusted) == expected


def test_client_ip_without_socket_peer() -> None:
    assert client_ip(_request(None, "203.0.113.9"), frozenset({"10.0.0.0/8"})) == "unknown"


def test_parse_trusted_proxies_rejects_junk() -> None:
    with pytest.raises(ValueError):
        parse_trusted_proxies(["10.0.0.0/33"])
    with pytest.raises(ValueError):
        parse_trusted_proxies(["proxy.internal"])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("203.0.113.9", "203.0.113.9"),
        ("[2001:db8::1]:443", "2001:db8::1"),
        ("::ffff:192.0.2.1", "192.0.2.1"),
        ("garbage", None),
    ],
)
def test_parse_ip(value: str, expected: str | None) -> None:
    parsed = parse_ip(value)
    assert (str(parsed) if parsed is not None else None) == expected


def test_rate_limit_ip_key_buckets_ipv6_per_64() -> None:
    a = rate_limit_ip_key("2001:db8:1:2:aaaa::1")
    b = rate_limit_ip_key("2001:db8:1:2:ffff:ffff:ffff:ffff")
    assert a == b == "2001:db8:1:2::/64"
    assert rate_limit_ip_key("2001:db8:1:3::1") != a
    assert rate_limit_ip_key("203.0.113.9") == "203.0.113.9"
    assert rate_limit_ip_key("unknown") == "unknown"
