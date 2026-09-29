"""Unit tests for upstream URL validation."""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from jentic_one.shared.config import EgressConfig
from jentic_one.shared.url_validation import validate_upstream_url


def test_valid_https_url() -> None:
    assert validate_upstream_url("https://api.example.com/v1") == "https://api.example.com/v1"


def test_valid_http_url() -> None:
    assert validate_upstream_url("http://api.example.com/v1") == "http://api.example.com/v1"


def test_adds_https_scheme() -> None:
    assert validate_upstream_url("api.example.com/v1") == "https://api.example.com/v1"


def test_rejects_empty_string() -> None:
    with pytest.raises(ValueError, match="upstream URL is required"):
        validate_upstream_url("")


def test_rejects_whitespace_only() -> None:
    with pytest.raises(ValueError, match="upstream URL is required"):
        validate_upstream_url("   ")


def test_rejects_loopback_ip() -> None:
    with pytest.raises(ValueError, match="blocked address range"):
        validate_upstream_url("http://127.0.0.1/foo")


def test_rejects_private_10_network() -> None:
    with pytest.raises(ValueError, match="blocked address range"):
        validate_upstream_url("http://10.0.0.1/foo")


def test_rejects_private_172_network() -> None:
    with pytest.raises(ValueError, match="blocked address range"):
        validate_upstream_url("http://172.16.0.1/foo")


def test_rejects_private_192_network() -> None:
    with pytest.raises(ValueError, match="blocked address range"):
        validate_upstream_url("http://192.168.1.1/foo")


def test_rejects_link_local() -> None:
    with pytest.raises(ValueError, match="blocked address range"):
        validate_upstream_url("http://169.254.169.254/latest/meta-data/")


def test_rejects_metadata_hostname() -> None:
    with pytest.raises(ValueError, match="blocked hostname"):
        validate_upstream_url("http://metadata.google.internal/computeMetadata/v1/")


def test_rejects_ipv6_loopback() -> None:
    with pytest.raises(ValueError, match="blocked address range"):
        validate_upstream_url("http://[::1]/foo")


def test_rejects_no_hostname() -> None:
    with pytest.raises(ValueError, match="no hostname"):
        validate_upstream_url("http:///path")


def test_rejects_zero_network() -> None:
    with pytest.raises(ValueError, match="blocked address range"):
        validate_upstream_url("http://0.0.0.1/foo")


def test_hostname_resolving_to_private_ip_blocked() -> None:
    fake_result = [(2, 1, 6, "", ("169.254.169.254", 0))]
    with (
        patch("jentic_one.shared.url_validation.socket.getaddrinfo", return_value=fake_result),
        pytest.raises(ValueError, match="blocked address range"),
    ):
        validate_upstream_url("http://evil.attacker.com/metadata")


def test_hostname_resolving_to_public_ip_allowed() -> None:
    fake_result = [(2, 1, 6, "", ("93.184.216.34", 0))]
    with patch("jentic_one.shared.url_validation.socket.getaddrinfo", return_value=fake_result):
        result = validate_upstream_url("http://example.com/api")
        assert result == "http://example.com/api"


def test_dns_resolution_failure_allows_url() -> None:
    with patch(
        "jentic_one.shared.url_validation.socket.getaddrinfo",
        side_effect=socket.gaierror("Name resolution failed"),
    ):
        result = validate_upstream_url("http://nonexistent.example.com/api")
        assert result == "http://nonexistent.example.com/api"


# --- §08 E2: configurable internal-egress allowlist -------------------------


def test_default_egress_is_strict() -> None:
    # An empty (default) EgressConfig must behave exactly like no policy: strict.
    with pytest.raises(ValueError, match="blocked address range"):
        validate_upstream_url("http://10.50.2.10/api", EgressConfig())


def test_allowlisted_subnet_permits_ip_literal() -> None:
    egress = EgressConfig(allowed_private_subnets=["10.50.0.0/16"])
    assert validate_upstream_url("http://10.50.2.10/api", egress) == "http://10.50.2.10/api"


def test_private_ip_outside_allowlist_still_blocked() -> None:
    egress = EgressConfig(allowed_private_subnets=["10.50.0.0/16"])
    with pytest.raises(ValueError, match="blocked address range"):
        validate_upstream_url("http://10.60.0.1/api", egress)


def test_metadata_ip_blocked_even_when_range_allowlisted() -> None:
    # The covering /16 is allowlisted, but the IMDS IP is a hard, non-overridable deny.
    egress = EgressConfig(allowed_private_subnets=["169.254.0.0/16"])
    with pytest.raises(ValueError, match="blocked address range"):
        validate_upstream_url("http://169.254.169.254/latest/meta-data/", egress)


