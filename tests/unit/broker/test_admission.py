"""Unit tests for the broker admission-control middleware (§04 R1).

Past ``max_in_flight`` the next request is shed with ``503`` + ``Retry-After``
without queuing; infra routes (``/health``, ``/metrics``) are never gated; a
slot releases once its response completes, before any post-response work.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from starlette.background import BackgroundTask
from starlette.responses import Response, StreamingResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jentic_one.broker.web.middleware import AdmissionControlMiddleware, _AdmissionGate


def _app(*, max_in_flight: int, gate: asyncio.Event | None = None) -> FastAPI:
    app = FastAPI()

    @app.get("/slow")
    async def slow() -> dict[str, str]:
        if gate is not None:
            await gate.wait()
        return {"status": "ok"}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(AdmissionControlMiddleware, max_in_flight=max_in_flight, retry_after_s=7)
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_sheds_503_with_retry_after_past_cap() -> None:
    gate = asyncio.Event()
    app = _app(max_in_flight=1, gate=gate)
    async with _client(app) as client:
        # Occupy the only slot with a request held open by the gate.
        held = asyncio.create_task(client.get("/slow"))
        await asyncio.sleep(0.05)

        shed = await client.get("/slow")
        assert shed.status_code == 503
        assert shed.headers["retry-after"] == "7"
        assert shed.headers["content-type"] == "application/problem+json"
        assert shed.json()["status"] == 503

        # Release the held request; the slot frees and the next request succeeds.
        gate.set()
        assert (await held).status_code == 200

        ok = await client.get("/slow")
        assert ok.status_code == 200


@pytest.mark.asyncio
async def test_health_excluded_even_while_shedding() -> None:
    gate = asyncio.Event()
    app = _app(max_in_flight=1, gate=gate)
    async with _client(app) as client:
        held = asyncio.create_task(client.get("/slow"))
        await asyncio.sleep(0.05)

        # The single slot is occupied — a /slow would shed — but /health is
        # never gated and must still answer 200.
        shed = await client.get("/slow")
        assert shed.status_code == 503

        health = await client.get("/health")
        assert health.status_code == 200

        gate.set()
        await held


@pytest.mark.asyncio
async def test_connection_close_stamped_while_draining() -> None:
    # During drain the middleware stamps Connection: close so a keep-alive
    # client/LB tears the connection down and re-resolves to a healthy pod
    # (§09 E4.3). A non-draining gate leaves the header untouched.
    admission_gate = _AdmissionGate(max_in_flight=5)
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(
        AdmissionControlMiddleware, max_in_flight=5, retry_after_s=7, gate=admission_gate
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        normal = await client.get("/ping")
        assert normal.headers.get("connection", "").lower() != "close"

        admission_gate.start_draining()
        draining = await client.get("/ping")
        assert draining.status_code == 200
        assert draining.headers["connection"].lower() == "close"


async def _drive(
    app: ASGIApp, path: str, sent: list[Message], *, disconnect: asyncio.Event | None = None
) -> None:
    """Run one raw ASGI request through ``app``, recording what it sends."""
    scope = {"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""}
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await (disconnect or asyncio.Event()).wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_slot_released_when_response_completes_not_after_background(
    streaming: bool,
) -> None:
    """Post-response background work holds no slot and doesn't count toward /ready."""
    admission_gate = _AdmissionGate(max_in_flight=1)
    background_started = asyncio.Event()
    background_release = asyncio.Event()

    async def _background() -> None:
        background_started.set()
        await background_release.wait()

    async def _body() -> AsyncIterator[bytes]:
        yield b"ok"

    app = FastAPI()

    @app.get("/done")
    async def done() -> Response:
        task = BackgroundTask(_background)
        if streaming:
            return StreamingResponse(_body(), background=task)
        return Response(b"ok", background=task)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(
        AdmissionControlMiddleware, max_in_flight=1, retry_after_s=7, gate=admission_gate
    )

    sent: list[Message] = []
    request = asyncio.create_task(_drive(app, "/done", sent))
    async with asyncio.timeout(2.0):
        await background_started.wait()

    # The response is complete and the background task still runs inside the
    # app call, yet the slot is already free.
    assert sent[-1]["type"] == "http.response.body"
    assert not sent[-1].get("more_body", False)
    assert not request.done()
    assert admission_gate.in_flight == 0
    assert admission_gate.saturation == 0.0

    # A second request is admitted rather than shed while the first one's
    # background work is still running.
    second: list[Message] = []
    async with asyncio.timeout(2.0):
        await _drive(app, "/ping", second)
    assert second[0]["status"] == 200
    assert not request.done()

    background_release.set()
    await request
    assert admission_gate.in_flight == 0


@pytest.mark.asyncio
async def test_slot_released_once_when_app_raises_after_response() -> None:
    """A slot freed at response completion is not released again on exit."""
    admission_gate = _AdmissionGate(max_in_flight=2)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})
        assert admission_gate.in_flight == 0
        raise RuntimeError("post-response failure")

    middleware = AdmissionControlMiddleware(
        app, max_in_flight=2, retry_after_s=7, gate=admission_gate
    )
    with pytest.raises(RuntimeError):
        await _drive(middleware, "/x", [])
    assert admission_gate.in_flight == 0
