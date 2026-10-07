"""Unit tests for the HTTP runner: shared injected client + per-host bulkhead (§04)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import structlog.testing
from fastapi import FastAPI, Request, Response
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import jentic_one.broker.adapters.runners.http as http_runner_mod
from jentic_one.broker.adapters.runners.base import RunnerRequest
from jentic_one.broker.adapters.runners.http import HttpRunner, _HostBulkhead
from jentic_one.broker.core.exceptions import BrokerError, UpstreamTimeoutError


def _asgi_client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://api.example.com")


def _mock_client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


@pytest.mark.asyncio
async def test_returns_verbatim_status_and_body() -> None:
    app = FastAPI()

    @app.get("/x")
    async def x() -> Response:
        return Response(content=b"teapot", status_code=418, media_type="text/plain")

    async with _asgi_client(app) as client:
        runner = HttpRunner(client)
        result = await runner.run(RunnerRequest(method="GET", url="http://api.example.com/x"))

    assert result.status_code == 418
    assert result.body == b"teapot"
    assert result.content_type is not None
    assert result.content_type.startswith("text/plain")


@pytest.mark.asyncio
async def test_forwards_method_body_headers() -> None:
    seen: dict[str, object] = {}
    app = FastAPI()

    @app.post("/x")
    async def x(request: Request) -> Response:
        seen["method"] = request.method
        seen["body"] = await request.body()
        seen["x-test"] = request.headers.get("x-test")
        return Response(content=b"ok", status_code=200)

    async with _asgi_client(app) as client:
        runner = HttpRunner(client)
        await runner.run(
            RunnerRequest(
                method="POST",
                url="http://api.example.com/x",
                headers={"X-Test": "v"},
                body=b"payload",
            )
        )

    assert seen == {"method": "POST", "body": b"payload", "x-test": "v"}


@pytest.mark.asyncio
async def test_timeout_maps_to_domain_error() -> None:
    def handle(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    async with _mock_client(httpx.MockTransport(handle)) as client:
        runner = HttpRunner(client)
        with pytest.raises(UpstreamTimeoutError):
            await runner.run(RunnerRequest(method="GET", url="https://api.example.com/x"))


@pytest.mark.asyncio
async def test_transport_error_maps_to_broker_error() -> None:
    def handle(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with _mock_client(httpx.MockTransport(handle)) as client:
        runner = HttpRunner(client)
        with pytest.raises(BrokerError):
            await runner.run(RunnerRequest(method="GET", url="https://api.example.com/x"))


_INJECTED_SECRET = "SECRET_TRAILING_NL"


def _raise_header_error(_req: httpx.Request) -> httpx.Response:
    # What h11 raises for a header value carrying CR/LF: the message quotes it.
    raise httpx.LocalProtocolError(f"Illegal header value b'{_INJECTED_SECRET}\\n'")


@pytest.mark.asyncio
async def test_transport_error_detail_and_span_omit_exception_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(http_runner_mod, "_tracer", provider.get_tracer("test"))

    async with _mock_client(httpx.MockTransport(_raise_header_error)) as client:
        runner = HttpRunner(client)
        with structlog.testing.capture_logs() as logs, pytest.raises(BrokerError) as excinfo:
            await runner.run(RunnerRequest(method="GET", url="https://api.example.com/x"))

    assert excinfo.value.detail == "Upstream transport error (LocalProtocolError)"
    assert _INJECTED_SECRET not in repr(logs)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True
    (span,) = exporter.get_finished_spans()
    events = [dict(e.attributes or {}) for e in span.events if e.name == "exception"]
    # Our transport event (type only) plus the SDK's auto-recorded BrokerError.
    assert events[0] == {
        "exception.type": "LocalProtocolError",
        "exception.message": "Upstream transport error (LocalProtocolError)",
    }
    assert _INJECTED_SECRET not in repr(events)


@pytest.mark.asyncio
async def test_stream_transport_error_detail_omits_exception_message() -> None:
    async with _mock_client(httpx.MockTransport(_raise_header_error)) as client:
        runner = HttpRunner(client)
        with structlog.testing.capture_logs() as logs, pytest.raises(BrokerError) as excinfo:
            async with runner.stream(RunnerRequest(method="GET", url="https://api.example.com/x")):
                pass  # pragma: no cover - the stream never opens

    assert excinfo.value.detail == "Upstream transport error (LocalProtocolError)"
    assert _INJECTED_SECRET not in repr(logs)
    assert [e["error_type"] for e in logs] == ["LocalProtocolError"]


@pytest.mark.asyncio
async def test_per_host_bulkhead_caps_concurrency() -> None:
    in_flight = 0
    peak = 0
    release = asyncio.Event()
    app = FastAPI()

    @app.get("/x")
    async def x() -> Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await release.wait()
        in_flight -= 1
        return Response(content=b"ok", status_code=200)

    async with _asgi_client(app) as client:
        runner = HttpRunner(client, max_per_host=2)
        tasks = [
            asyncio.create_task(
                runner.run(RunnerRequest(method="GET", url="http://same.example.com/x"))
            )
            for _ in range(5)
        ]
        await asyncio.sleep(0.1)
        # Only 2 may be in-flight to the same host at once (the bulkhead).
        assert peak <= 2
        release.set()
        results = await asyncio.gather(*tasks)
    assert all(r.status_code == 200 for r in results)


def test_bulkhead_evicts_idle_and_skips_gate_when_full() -> None:
    bulkhead = _HostBulkhead(per_host=2, max_hosts=2)
    # Two distinct idle hosts fill the map.
    assert bulkhead._slot_for("a") is not None
    assert bulkhead._slot_for("b") is not None
    # A third host evicts an idle entry to make room (still bounded at 2).
    assert bulkhead._slot_for("c") is not None
    assert len(bulkhead._slots) == 2


# ---------------------------------------------------------------------------
# Accept-Encoding passthrough contract (§04 strict passthrough)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_accept_encoding_injects_identity() -> None:
    """When the caller omits Accept-Encoding, the runner injects 'identity'."""
    seen_headers: dict[str, str] = {}
    app = FastAPI()

    @app.get("/x")
    async def x(request: Request) -> Response:
        seen_headers.update(dict(request.headers))
        return Response(content=b"ok", status_code=200)

    async with _asgi_client(app) as client:
        runner = HttpRunner(client)
        await runner.run(RunnerRequest(method="GET", url="http://api.example.com/x", headers={}))

    assert seen_headers["accept-encoding"] == "identity"


@pytest.mark.asyncio
async def test_caller_accept_encoding_forwarded_verbatim() -> None:
    """When the caller provides Accept-Encoding, it passes through unchanged."""
    seen_headers: dict[str, str] = {}
    app = FastAPI()

    @app.get("/x")
    async def x(request: Request) -> Response:
        seen_headers.update(dict(request.headers))
        return Response(content=b"ok", status_code=200)

    async with _asgi_client(app) as client:
        runner = HttpRunner(client)
        await runner.run(
            RunnerRequest(
                method="GET",
                url="http://api.example.com/x",
                headers={"Accept-Encoding": "gzip"},
            )
        )

    assert seen_headers["accept-encoding"] == "gzip"


@pytest.mark.asyncio
async def test_stream_no_accept_encoding_injects_identity() -> None:
    """Streaming path also injects 'identity' when caller omits Accept-Encoding."""
    seen_headers: dict[str, str] = {}
    app = FastAPI()

    @app.get("/x")
    async def x(request: Request) -> Response:
        seen_headers.update(dict(request.headers))
        return Response(content=b"streamed", status_code=200)

    async with _asgi_client(app) as client:
        runner = HttpRunner(client)
        async with runner.stream(
            RunnerRequest(method="GET", url="http://api.example.com/x", headers={})
        ) as result:
            chunks = [chunk async for chunk in result.aiter]
            assert b"".join(chunks) == b"streamed"

    assert seen_headers["accept-encoding"] == "identity"


@pytest.mark.asyncio
async def test_stream_caller_accept_encoding_forwarded_verbatim() -> None:
    """Streaming path forwards caller's Accept-Encoding verbatim."""
    seen_headers: dict[str, str] = {}
    app = FastAPI()

    @app.get("/x")
    async def x(request: Request) -> Response:
        seen_headers.update(dict(request.headers))
        return Response(content=b"streamed", status_code=200)

    async with _asgi_client(app) as client:
        runner = HttpRunner(client)
        async with runner.stream(
            RunnerRequest(
                method="GET",
                url="http://api.example.com/x",
                headers={"Accept-Encoding": "br"},
            )
        ) as result:
            chunks = [chunk async for chunk in result.aiter]
            assert b"".join(chunks) == b"streamed"

    assert seen_headers["accept-encoding"] == "br"
