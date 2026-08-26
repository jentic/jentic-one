"""Unit tests for the DNS-rebinding guard + connection pin (§08 E2)."""

from __future__ import annotations

import socket

import httpx
import pytest

from jentic_one.broker.adapters.egress import DnsPinningTransport, resolve_and_validate
from jentic_one.broker.adapters.http_client import build_client
from jentic_one.shared.config import EgressConfig, UpstreamClientConfig


def _fake_getaddrinfo(*ips: str):
    """Return a getaddrinfo stub yielding the given IPs (ignoring the queried host)."""

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


# --- resolve_and_validate (the rebind guard) ----------------------------------


def test_resolve_returns_public_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    addr = resolve_and_validate("example.com", None)
    assert str(addr) == "93.184.216.34"


def test_resolve_rejects_rebind_to_private(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hostile resolver answers a private IP at connect time.
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("10.0.0.5"))
    with pytest.raises(ValueError, match="blocked address range"):
        resolve_and_validate("evil.example.com", None)


def test_resolve_rejects_rebind_to_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254"))
    egress = EgressConfig(allowed_private_subnets=["169.254.0.0/16"])
    with pytest.raises(ValueError, match="blocked address range"):
        resolve_and_validate("evil.example.com", egress)


def test_resolve_validates_every_record(monkeypatch: pytest.MonkeyPatch) -> None:
    # A multi-record answer hiding one private IP must be rejected.
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34", "10.0.0.5"))
    with pytest.raises(ValueError, match="blocked address range"):
        resolve_and_validate("mixed.example.com", None)


def test_resolve_allows_internal_when_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("10.1.2.3"))
    egress = EgressConfig(
        allowed_private_subnets=["10.0.0.0/8"],
        allowed_internal_domains=[".svc.cluster.local"],
    )
    addr = resolve_and_validate("api.svc.cluster.local", egress)
    assert str(addr) == "10.1.2.3"


def test_resolve_raises_on_nxdomain(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a, **_k):
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    with pytest.raises(ValueError, match="did not resolve"):
        resolve_and_validate("ghost.example.com", None)


# --- DnsPinningTransport (the pin) --------------------------------------------


@pytest.mark.asyncio
async def test_transport_pins_to_resolved_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, None)
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.get("https://example.com/path")

    assert resp.status_code == 204
    assert inner.seen is not None
    # The connection is pinned to the validated IP...
    assert inner.seen.url.host == "93.184.216.34"
    # ...while Host header + SNI still carry the real name for cert validation.
    assert inner.seen.headers["host"] == "example.com"
    assert inner.seen.extensions.get("sni_hostname") == "example.com"


@pytest.mark.asyncio
async def test_transport_blocks_rebind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254"))
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, None)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="blocked address range"):
            await client.get("https://evil.example.com/")
    # The blocked request never reached the inner transport.
    assert inner.seen is None


@pytest.mark.asyncio
async def test_transport_passes_through_ip_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    # A public IP-literal host has nothing to rebind; no resolution should occur,
    # and a public address passes the egress policy unchanged.
    def _should_not_resolve(*_a, **_k):
        raise AssertionError("getaddrinfo must not be called for an IP literal")

    monkeypatch.setattr(socket, "getaddrinfo", _should_not_resolve)
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, None)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("http://93.184.216.34/x")
    assert inner.seen is not None
    assert inner.seen.url.host == "93.184.216.34"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata IMDS
        "http://10.0.0.5/",  # private range
        "http://127.0.0.1:6379/",  # loopback (e.g. local Redis)
        "http://[::1]/",  # IPv6 loopback literal
    ],
)
async def test_transport_blocks_ip_literal_ssrf_targets(
    monkeypatch: pytest.MonkeyPatch, target_url: str
) -> None:
    """An IP-literal SSRF target is rejected by the transport, not silently sent.

    This is the Phase-0 blocker: previously an IP literal skipped the egress
    policy entirely. It must now hit the same ``assert_ip_allowed`` guard the DNS
    path uses (no resolution needed — there is no name to rebind).
    """

    def _should_not_resolve(*_a, **_k):
        raise AssertionError("getaddrinfo must not be called for an IP literal")

    monkeypatch.setattr(socket, "getaddrinfo", _should_not_resolve)
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, None)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="blocked address range"):
            await client.get(target_url)
    # The blocked request never reached the inner transport.
    assert inner.seen is None


@pytest.mark.asyncio
async def test_transport_allows_public_ipv6_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A public IPv6 literal still passes (only blocked ranges are rejected)."""

    def _should_not_resolve(*_a, **_k):
        raise AssertionError("getaddrinfo must not be called for an IP literal")

    monkeypatch.setattr(socket, "getaddrinfo", _should_not_resolve)
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, None)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("http://[2606:2800:220:1:248:1893:25c8:1946]/")
    assert inner.seen is not None
    assert inner.seen.url.host == "2606:2800:220:1:248:1893:25c8:1946"


@pytest.mark.asyncio
async def test_transport_allows_allowlisted_private_ip_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator-allowlisted private literal is permitted — same policy as DNS."""

    def _should_not_resolve(*_a, **_k):
        raise AssertionError("getaddrinfo must not be called for an IP literal")

    monkeypatch.setattr(socket, "getaddrinfo", _should_not_resolve)
    inner = _RecordingTransport()
    egress = EgressConfig(allowed_private_subnets=["10.0.0.0/8"])
    transport = DnsPinningTransport(inner, egress)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("http://10.1.2.3/")
    assert inner.seen is not None
    assert inner.seen.url.host == "10.1.2.3"


def test_build_client_wraps_transport_when_pinning_enabled() -> None:
    cfg = UpstreamClientConfig()
    client = build_client(cfg, EgressConfig(dns_pinning_enabled=True))
    assert isinstance(client._transport, DnsPinningTransport)


def test_build_client_no_wrap_when_pinning_disabled() -> None:
    cfg = UpstreamClientConfig()
    client = build_client(cfg, EgressConfig(dns_pinning_enabled=False))
    assert not isinstance(client._transport, DnsPinningTransport)
    client2 = build_client(cfg, None)
    assert not isinstance(client2._transport, DnsPinningTransport)
