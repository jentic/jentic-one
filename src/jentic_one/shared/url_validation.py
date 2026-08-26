"""Upstream URL validation — guards against SSRF attacks.

The default policy is strict: every private/loopback range, the cloud-metadata
hosts, and non-HTTP schemes are rejected. A caller may pass an :class:`EgressConfig`
(§08 E2) to **opt in** to specific internal targets — a corporate install bridging
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


class EgressBlockedError(ValueError):
    """A target address was refused by the egress/SSRF policy.

    Subclasses ``ValueError`` so every existing ``except ValueError`` /
    ``pytest.raises(ValueError, match="blocked address range")`` caller keeps
    working unchanged — the message is deliberately identical to the historical
    string. Its purpose is purely to let a *categoriser* tell egress refusals
    apart from other value errors **without parsing the message** (which could
    embed the resolved IP). The message itself never carries the offending IP.
    """


class AllowlistBlockedError(EgressBlockedError):
    """A resolved/pinned IP fell outside a non-empty per-endpoint allowlist.

    A more specific ``EgressBlockedError``: the address was not blocked by the
    operator-wide egress policy (private/loopback/metadata) but by the
    *endpoint's own* ``allowed_cidrs`` restriction. Distinguishing it lets the UI
    say "Blocked by IP allowlist" (a config the operator controls) rather than
    the generic egress-policy message — the motivating bug. Carries no IP.
    """


class DnsResolutionError(ValueError):
    """A hostname could not be resolved to any address.

    Subclasses ``ValueError`` for backwards compatibility; the host is
    user-supplied so it *may* appear in the message, but the categoriser maps
    this to a fixed label and never persists the message.
    """


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


def validate_upstream_url(
    raw_url: str,
    egress: EgressConfig | None = None,
    *,
    extra_allowed_cidrs: list[str] | None = None,
) -> str:
    """Validate and normalise an upstream URL, raising ValueError on unsafe targets.

    Rejects private/loopback IP literals, cloud metadata hostnames, non-HTTP schemes,
    and hostnames that resolve to blocked IP ranges. When *egress* is provided, a
    private IP inside an ``allowed_private_subnets`` CIDR (and, for resolved hosts,
    matching an ``allowed_internal_domains`` suffix) is permitted — except the
    cloud-metadata IPs, which stay a hard deny. A non-empty ``extra_allowed_cidrs``
    (Phase 3 per-endpoint allowlist) is *restrictive*: the address must fall inside
    one of those CIDRs or the URL is rejected — again, never re-opening the metadata
    IPs. Returns the normalised URL.
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
        _resolve_and_check(hostname, egress, extra_allowed_cidrs=extra_allowed_cidrs)
    else:
        # An IP literal: no domain-suffix exemption (there's no name to match).
        _check_ip(addr, egress, hostname=None, extra_allowed_cidrs=extra_allowed_cidrs)

    return url


def assert_ip_allowed(
    addr: _IpAddress,
    egress: EgressConfig | None,
    *,
    hostname: str | None,
    extra_allowed_cidrs: list[str] | None = None,
    enforce_allowlist: bool = True,
) -> None:
    """Raise ValueError if *addr* is blocked or falls outside a required allowlist.

    The reusable core of the SSRF check, shared by URL validation and the
    connection-time DNS-pinning guard (§08 E2) so the rebind check uses the exact
    same block rules, metadata hard-deny, and allowlist semantics.

    ``extra_allowed_cidrs`` is a **per-target restrictive allowlist** (Phase 3
    per-endpoint ``allowed_cidrs``). When it is **non-empty** the address *must*
    fall inside one of its CIDRs or delivery is refused — this restricts even an
    otherwise-public address (e.g. a webhook whose target must only ever resolve
    to one pinned IP), which is the whole point of the per-endpoint allowlist. An
    **empty/omitted** list imposes no restriction, so the operator-wide egress
    policy alone applies (the broker's fetch path passes no allowlist and is
    unaffected). The cloud-metadata hard-deny is checked *first* and is **never**
    re-opened, no matter how wide an allowlisted CIDR is.

    ``enforce_allowlist`` scopes only the *restrictive* half of the allowlist. A
    DNS name can resolve to **several** IPs while the connection is pinned to just
    one, so the two checks have different correct scopes over a multi-record
    answer: the SSRF hard-deny (metadata + private/loopback/link-local) must be
    enforced against **every** resolved address (the anti-rebind guarantee), but
    the operator's positive allowlist should gate only the **pinned** IP the
    connection actually uses — a sibling record that is never connected to must
    not veto the delivery. Callers therefore validate every address with
    ``enforce_allowlist=False`` (egress hard-deny only, allowlist still *widening*
    a listed private range) and re-check the single pinned address with the
    default ``True`` to apply the restrictive gate. For a single address (an IP
    literal, or a name with one record) both scopes coincide, so the default
    ``True`` is exactly the historical behaviour.
    """
    if addr in _METADATA_IPS:
        # Hard deny — never exemptable, not even by a per-endpoint allowlist.
        raise EgressBlockedError("upstream URL resolves to a blocked address range")

    # A non-empty per-endpoint allowlist is *restrictive*: the pinned IP must be
    # inside it (metadata excepted above), so an out-of-range address is refused
    # whether it is public or private. This is what makes storing ``allowed_cidrs``
    # actually gate the send. It is scoped to the pinned address (``enforce_allowlist``)
    # so an unconnected sibling record does not falsely block the delivery, while
    # the egress hard-deny below still runs on *every* address. Raised as the more
    # specific ``AllowlistBlockedError`` so a categoriser can attribute the refusal
    # to the *endpoint's own* allowlist (operator-fixable) rather than the egress
    # policy.
    if enforce_allowlist and extra_allowed_cidrs and not _in_any_cidr(addr, extra_allowed_cidrs):
        raise AllowlistBlockedError("upstream URL resolves to a blocked address range")

    for network in _BLOCKED_NETWORKS:
        if addr not in network:
            continue
        if _is_exempted(addr, hostname, egress, extra_allowed_cidrs):
            return
        raise EgressBlockedError("upstream URL resolves to a blocked address range")


