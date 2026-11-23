"""Bounds on control-plane IdP exchanges: total deadline, body cap, no inflation."""

from __future__ import annotations

import asyncio
import gzip
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx
import pytest

from jentic_one.shared.http import idp_client
from jentic_one.shared.http.idp_client import IdpTransportError, idp_request

_Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


@pytest.fixture(autouse=True)
def _no_dns_or_pinning() -> Iterator[None]:
    async def _passthrough(url: str, egress: object = None) -> str:
        return url

    with (
        patch.object(idp_client, "validate_upstream_url_async", _passthrough),
        patch.object(
            idp_client, "build_always_pinned_transport", lambda egress: httpx.AsyncHTTPTransport()
        ),
    ):
        yield


@asynccontextmanager
async def _server(handler: _Handler) -> AsyncIterator[str]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/token"
    finally:
        server.close()
        await server.wait_closed()


async def _drain_request(reader: asyncio.StreamReader) -> None:
    await reader.readuntil(b"\r\n\r\n")


async def _drip(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    await _drain_request(reader)
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n")
    await writer.drain()
    try:
        while True:
            writer.write(b" ")
            await writer.drain()
            await asyncio.sleep(0.2)
    except (ConnectionError, asyncio.CancelledError):
        writer.close()


async def test_total_deadline_trips_on_drip_feed() -> None:
    async with _server(_drip) as url:
        start = time.monotonic()
        with pytest.raises(IdpTransportError) as exc:
            await idp_request("POST", url, egress=None, data={"a": "b"}, deadline_s=1.0)
        assert exc.value.kind == "timeout"
        assert time.monotonic() - start < 1.5


async def test_oversized_chunked_body_rejected_before_full_read() -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _drain_request(reader)
        writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
        chunk = b"x" * 65536
        try:
            for _ in range(16):  # 1 MiB
                writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                await writer.drain()
            writer.write(b"0\r\n\r\n")
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    async with _server(handler) as url:
        with pytest.raises(IdpTransportError) as exc:
            await idp_request("GET", url, egress=None, max_bytes=256 * 1024)
        assert exc.value.kind == "too_large"


async def test_oversized_content_length_rejected() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (1024 * 1024))

    with (
        patch.object(
            idp_client,
            "build_always_pinned_transport",
            lambda egress: httpx.MockTransport(respond),
        ),
        pytest.raises(IdpTransportError) as exc,
    ):
        await idp_request("GET", "https://idp.example/x", egress=None, max_bytes=256 * 1024)
    assert exc.value.kind == "too_large"


async def test_gzip_bomb_is_capped() -> None:
    bomb = gzip.compress(b"\0" * (50 * 1024 * 1024))
    assert len(bomb) < 100 * 1024

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=bomb)

    with (
        patch.object(
            idp_client,
            "build_always_pinned_transport",
            lambda egress: httpx.MockTransport(respond),
        ),
        pytest.raises(IdpTransportError) as exc,
    ):
        await idp_request("GET", "https://idp.example/x", egress=None)
    assert exc.value.kind == "too_large"


async def test_request_asks_for_identity_encoding_and_returns_json() -> None:
    seen: dict[str, str] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        seen["ae"] = request.headers["accept-encoding"]
        return httpx.Response(200, json={"access_token": "t"})

    with patch.object(
        idp_client,
        "build_always_pinned_transport",
        lambda egress: httpx.MockTransport(respond),
    ):
        resp = await idp_request("POST", "https://idp.example/x", egress=None, data={"a": "b"})
    assert seen["ae"] == "identity"
    assert resp.status_code == 200
    assert resp.json() == {"access_token": "t"}
    assert "access_token" in resp.text()


_URL_WITH_USERINFO = "https://user:hunter2pass@idp.example/x"  # pragma: allowlist secret


async def test_unsafe_url_message_is_redacted() -> None:
    async def _reject(url: str, egress: object = None) -> str:
        raise ValueError(f"blocked: {url}")

    with (
        patch.object(idp_client, "validate_upstream_url_async", _reject),
        pytest.raises(IdpTransportError) as exc,
    ):
        await idp_request("GET", _URL_WITH_USERINFO, egress=None)
    assert exc.value.kind == "unsafe_url"
    assert "hunter2pass" not in str(exc.value)


async def test_transport_error_carries_class_only() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-token-abc123 refused")

    with (
        patch.object(
            idp_client,
            "build_always_pinned_transport",
            lambda egress: httpx.MockTransport(respond),
        ),
        pytest.raises(IdpTransportError) as exc,
    ):
        await idp_request("GET", "https://idp.example/x", egress=None)
    assert exc.value.kind == "transport"
    assert "ConnectError" in str(exc.value)
    assert "secret-token" not in str(exc.value)
