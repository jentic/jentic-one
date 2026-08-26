"""Unit tests for the per-endpoint IP/CIDR allowlist on the shared egress seam.

Phase 3 adds a per-endpoint ``allowed_cidrs`` list. When **non-empty** it is a
*restrictive* allowlist: the resolved/pinned IP must fall inside one of its CIDRs
or the send is refused — so it both permits an otherwise-private/blocked target
that is listed AND blocks an out-of-range target that is not (even a public one,
e.g. a Slack/relay IP the operator did not list). It is threaded to the send site
as the ``jentic_allowed_cidrs`` request extension and applied to the **pinned** IP
by :class:`DnsPinningTransport` (rebind-proof — the check runs on the address the
connection actually uses). An empty/omitted allowlist imposes no restriction.

These specs pin the properties that make the feature safe:

* an in-range IP is **allowed** (private or public; IPv4 and IPv6);
* an out-of-range IP is **blocked** — including a **public** one, which is the
  security fix (the stored allowlist actually gates the send);
* the cloud-metadata IPs are **always** denied, even when a covering CIDR
  (``169.254.0.0/16`` / ``fd00:ec2::/32``) is in the allowlist;
* an empty allowlist imposes no restriction.

They mirror the style of ``tests/unit/broker/test_dns_pin.py`` (fake
``getaddrinfo``, a recording inner transport, ``match="blocked address range"``).
"""

from __future__ import annotations

import ipaddress
import socket

import httpx
import pytest

from jentic_one.shared.egress import (
    DnsPinningTransport,
    resolve_and_validate,
)
from jentic_one.shared.url_validation import (
    AllowlistBlockedError,
    EgressBlockedError,
    assert_ip_allowed,
    validate_upstream_url,
)

EXT = DnsPinningTransport.EXTRA_CIDRS_EXTENSION


def _fake_getaddrinfo(*ips: str):
    """A getaddrinfo stub yielding the given IPs (ignoring the queried host)."""

    def _inner(host, *_a, **_k):
        out = []
        for ip in ips:
            family = socket.AF_INET6 if ":" in ip else socket.AF_INET
            out.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0)))
        return out

    return _inner


class _RecordingTransport(httpx.AsyncBaseTransport):
    """Inner transport that captures the (pinned) request and returns 204."""

    def __init__(self) -> None:
        self.seen: httpx.Request | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen = request
        return httpx.Response(204)


# --- assert_ip_allowed: the reusable core ------------------------------------


def test_allowlist_permits_in_range_private_ipv4() -> None:
    """A private IPv4 inside a per-endpoint CIDR is exempted (no egress config)."""
    assert_ip_allowed(
        ipaddress.ip_address("10.1.2.3"),
        None,
        hostname=None,
        extra_allowed_cidrs=["10.0.0.0/8"],
    )  # does not raise


def test_allowlist_permits_in_range_private_ipv6() -> None:
    assert_ip_allowed(
        ipaddress.ip_address("fc00::1234"),
        None,
        hostname=None,
        extra_allowed_cidrs=["fc00::/7"],
    )  # does not raise


def test_allowlist_permits_in_range_public_ip() -> None:
    """A public IP that IS in the allowlist is permitted (the happy path)."""
    assert_ip_allowed(
        ipaddress.ip_address("192.0.2.1"),
        None,
        hostname=None,
        extra_allowed_cidrs=["192.0.2.1/32"],
    )  # does not raise


def test_allowlist_blocks_out_of_range_public_ip() -> None:
    """The security fix: a *public* IP outside a non-empty allowlist is blocked.

    This is the exact user symptom — an endpoint allowlisted to ``192.0.2.1/32``
    whose target (Slack/relay) resolves to some other public IP must be refused.
    The old widen-only semantics never consulted the allowlist for a public IP,
    so the send wrongly succeeded.
    """
    with pytest.raises(ValueError, match="blocked address range"):
        assert_ip_allowed(
            ipaddress.ip_address("93.184.216.34"),
            None,
            hostname=None,
            extra_allowed_cidrs=["192.0.2.1/32"],
        )