def _check_ip(
    addr: _IpAddress,
    egress: EgressConfig | None,
    *,
    hostname: str | None,
    extra_allowed_cidrs: list[str] | None = None,
    enforce_allowlist: bool = True,
) -> None:
    assert_ip_allowed(
        addr,
        egress,
        hostname=hostname,
        extra_allowed_cidrs=extra_allowed_cidrs,
        enforce_allowlist=enforce_allowlist,
    )


def _in_any_cidr(addr: _IpAddress, cidrs: list[str] | None) -> bool:
    """Whether *addr* falls inside any CIDR in *cidrs* (ignoring unparseable ones)."""
    if not cidrs:
        return False
    for cidr in cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if addr.version == network.version and addr in network:
            return True
    return False


def _is_exempted(
    addr: _IpAddress,
    hostname: str | None,
    egress: EgressConfig | None,
    extra_allowed_cidrs: list[str] | None = None,
) -> bool:
    """Whether a private *addr* is opted-in via the egress allowlists.

    A blocked (private/loopback) address is exempted when it falls inside a
    per-target ``extra_allowed_cidrs`` entry (the Phase 3 per-endpoint allowlist),
    OR inside an operator-wide ``allowed_private_subnets`` CIDR. The per-endpoint
    list needs no domain-suffix match — it is an explicit, endpoint-scoped opt-in
    to a specific stable/internal IP range (``assert_ip_allowed`` has already
    enforced that a non-empty per-endpoint allowlist *contains* the address before
    reaching here). For the operator-wide subnet path, a DNS name (``hostname``
    set) must *also* match an ``allowed_internal_domains`` suffix; an IP literal
    has no name to match, so the subnet exemption alone applies.
    """
    if _in_any_cidr(addr, extra_allowed_cidrs):
        return True

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


def _resolve_and_check(
    hostname: str, egress: EgressConfig | None, *, extra_allowed_cidrs: list[str] | None = None
) -> None:
    """Resolve a hostname and validate its addresses against the policy.

    The egress hard-deny (metadata + private/loopback/link-local) is enforced on
    **every** returned address so a multi-record answer can't smuggle a blocked
    IP past the check (the anti-rebind guarantee). The *restrictive* per-endpoint
    allowlist, however, gates only the address the connection will actually pin to
    — the first record, matching ``resolve_and_validate`` — so a sibling record
    outside the allowlist does not falsely reject a URL whose pinned IP is allowed.
    """
    try:
        results = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return

    addrs = [ipaddress.ip_address(sockaddr[0]) for *_rest, sockaddr in results]
    if not addrs:
        return

    for addr in addrs:
        # Egress hard-deny on every address (allowlist may still *widen* a listed
        # private range here, but does not *restrict* — that is scoped to the pin).
        _check_ip(
            addr,
            egress,
            hostname=hostname,
            extra_allowed_cidrs=extra_allowed_cidrs,
            enforce_allowlist=False,
        )
    # The restrictive allowlist gates only the pinned (first) address, mirroring
    # the connect-time pin in ``resolve_and_validate`` so pre-check and connect
    # agree for the same multi-IP answer.
    _check_ip(
        addrs[0],
        egress,
        hostname=hostname,
        extra_allowed_cidrs=extra_allowed_cidrs,
        enforce_allowlist=True,
    )
