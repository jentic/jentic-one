"""Unit tests for the DNS-rebinding guard + connection pin (§08 E2)."""

from __future__ import annotations

import contextlib
import ipaddress
import socket
import ssl
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from jentic_one.broker.adapters.egress import DnsPinningTransport, resolve_and_validate
from jentic_one.broker.adapters.http_client import build_client
from jentic_one.broker.adapters.runners.base import RunnerRequest
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.adapters.runners.retry import RetryRunner
from jentic_one.shared import egress as egress_module
from jentic_one.shared.config import EgressConfig, UpstreamClientConfig
from jentic_one.shared.egress import UpstreamHostUnresolvedError


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
    with pytest.raises(UpstreamHostUnresolvedError, match="did not resolve") as excinfo:
        resolve_and_validate("ghost.example.com", None)
    # Typed so the runner can map it, still a ValueError so callers fail closed.
    assert isinstance(excinfo.value, ValueError)
    assert excinfo.value.host == "ghost.example.com"


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


@dataclass
class _MultiSniServer:
    """Loopback TLS HTTP/1.1 server routing requests by the connection's TLS SNI."""

    port: int
    client_ssl: ssl.SSLContext
    handshakes: list[str] = field(default_factory=list)
    received: list[tuple[str | None, str, str | None, str | None, str]] = field(
        default_factory=list
    )
    fail_once_paths: set[str] = field(default_factory=set)