def test_allowlist_does_not_permit_out_of_range() -> None:
    """An IP outside every allowlisted CIDR stays blocked."""
    with pytest.raises(ValueError, match="blocked address range"):
        assert_ip_allowed(
            ipaddress.ip_address("192.168.5.5"),
            None,
            hostname=None,
            extra_allowed_cidrs=["10.0.0.0/8"],
        )


def test_empty_allowlist_imposes_no_restriction() -> None:
    """An empty allowlist leaves a public IP allowed (unrestricted default)."""
    assert_ip_allowed(
        ipaddress.ip_address("93.184.216.34"),
        None,
        hostname=None,
        extra_allowed_cidrs=[],
    )  # does not raise


@pytest.mark.parametrize(
    ("metadata_ip", "covering_cidr"),
    [
        ("169.254.169.254", "169.254.0.0/16"),
        ("fd00:ec2::254", "fd00:ec2::/32"),
    ],
)
def test_allowlist_never_reopens_metadata(metadata_ip: str, covering_cidr: str) -> None:
    """The metadata IPs are a hard deny — a covering allowlist CIDR can't open them."""
    with pytest.raises(ValueError, match="blocked address range"):
        assert_ip_allowed(
            ipaddress.ip_address(metadata_ip),
            None,
            hostname=None,
            extra_allowed_cidrs=[covering_cidr],
        )


# --- resolve_and_validate: the rebind guard with a per-endpoint allowlist ----


def test_resolve_allows_rebind_into_allowlisted_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """A name resolving into an allowlisted private range is permitted + pinned."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("10.9.9.9"))
    addr = resolve_and_validate("relay.internal.example", None, extra_allowed_cidrs=["10.0.0.0/8"])
    assert str(addr) == "10.9.9.9"


def test_resolve_blocks_rebind_outside_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("172.16.0.9"))
    with pytest.raises(ValueError, match="blocked address range"):
        resolve_and_validate("evil.example", None, extra_allowed_cidrs=["10.0.0.0/8"])


def test_resolve_blocks_public_rebind_outside_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """A name resolving to a *public* IP not in the allowlist is refused."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    with pytest.raises(ValueError, match="blocked address range"):
        resolve_and_validate("slack.example", None, extra_allowed_cidrs=["192.0.2.1/32"])


def test_resolve_blocks_metadata_rebind_even_when_allowlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254"))
    with pytest.raises(ValueError, match="blocked address range"):
        resolve_and_validate("sneaky.example", None, extra_allowed_cidrs=["169.254.0.0/16"])


# --- DnsPinningTransport: enforcement on the pinned IP, per-request ----------


@pytest.mark.asyncio
async def test_transport_allows_ip_literal_in_endpoint_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-request allowlist (the endpoint's ``allowed_cidrs``) exempts a literal."""

    def _no_resolve(*_a, **_k):
        raise AssertionError("getaddrinfo must not be called for an IP literal")

    monkeypatch.setattr(socket, "getaddrinfo", _no_resolve)
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, None)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("http://10.1.2.3/hook", extensions={EXT: ["10.0.0.0/8"]})
    assert inner.seen is not None
    assert inner.seen.url.host == "10.1.2.3"


@pytest.mark.asyncio
async def test_transport_blocks_ip_literal_outside_endpoint_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _no_resolve(*_a, **_k):
        raise AssertionError("getaddrinfo must not be called for an IP literal")

    monkeypatch.setattr(socket, "getaddrinfo", _no_resolve)
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, None)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="blocked address range"):
            await client.get("http://192.168.0.9/hook", extensions={EXT: ["10.0.0.0/8"]})
    assert inner.seen is None


@pytest.mark.asyncio
async def test_transport_pins_dns_name_into_allowlisted_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The allowlist is applied to the resolved (pinned) IP, not the name."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("10.5.5.5"))
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, None)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://relay.internal.example/hook", extensions={EXT: ["10.0.0.0/8"]})
    assert inner.seen is not None
    # Pinned to the resolved IP; Host + SNI keep the real name.
    assert inner.seen.url.host == "10.5.5.5"
    assert inner.seen.headers["host"] == "relay.internal.example"


