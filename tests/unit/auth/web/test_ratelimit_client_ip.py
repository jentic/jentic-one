"""Tests for ``client_ip`` — the pre-auth rate-limit identity behind proxies.

Pins #1880: ``auth.oauth_rate_limit.trusted_proxies`` entries are networks, so
a CIDR entry (as the Helm docs recommend) honours ``X-Forwarded-For`` from any
address inside it.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from jentic_one.auth.web.ratelimit import client_ip
from jentic_one.shared.config import OAuthRateLimitConfig


def _request(socket_ip: str | None, *forwarded: str) -> Request:
    headers = [(b"x-forwarded-for", line.encode()) for line in forwarded]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/authorize",
        "headers": headers,
        "client": (socket_ip, 51234) if socket_ip is not None else None,
    }
    return Request(scope)


def _ip(trusted: list[str], socket_ip: str | None, forwarded: str | None = None) -> str:
    networks = OAuthRateLimitConfig(trusted_proxies=trusted).trusted_networks
    return client_ip(_request(socket_ip, *([forwarded] if forwarded is not None else [])), networks)


@pytest.mark.parametrize(
    ("trusted", "socket_ip", "forwarded", "expected"),
    [
        # A CIDR entry trusts every proxy address inside it.
        (["10.0.0.0/8"], "10.1.2.3", "203.0.113.7", "203.0.113.7"),
        # Host bits in a CIDR are ignored, as ip_network(strict=False) does.
        (["10.9.9.9/8"], "10.1.2.3", "203.0.113.7", "203.0.113.7"),
        # An exact-IP entry keeps working (it is a /32).
        (["172.17.0.1"], "172.17.0.1", "203.0.113.7", "203.0.113.7"),
        # ...and trusts only that address.
        (["172.17.0.1"], "172.17.0.2", "203.0.113.7", "172.17.0.2"),
        # A socket address outside every trusted network ignores XFF.
        (["10.0.0.0/8"], "192.0.2.10", "203.0.113.7", "192.0.2.10"),
        # IPv6 network entries.
        (["fd00::/8"], "fd12:3456::1", "2001:db8::42", "2001:db8::42"),
        (["fd00::/8"], "fe80::1", "2001:db8::42", "fe80::1"),
        # A dual-stack listener reports IPv4 as IPv4-mapped IPv6.
        (["10.0.0.0/8"], "::ffff:10.1.2.3", "203.0.113.7", "203.0.113.7"),
        # An IPv4-mapped config entry is unmapped too.
        (["::ffff:10.0.0.0/104"], "10.1.2.3", "203.0.113.7", "203.0.113.7"),
        # A trusted chain of CIDR proxies: the first untrusted hop from the
        # right is the client; a spoofed leftmost hop is never reached.
        (
            ["10.0.0.0/8", "fd00::/8"],
            "10.0.0.5",
            "198.51.100.1, 203.0.113.7, fd00::9, 10.2.0.1",
            "203.0.113.7",
        ),
        # A trusted mapped hop in the chain is unmapped before matching.
        (["10.0.0.0/8"], "10.0.0.5", "203.0.113.7, ::ffff:10.2.0.1", "203.0.113.7"),
        # Every hop trusted: fall back to the socket address.
        (["10.0.0.0/8"], "10.0.0.5", "10.2.0.1, 10.3.0.1", "10.0.0.5"),
        # An unparsable hop is untrusted and is the client identity as-is.
        (["10.0.0.0/8"], "10.0.0.5", "not-an-ip, 10.2.0.1", "not-an-ip"),
        # No XFF header from a trusted proxy: the socket address.
        (["10.0.0.0/8"], "10.0.0.5", None, "10.0.0.5"),
        # No trusted proxies configured: XFF is ignored.
        ([], "10.0.0.5", "203.0.113.7", "10.0.0.5"),
        # No socket peer at all.
        (["10.0.0.0/8"], None, "203.0.113.7", "unknown"),
    ],
)
def test_client_ip_trusts_proxies_by_network_membership(
    trusted: list[str], socket_ip: str | None, forwarded: str | None, expected: str
) -> None:
    assert _ip(trusted, socket_ip, forwarded) == expected


def test_client_ip_reads_repeated_forwarded_lines_as_one_list() -> None:
    """A client-sent XFF line cannot outrank the line a trusted proxy adds."""
    networks = OAuthRateLimitConfig(trusted_proxies=["10.0.0.0/8"]).trusted_networks
    request = _request("10.0.0.5", "198.51.100.1", "203.0.113.7")
    assert client_ip(request, networks) == "203.0.113.7"