@contextlib.contextmanager
def _run_multi_sni_tls_server(*hostnames: str) -> Iterator[_MultiSniServer]:
    """Start a loopback TLS server with per-hostname certificates and SNI tracking."""
    with tempfile.TemporaryDirectory(prefix="jentic-dns-pin-sni-") as raw_tmp:
        tmp = Path(raw_tmp)
        contexts: dict[str, ssl.SSLContext] = {}
        client_ssl = ssl.create_default_context()
        for name in hostnames:
            cert, key = tmp / f"{name}.crt", tmp / f"{name}.key"
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(key),
                    "-out",
                    str(cert),
                    "-days",
                    "1",
                    "-subj",
                    f"/CN={name}",
                    "-addext",
                    f"subjectAltName=DNS:{name}",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(cert, key)
            contexts[name] = server_context
            client_ssl.load_verify_locations(cafile=str(cert))

        handshakes: list[str] = []
        received: list[tuple[str | None, str, str | None, str | None, str]] = []
        fail_once_paths: set[str] = set()
        conn_sni: dict[int, str] = {}

        def _select_cert(
            sock: ssl.SSLObject, server_name: str, _context: ssl.SSLContext
        ) -> int | None:
            handshakes.append(server_name)
            conn_sni[id(sock)] = server_name
            sock.context = contexts[server_name]
            return None

        primary = contexts[hostnames[0]]
        primary.sni_callback = _select_cert

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length).decode("utf-8") if length else ""
                host = self.headers.get("Host")
                authorization = self.headers.get("Authorization")
                sni = conn_sni.get(id(self.connection))
                received.append((sni, self.path, host, authorization, raw_body))
                if self.path in fail_once_paths:
                    fail_once_paths.remove(self.path)
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                payload = (
                    f"backend={sni};path={self.path};host={host};"
                    f"auth={authorization};body={raw_body}"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                self._handle()

            def do_POST(self) -> None:
                self._handle()

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.socket = primary.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield _MultiSniServer(
                port=int(server.server_address[1]),
                client_ssl=client_ssl,
                handshakes=handshakes,
                received=received,
                fail_once_paths=fail_once_paths,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


@pytest.mark.asyncio
async def test_transport_isolates_connection_pool_across_https_origins_sharing_pinned_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two HTTPS origins pinned to the same IP establish distinct TLS connections by hostname."""

    async def _resolve_loopback(_host: str, _egress: EgressConfig | None) -> ipaddress.IPv4Address:
        return ipaddress.IPv4Address("127.0.0.1")

    monkeypatch.setattr(egress_module, "resolve_and_validate_async", _resolve_loopback)

    with _run_multi_sni_tls_server("a.test", "b.test") as srv:
        inner = httpx.AsyncHTTPTransport(verify=srv.client_ssl, http2=False)
        transport = DnsPinningTransport(inner, None)
        async with httpx.AsyncClient(transport=transport) as client:
            first = await client.post(
                f"https://a.test:{srv.port}/first",
                headers={"Authorization": "Bearer secret-a"},
                content=b"body-from-a",
            )
            await first.aread()

            second = await client.post(
                f"https://b.test:{srv.port}/second",
                headers={"Authorization": "Bearer secret-b"},
                content=b"body-from-b",
            )
            await second.aread()

            # A third request to a.test reuses the idle connection for a.test
            # without triggering another TLS handshake.
            third = await client.get(
                f"https://a.test:{srv.port}/third",
                headers={"Authorization": "Bearer secret-a-2"},
            )
            await third.aread()

        assert srv.handshakes == ["a.test", "b.test"]
        assert first.text == (
            f"backend=a.test;path=/first;host=a.test:{srv.port};"
            "auth=Bearer secret-a;body=body-from-a"
        )
        assert second.text == (
            f"backend=b.test;path=/second;host=b.test:{srv.port};"
            "auth=Bearer secret-b;body=body-from-b"
        )
        assert third.text == (
            f"backend=a.test;path=/third;host=a.test:{srv.port};auth=Bearer secret-a-2;body="
        )
        assert srv.received == [
            ("a.test", "/first", f"a.test:{srv.port}", "Bearer secret-a", "body-from-a"),
            ("b.test", "/second", f"b.test:{srv.port}", "Bearer secret-b", "body-from-b"),
            ("a.test", "/third", f"a.test:{srv.port}", "Bearer secret-a-2", ""),
        ]


@pytest.mark.asyncio
async def test_shared_runner_isolates_sync_streaming_and_retries_across_two_actors_and_upstreams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two broker callers sharing one client cannot cross-route credentials across upstreams."""

    async def _resolve_loopback(_host: str, _egress: EgressConfig | None) -> ipaddress.IPv4Address:
        return ipaddress.IPv4Address("127.0.0.1")

    monkeypatch.setattr(egress_module, "resolve_and_validate_async", _resolve_loopback)

    with _run_multi_sni_tls_server("a.test", "b.test") as srv:
        srv.fail_once_paths.add("/retry-b")
        inner = httpx.AsyncHTTPTransport(verify=srv.client_ssl, http2=False)
        transport = DnsPinningTransport(inner, EgressConfig(dns_pinning_enabled=True))
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as shared_client:
            runner = RetryRunner(
                HttpRunner(shared_client),
                max_attempts=2,
                base_backoff_s=0.0,
                max_backoff_s=0.0,
            )

            # Actor 1 executes a sync call against a.test, leaving an idle connection.
            res_actor_1 = await runner.run(
                RunnerRequest(
                    method="POST",
                    url=f"https://a.test:{srv.port}/sync-a",
                    headers={"Authorization": "Bearer actor1-cred-a"},
                    body=b"actor1-payload",
                    timeout_s=5.0,
                )
            )
            assert res_actor_1.status_code == 200
            assert b"backend=a.test;" in res_actor_1.body
            assert b"auth=Bearer actor1-cred-a;body=actor1-payload" in res_actor_1.body

            # Actor 2 streams from b.test over the same shared client.
            streamed_chunks: list[bytes] = []
            async with runner.stream(
                RunnerRequest(
                    method="POST",
                    url=f"https://b.test:{srv.port}/stream-b",
                    headers={"Authorization": "Bearer actor2-cred-b"},
                    body=b"actor2-stream",
                    timeout_s=5.0,
                )
            ) as stream_res:
                assert stream_res.status_code == 200
                async for chunk in stream_res.aiter:
                    streamed_chunks.append(chunk)
            stream_body = b"".join(streamed_chunks)
            assert b"backend=b.test;" in stream_body
            assert b"auth=Bearer actor2-cred-b;body=actor2-stream" in stream_body

            # Actor 2 executes a retried GET against b.test (first 503, second 200).
            retry_res = await runner.run(
                RunnerRequest(
                    method="GET",
                    url=f"https://b.test:{srv.port}/retry-b",
                    headers={"Authorization": "Bearer actor2-cred-b-retry"},
                    body=None,
                    timeout_s=5.0,
                )
            )
            assert retry_res.status_code == 200
            assert b"backend=b.test;" in retry_res.body
            assert b"auth=Bearer actor2-cred-b-retry;" in retry_res.body

        assert srv.handshakes == ["a.test", "b.test"]
        assert [entry[0] for entry in srv.received] == [
            "a.test",
            "b.test",
            "b.test",
            "b.test",
        ]