@pytest.mark.asyncio
async def test_transport_blocks_dns_name_resolving_outside_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DNS name pinning to a *public* IP outside the allowlist is refused.

    Mirrors the user's symptom at the transport layer: the endpoint allowlists
    ``192.0.2.1/32`` but the target resolves to a different public IP, so the
    pinned connection must be blocked before it reaches the inner transport.
    """
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, None)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="blocked address range"):
            await client.get("https://slack.example/hook", extensions={EXT: ["192.0.2.1/32"]})
    assert inner.seen is None


@pytest.mark.asyncio
async def test_transport_never_reaches_inner_for_metadata_even_allowlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with a covering CIDR, a metadata literal is refused before the send."""

    def _no_resolve(*_a, **_k):
        raise AssertionError("getaddrinfo must not be called for an IP literal")

    monkeypatch.setattr(socket, "getaddrinfo", _no_resolve)
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, None)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="blocked address range"):
            await client.get(
                "http://169.254.169.254/latest/meta-data/",
                extensions={EXT: ["169.254.0.0/16"]},
            )
    assert inner.seen is None


# --- config-time validator honours the same allowlist ------------------------


def test_validate_upstream_url_accepts_allowlisted_literal() -> None:
    """The pre-request validator also honours the per-endpoint allowlist."""
    url = validate_upstream_url("http://10.1.2.3/hook", None, extra_allowed_cidrs=["10.0.0.0/8"])
    assert url == "http://10.1.2.3/hook"


def test_validate_upstream_url_still_blocks_metadata_literal_when_allowlisted() -> None:
    with pytest.raises(ValueError, match="blocked address range"):
        validate_upstream_url(
            "http://169.254.169.254/", None, extra_allowed_cidrs=["169.254.0.0/16"]
        )


# --- multi-IP answers: allowlist gates the PINNED IP, egress gates ALL --------
#
# Regression for the over-blocking bug: a host resolving to several IPs (e.g.
# ngrok's rotating pool) where only *some* fall inside the endpoint's allowlist.
# The connection is pinned to the FIRST address, so the restrictive allowlist
# must gate only that pinned IP — a sibling record outside the allowlist must NOT
# veto the delivery. The egress hard-deny still applies to EVERY address so the
# anti-rebind guarantee is unweakened.


def test_resolve_multi_ip_pinned_in_allowlist_sibling_outside_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug: first/pinned IP is in the allowlist, a public sibling is not → allowed.

    Mirrors ngrok returning e.g. 3.124.x (inside ``3.120.0.0/13``) first plus
    18.158.x/18.192.x siblings a narrow allowlist doesn't cover. Only the pinned
    address is gated, so the delivery proceeds and pins to the first record.
    """
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_getaddrinfo("3.124.1.1", "18.158.249.75", "18.192.31.165")
    )
    addr = resolve_and_validate("x.ngrok-free.dev", None, extra_allowed_cidrs=["3.120.0.0/13"])
    assert str(addr) == "3.124.1.1"


def test_resolve_multi_ip_pinned_outside_allowlist_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inverse: the pinned (first) IP is OUTSIDE the allowlist → AllowlistBlockedError.

    Even though a *sibling* record is inside the allowlist, the connection would
    pin to the first (out-of-range) address, so the restrictive allowlist must
    refuse it — and as the endpoint-specific ``AllowlistBlockedError`` so the UI
    attributes it to the operator's allowlist.
    """
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("18.158.249.75", "3.124.1.1"))
    with pytest.raises(AllowlistBlockedError, match="blocked address range"):
        resolve_and_validate("x.ngrok-free.dev", None, extra_allowed_cidrs=["3.120.0.0/13"])