def test_ipv6_metadata_ip_blocked_even_when_range_allowlisted() -> None:
    egress = EgressConfig(allowed_private_subnets=["fc00::/7"])
    with pytest.raises(ValueError, match="blocked address range"):
        validate_upstream_url("http://[fd00:ec2::254]/latest/meta-data/", egress)


def test_internal_domain_resolving_into_allowed_subnet_permitted() -> None:
    egress = EgressConfig(
        allowed_private_subnets=["10.50.0.0/16"],
        allowed_internal_domains=[".svc.cluster.local"],
    )
    fake_result = [(2, 1, 6, "", ("10.50.2.10", 0))]
    with patch("jentic_one.shared.url_validation.socket.getaddrinfo", return_value=fake_result):
        result = validate_upstream_url("http://billing.svc.cluster.local/api", egress)
        assert result == "http://billing.svc.cluster.local/api"


def test_internal_domain_not_in_allowlist_blocked() -> None:
    # Resolves into the allowed subnet, but the host suffix isn't allowlisted.
    egress = EgressConfig(allowed_private_subnets=["10.50.0.0/16"])
    fake_result = [(2, 1, 6, "", ("10.50.2.10", 0))]
    with (
        patch("jentic_one.shared.url_validation.socket.getaddrinfo", return_value=fake_result),
        pytest.raises(ValueError, match="blocked address range"),
    ):
        validate_upstream_url("http://billing.svc.cluster.local/api", egress)


def test_allowed_domain_resolving_outside_allowed_subnet_blocked() -> None:
    # Host suffix is allowlisted, but it resolves to a private IP outside the
    # allowed subnet — still blocked (both checks must pass).
    egress = EgressConfig(
        allowed_private_subnets=["10.50.0.0/16"],
        allowed_internal_domains=[".svc.cluster.local"],
    )
    fake_result = [(2, 1, 6, "", ("192.168.1.5", 0))]
    with (
        patch("jentic_one.shared.url_validation.socket.getaddrinfo", return_value=fake_result),
        pytest.raises(ValueError, match="blocked address range"),
    ):
        validate_upstream_url("http://billing.svc.cluster.local/api", egress)


def test_domain_suffix_match_requires_dot_boundary() -> None:
    # "malinternal.corp" must NOT be matched by suffix "internal.corp".
    egress = EgressConfig(
        allowed_private_subnets=["10.50.0.0/16"],
        allowed_internal_domains=["internal.corp"],
    )
    fake_result = [(2, 1, 6, "", ("10.50.2.10", 0))]
    with (
        patch("jentic_one.shared.url_validation.socket.getaddrinfo", return_value=fake_result),
        pytest.raises(ValueError, match="blocked address range"),
    ):
        validate_upstream_url("http://malinternal.corp/api", egress)


def test_domain_exact_match_without_leading_dot() -> None:
    # Exact match on the bare domain works.
    egress = EgressConfig(
        allowed_private_subnets=["10.50.0.0/16"],
        allowed_internal_domains=["internal.corp"],
    )
    fake_result = [(2, 1, 6, "", ("10.50.2.10", 0))]
    with patch("jentic_one.shared.url_validation.socket.getaddrinfo", return_value=fake_result):
        result = validate_upstream_url("http://internal.corp/api", egress)
        assert result == "http://internal.corp/api"


def test_domain_subdomain_match_without_leading_dot() -> None:
    # "foo.internal.corp" should be matched by suffix "internal.corp".
    egress = EgressConfig(
        allowed_private_subnets=["10.50.0.0/16"],
        allowed_internal_domains=["internal.corp"],
    )
    fake_result = [(2, 1, 6, "", ("10.50.2.10", 0))]
    with patch("jentic_one.shared.url_validation.socket.getaddrinfo", return_value=fake_result):
        result = validate_upstream_url("http://foo.internal.corp/api", egress)
        assert result == "http://foo.internal.corp/api"


def test_invalid_cidr_in_config_rejected() -> None:
    with pytest.raises(ValueError, match="invalid CIDR"):
        EgressConfig(allowed_private_subnets=["not-a-cidr"])


# --- address classification: embedded IPv4 and non-global ranges ------------

