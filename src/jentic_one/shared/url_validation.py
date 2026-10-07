"""Upstream URL validation — keeps outbound requests off private and metadata targets.

The default policy is strict: every private/loopback range, the cloud-metadata
hosts, and non-HTTP schemes are rejected. A caller may pass an :class:`EgressConfig`
to **opt in** to specific internal targets — a corporate install bridging
to internal/legacy APIs — via CIDR exemptions (``allowed_private_subnets``) and
resolved-domain-suffix exemptions (``allowed_internal_domains``). The cloud-metadata
IPs are a **hard, non-overridable** deny regardless of any allowlist.

Address classification (:func:`assert_ip_allowed`) is shared with the
connection-time DNS-pinning transport (``shared/egress``) so both apply the same
rules. An IPv6 address that embeds an IPv4 address (IPv4-mapped, IPv4-compatible,
NAT64 well-known prefix, 6to4) is classified by the embedded IPv4, and anything
that is not globally routable — plus shared address space (100.64.0.0/10),
multicast, and reserved ranges — is blocked unless an allowlist exempts it.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from jentic_one.shared.config import EgressConfig

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Explicitly-listed ranges. Kept alongside the ``is_global`` test in
# :func:`_is_blocked_address` so the policy never depends solely on the
# interpreter's special-purpose registry (which has shifted between releases).
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # shared address space (RFC 6598)
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking (RFC 2544)
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("240.0.0.0/4"),  # reserved + limited broadcast
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("64:ff9b:1::/48"),  # NAT64 local-use (RFC 8215)
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fec0::/10"),  # deprecated site-local (RFC 3879)
    ipaddress.ip_network("ff00::/8"),  # multicast
]

# IPv6 prefixes whose low 32 bits carry an IPv4 address the packet ultimately
# reaches: the NAT64 well-known prefix (RFC 6052) and the deprecated
# IPv4-compatible form (``::a.b.c.d``). IPv4-mapped (``::ffff:a.b.c.d``) and
# 6to4 (``2002::/16``) are unwrapped via ``ipaddress``'s own accessors.
_LOW32_EMBEDDING_NETWORKS = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("::/96"),
)

_BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.internal",
    }
)

# Cloud instance-metadata and platform-credential endpoints. Never exemptable by
# an allowlist — a covering CIDR (e.g. 169.254.0.0/16 or fd00::/8) must NOT open
# these, so instance or workload credentials can't be allowlisted by accident.
_METADATA_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),  # AWS/GCP/Azure/OCI/DigitalOcean IMDS
        ipaddress.ip_address("fd00:ec2::254"),  # AWS IMDS over IPv6
        ipaddress.ip_address("169.254.170.2"),  # AWS ECS task metadata + credentials
        ipaddress.ip_address("169.254.170.23"),  # AWS EKS Pod Identity agent
        ipaddress.ip_address("fd00:ec2::23"),  # AWS EKS Pod Identity agent over IPv6
        ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud metadata
        ipaddress.ip_address("192.0.0.192"),  # Oracle Cloud (legacy) metadata
        ipaddress.ip_address("168.63.129.16"),  # Azure WireServer / host agent
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

    if _normalise_hostname(hostname) in _BLOCKED_HOSTNAMES:
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


def _normalise_hostname(hostname: str) -> str:
    """Lower-case *hostname* and drop a trailing root dot (``host.`` == ``host``)."""
    return hostname.lower().rstrip(".")


def _strip_scope(addr: _IpAddress) -> _IpAddress:
    """Drop an IPv6 zone id (``fe80::1%eth0``) so set/range checks see the bare address.

    ``IPv6Address`` equality includes the scope id, so ``fd00:ec2::254%eth0`` would
    otherwise miss the metadata hard-deny set while still matching an allowlisted
    covering CIDR (network containment ignores the scope).
    """
    if isinstance(addr, ipaddress.IPv6Address) and addr.scope_id is not None:
        return ipaddress.IPv6Address(int(addr))
    return addr


def embedded_ipv4(addr: _IpAddress) -> ipaddress.IPv4Address | None:
    """Return the IPv4 address embedded in an IPv6 *addr*, or ``None``.

    Covers IPv4-mapped (``::ffff:a.b.c.d``), IPv4-compatible (``::a.b.c.d``),
    the NAT64 well-known prefix (``64:ff9b::/96``), and 6to4 (``2002::/16``) —
    the forms where the connection effectively lands on the embedded IPv4.
    """
    if not isinstance(addr, ipaddress.IPv6Address):
        return None
    if addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    if addr.sixtofour is not None:
        return addr.sixtofour
    if any(addr in network for network in _LOW32_EMBEDDING_NETWORKS):
        return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return None


def _is_blocked_address(addr: _IpAddress) -> bool:
    """Whether *addr* (already unwrapped) is outside the public unicast space."""
    if (
        not addr.is_global
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_private
    ):
        return True
    return any(addr in network for network in _BLOCKED_NETWORKS)


def assert_ip_allowed(
    addr: _IpAddress, egress: EgressConfig | None, *, hostname: str | None
) -> None:
    """Raise ValueError if *addr* is blocked and not exempted by the egress policy.

    The reusable core of the egress check, shared by URL validation and the
    connection-time DNS-pinning guard so the rebind check uses the exact
    same block rules, metadata hard-deny, and allowlist exemptions.

    An IPv6 address embedding an IPv4 address is classified (and matched
    against ``allowed_private_subnets``) by the embedded IPv4. The cloud-metadata
    hard-deny applies to both the literal and the unwrapped address.
    """
    addr = _strip_scope(addr)
    embedded = embedded_ipv4(addr)
    target: _IpAddress = embedded if embedded is not None else addr

    if addr in _METADATA_IPS or target in _METADATA_IPS:
        # Hard deny — never exemptable.
        raise ValueError("upstream URL resolves to a blocked address range")

    if not _is_blocked_address(target):
        return
    if _is_exempted(target, hostname, egress):
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

    lower_hostname = _normalise_hostname(hostname)
    for suffix in egress.allowed_internal_domains:
        bare = _normalise_hostname(suffix).lstrip(".")
        if lower_hostname == bare or lower_hostname.endswith("." + bare):
            return True
    return False


def _resolve_and_check(hostname: str, egress: EgressConfig | None) -> None:
    """Resolve a hostname and validate all returned IPs against the policy.

    A name that does not resolve here is let through: this pre-flight check has
    no connection to protect, and resolution can legitimately differ between the
    validating host and the connecting one. Callers that go on to connect must
    use the DNS-pinning transport (``shared/egress``), which resolves again at
    connect time and fails closed on a name that does not resolve.
    """
    try:
        results = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return

    for _family, _type, _proto, _canonname, sockaddr in results:
        ip_str = sockaddr[0]
        addr = ipaddress.ip_address(ip_str)
        _check_ip(addr, egress, hostname=hostname)