def test_resolve_multi_ip_any_metadata_sibling_still_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anti-rebind preserved: a metadata sibling is hard-denied regardless of allowlist.

    The pinned (first) address is inside the allowlist, yet a metadata IP among
    the siblings must still block the whole delivery with the generic
    ``EgressBlockedError`` (never ``AllowlistBlockedError``) — the hard-deny runs
    on EVERY address and is never re-opened.
    """
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("3.124.1.1", "169.254.169.254"))
    with pytest.raises(EgressBlockedError, match="blocked address range") as exc_info:
        resolve_and_validate(
            "sneaky.ngrok-free.dev", None, extra_allowed_cidrs=["3.120.0.0/13", "169.254.0.0/16"]
        )
    # A metadata refusal is a policy hard-deny, NOT an allowlist attribution.
    assert not isinstance(exc_info.value, AllowlistBlockedError)


def test_resolve_multi_ip_any_private_sibling_still_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anti-rebind preserved: a private sibling outside every allowlist is hard-denied.

    The pinned public address is allowlisted, but a private sibling that is NOT
    covered by the allowlist must still block the delivery (``EgressBlockedError``),
    proving the egress block still validates every resolved address.
    """
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("3.124.1.1", "10.0.0.5"))
    with pytest.raises(EgressBlockedError, match="blocked address range") as exc_info:
        resolve_and_validate("mixed.ngrok-free.dev", None, extra_allowed_cidrs=["3.120.0.0/13"])
    assert not isinstance(exc_info.value, AllowlistBlockedError)


@pytest.mark.asyncio
async def test_transport_multi_ip_pins_first_when_sibling_outside_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end at the transport: multi-IP answer pins the allowlisted first IP."""
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_getaddrinfo("3.124.1.1", "18.158.249.75", "18.192.31.165")
    )
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, None)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://x.ngrok-free.dev/hook", extensions={EXT: ["3.120.0.0/13"]})
    assert inner.seen is not None
    assert inner.seen.url.host == "3.124.1.1"
    assert inner.seen.headers["host"] == "x.ngrok-free.dev"


# --- pre-check (validate_upstream_url) and connect-time (resolve) agree -------


def test_precheck_and_connect_agree_multi_ip_pinned_in_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-request validator agrees with connect-time on a multi-IP answer.

    Same rotating-pool answer: the pre-check must PASS (pinned/first IP allowed,
    sibling ignored) exactly as ``resolve_and_validate`` does — no pre-check pass
    + connect-time block or vice-versa.
    """
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_getaddrinfo("3.124.1.1", "18.158.249.75", "18.192.31.165")
    )
    # Pre-check: does not raise.
    url = validate_upstream_url(
        "https://x.ngrok-free.dev/hook", None, extra_allowed_cidrs=["3.120.0.0/13"]
    )
    assert url == "https://x.ngrok-free.dev/hook"
    # Connect-time: pins the same first IP.
    addr = resolve_and_validate("x.ngrok-free.dev", None, extra_allowed_cidrs=["3.120.0.0/13"])
    assert str(addr) == "3.124.1.1"


def test_precheck_and_connect_agree_multi_ip_pinned_outside_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both paths reject when the pinned (first) IP is outside the allowlist."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("18.158.249.75", "3.124.1.1"))
    with pytest.raises(AllowlistBlockedError, match="blocked address range"):
        validate_upstream_url(
            "https://x.ngrok-free.dev/hook", None, extra_allowed_cidrs=["3.120.0.0/13"]
        )
    with pytest.raises(AllowlistBlockedError, match="blocked address range"):
        resolve_and_validate("x.ngrok-free.dev", None, extra_allowed_cidrs=["3.120.0.0/13"])


def test_precheck_and_connect_agree_multi_ip_metadata_sibling_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both paths hard-deny a metadata sibling regardless of the allowlist."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("3.124.1.1", "169.254.169.254"))
    with pytest.raises(EgressBlockedError, match="blocked address range"):
        validate_upstream_url(
            "https://sneaky.ngrok-free.dev/hook",
            None,
            extra_allowed_cidrs=["3.120.0.0/13", "169.254.0.0/16"],
        )
    with pytest.raises(EgressBlockedError, match="blocked address range"):
        resolve_and_validate(
            "sneaky.ngrok-free.dev", None, extra_allowed_cidrs=["3.120.0.0/13", "169.254.0.0/16"]
        )