#: Addresses outside public unicast space, including IPv6 forms that embed a
#: blocked IPv4 address. Every one must be refused under the default policy,
#: whether it arrives as a URL literal or as a resolved address.
_NON_PUBLIC_ADDRESSES = [
    # IPv4-mapped IPv6
    "::ffff:127.0.0.1",
    "::ffff:10.0.0.1",
    "::ffff:169.254.169.254",
    # IPv4-compatible IPv6 (deprecated) and the unspecified address
    "::127.0.0.1",
    "::a9fe:a9fe",
    "::",
    # NAT64 well-known prefix and local-use prefix
    "64:ff9b::7f00:1",
    "64:ff9b::a9fe:a9fe",
    "64:ff9b::a00:1",
    "64:ff9b:1::a00:1",
    # 6to4
    "2002:7f00:1::",
    "2002:a9fe:a9fe::1",
    "2002:c0a8:101::",
    # IPv4 non-global ranges
    "0.0.0.0",
    "100.64.0.1",
    "100.127.255.254",
    "198.18.0.1",
    "198.19.255.254",
    "192.0.0.1",
    "192.0.2.1",
    "224.0.0.1",
    "239.255.255.250",
    "240.0.0.1",
    "255.255.255.255",
    # IPv6 non-global ranges
    "::1",
    "fe80::1",
    "fd12:3456::1",
    "ff02::1",
    "ff0e::1",
    "2001:db8::1",
]

#: Public unicast addresses, including embedded forms of a public IPv4.
_PUBLIC_ADDRESSES = [
    "93.184.216.34",
    "8.8.8.8",
    "1.1.1.1",
    "2606:4700:4700::1111",
    "::ffff:93.184.216.34",
    "64:ff9b::5db8:d822",
    "2002:5db8:d822::1",
]


def _url_for(ip: str) -> str:
    return f"http://[{ip}]/x" if ":" in ip else f"http://{ip}/x"


def _resolving_to(ip: str) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]


@pytest.mark.parametrize("ip", _NON_PUBLIC_ADDRESSES)
def test_non_public_ip_literal_blocked(ip: str) -> None:
    with pytest.raises(ValueError, match="blocked address range"):
        validate_upstream_url(_url_for(ip))


@pytest.mark.parametrize("ip", _NON_PUBLIC_ADDRESSES)
def test_hostname_resolving_to_non_public_ip_blocked(ip: str) -> None:
    with (
        patch(
            "jentic_one.shared.url_validation.socket.getaddrinfo",
            return_value=_resolving_to(ip),
        ),
        pytest.raises(ValueError, match="blocked address range"),
    ):
        validate_upstream_url("https://api.example.com/v1")


@pytest.mark.parametrize("ip", _PUBLIC_ADDRESSES)
def test_public_ip_literal_allowed(ip: str) -> None:
    assert validate_upstream_url(_url_for(ip)) == _url_for(ip)


@pytest.mark.parametrize("ip", _PUBLIC_ADDRESSES)
def test_hostname_resolving_to_public_ip_forms_allowed(ip: str) -> None:
    with patch(
        "jentic_one.shared.url_validation.socket.getaddrinfo",
        return_value=_resolving_to(ip),
    ):
        assert validate_upstream_url("https://api.example.com/v1") == "https://api.example.com/v1"


@pytest.mark.parametrize(
    "ip",
    ["::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe", "2002:a9fe:a9fe::", "::a9fe:a9fe"],
)
def test_embedded_metadata_ip_blocked_even_when_range_allowlisted(ip: str) -> None:
    # The metadata hard-deny applies to the unwrapped IPv4, not just the literal.
    egress = EgressConfig(allowed_private_subnets=["169.254.0.0/16", "::/0"])
    with pytest.raises(ValueError, match="blocked address range"):
        validate_upstream_url(_url_for(ip), egress)


@pytest.mark.parametrize(
    ("subnet", "ip"),
    [
        ("10.50.0.0/16", "10.50.2.10"),
        # An IPv4-mapped form is matched against the allowlist by its IPv4.
        ("10.50.0.0/16", "::ffff:10.50.2.10"),
        ("100.64.0.0/10", "100.100.1.1"),
        ("127.0.0.0/8", "127.0.0.1"),
        ("fd12:3456::/32", "fd12:3456::1"),
    ],
)
def test_allowlisted_subnet_still_permits_configured_ranges(subnet: str, ip: str) -> None:
    egress = EgressConfig(allowed_private_subnets=[subnet])
    assert validate_upstream_url(_url_for(ip), egress) == _url_for(ip)


def test_embedded_ipv4_outside_allowlist_blocked() -> None:
    # The allowlist covers 10.50/16; a mapped address embedding 10.60.x is not covered.
    egress = EgressConfig(allowed_private_subnets=["10.50.0.0/16"])
    with pytest.raises(ValueError, match="blocked address range"):
        validate_upstream_url(_url_for("::ffff:10.60.0.1"), egress)
