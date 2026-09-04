"""Upstream URL validation — guards against SSRF attacks.

The default policy is strict: every private/loopback range, the cloud-metadata
hosts, and non-HTTP schemes are rejected. A caller may pass an :class:`EgressConfig`
to **opt in** to specific internal targets — a corporate install bridging
to internal/legacy APIs — via CIDR exemptions (``allowed_private_subnets``) and
resolved-domain-suffix exemptions (``allowed_internal_domains``). The cloud-metadata
IPs are a **hard, non-overridable** deny regardless of any allowlist.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from jentic_one.shared.config import EgressConfig

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

_BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.internal",
    }
)

# Cloud instance-metadata service IPs. Never exemptable by an allowlist — a
# covering CIDR (e.g. 169.254.0.0/16) must NOT open these, or a credential-
# stealing SSRF could be allowlisted by accident.
_METADATA_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),  # AWS/GCP/Azure IMDS
        ipaddress.ip_address("fd00:ec2::254"),  # AWS IMDS over IPv6
    }
)


def validate_upstream_url(raw_url: str, egress: EgressConfig | None = None) -> str:
    """Validate and normalise an upstream URL, raising ValueError on unsafe targets.

    Rejects private/loopback IP literals, cloud metadata hostnames, non-HTTP schemes,
    and hostnames that resolve to blocked IP ranges. When *egress* is provided, a
    private IP inside an ``allowed_private_subnets`` CIDR (and, for resolved hosts,
    matching an ``allowed_internal_domains`` suffix) is permitted — except the
    cloud-metadata IPs, which stay a hard deny. Returns the normalised URL.
    """
    if not raw_url or not raw_url.strip():
        raise ValueError("upstream URL is required")

    url = raw_url.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parsed.scheme}")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("upstream URL has no hostname")

    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise ValueError("upstream URL targets a blocked hostname")

    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError as exc:
        if "does not appear to be" not in str(exc):
            raise
        # A DNS name: resolve and validate every returned address. The host-suffix
        # exemption only applies when the name resolves into an allowed subnet.
        _resolve_and_check(hostname, egress)
    else:
        # An IP literal: no domain-suffix exemption (there's no name to match).
        _check_ip(addr, egress, hostname=None)

    return url


def assert_ip_allowed(
    addr: _IpAddress, egress: EgressConfig | None, *, hostname: str | None
) -> None:
    """Raise ValueError if *addr* is blocked and not exempted by the egress policy.

    The reusable core of the SSRF check, shared by URL validation and the
    connection-time DNS-pinning guard so the rebind check uses the exact
    same block rules, metadata hard-deny, and allowlist exemptions.
    """
    if addr in _METADATA_IPS:
        # Hard deny — never exemptable.
        raise ValueError("upstream URL resolves to a blocked address range")

    for network in _BLOCKED_NETWORKS:
        if addr not in network:
            continue
        if _is_exempted(addr, hostname, egress):
            return
        raise ValueError("upstream URL resolves to a blocked address range")


def _check_ip(addr: _IpAddress, egress: EgressConfig | None, *, hostname: str | None) -> None:
    assert_ip_allowed(addr, egress, hostname=hostname)


def _is_exempted(addr: _IpAddress, hostname: str | None, egress: EgressConfig | None) -> bool:
    """Whether a private *addr* is opted-in via the egress allowlists.

    A blocked address is exempted only when it falls inside an
    ``allowed_private_subnets`` CIDR. If the target is a DNS name (``hostname``
    set), the name must *also* match an ``allowed_internal_domains`` suffix — an
    IP literal has no name to match, so the subnet exemption alone applies.
    """
    if egress is None:
        return False

    in_allowed_subnet = any(
        addr in ipaddress.ip_network(cidr, strict=False) for cidr in egress.allowed_private_subnets
    )
    if not in_allowed_subnet:
        return False

    if hostname is None:
        return True

    lower_hostname = hostname.lower()
    return any(
        lower_hostname == suffix.lower().lstrip(".")
        or lower_hostname.endswith("." + suffix.lower().lstrip("."))
        for suffix in egress.allowed_internal_domains
    )


def _resolve_and_check(hostname: str, egress: EgressConfig | None) -> None:
    """Resolve a hostname and validate all returned IPs against the policy."""
    try:
        results = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return

    for _family, _type, _proto, _canonname, sockaddr in results:
        ip_str = sockaddr[0]
        addr = ipaddress.ip_address(ip_str)
        _check_ip(addr, egress, hostname=hostname)
