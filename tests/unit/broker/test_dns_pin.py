"""Unit tests for the DNS-rebinding guard + connection pin (§08 E2)."""

from __future__ import annotations

import socket
import threading

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
    # An IP-literal host has nothing to rebind; no resolution should occur.
    def _should_not_resolve(*_a, **_k):
        raise AssertionError("getaddrinfo must not be called for an IP literal")

    monkeypatch.setattr(socket, "getaddrinfo", _should_not_resolve)
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, None)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("http://93.184.216.34/x")
    assert inner.seen is not None
    assert inner.seen.url.host == "93.184.216.34"


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


@pytest.mark.parametrize(
    "ip", ["::ffff:127.0.0.1", "64:ff9b::a9fe:a9fe", "2002:a00:1::", "100.64.0.1", "::"]
)
def test_resolve_and_validate_blocks_embedded_and_non_global(
    monkeypatch: pytest.MonkeyPatch, ip: str
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(ip))
    with pytest.raises(ValueError, match="blocked address range"):
        resolve_and_validate("evil.example.com", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["127.0.0.1", "[::ffff:169.254.169.254]", "[64:ff9b::a00:1]"])
async def test_transport_blocks_non_public_ip_literal(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    # IP literals are not resolved, but the policy still applies at connect time.
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, None)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="blocked address range"):
            await client.get(f"http://{host}/x")
    assert inner.seen is None


@pytest.mark.asyncio
async def test_transport_allows_allowlisted_ip_literal() -> None:
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, EgressConfig(allowed_private_subnets=["10.50.0.0/16"]))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("http://10.50.2.10/x")
    assert inner.seen is not None


@pytest.mark.asyncio
async def test_transport_fails_closed_on_unresolvable_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a, **_k):
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, None)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="did not resolve"):
            await client.get("https://ghost.example.com/")
    assert inner.seen is None


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["[fe80::1%25eth0]", "[fd00:ec2::254%25eth0]"])
async def test_transport_blocks_zone_id_literal_even_when_allowlisted(host: str) -> None:
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, EgressConfig(allowed_private_subnets=["fd00:ec2::/32"]))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="blocked address range"):
            await client.get(f"http://{host}/x")
    assert inner.seen is None


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["2130706433", "0x7f.1", "017700000001"])
async def test_transport_resolves_numeric_ipv4_spelling_and_blocks(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("127.0.0.1"))
    inner = _RecordingTransport()
    transport = DnsPinningTransport(inner, None)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="blocked address range"):
            await client.get(f"http://{host}/x")
    assert inner.seen is None


@pytest.mark.asyncio
async def test_transport_resolves_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    # getaddrinfo blocks; the transport must not call it on the event-loop thread.
    loop_thread = threading.get_ident()
    calls: list[int] = []

    def _resolver(*_a, **_k):
        calls.append(threading.get_ident())
        return _fake_getaddrinfo("93.184.216.34")("api.example.com")

    monkeypatch.setattr(socket, "getaddrinfo", _resolver)
    inner = _RecordingTransport()
    async with httpx.AsyncClient(transport=DnsPinningTransport(inner, None)) as client:
        await client.get("https://api.example.com/x")
    assert calls and calls[0] != loop_thread
    assert inner.seen is not None
