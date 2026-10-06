"""Unit tests for shared logging configuration."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import logging.config
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import h2.config
import h2.connection
import httpx
import pytest
import structlog
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool
from starlette.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send
from uvicorn.config import LOGGING_CONFIG

from jentic_one.shared.config import AppConfig
from jentic_one.shared.logging import (
    RequestIDMiddleware,
    add_otel_context,
    configure_logging,
    request_id_ctx,
)


@pytest.fixture()
def minimal_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "databases": {
                "registry": {"name": "r"},
                "admin": {"name": "a"},
                "control": {"name": "c"},
            },
            "runtime": {"debug": False, "log_level": "WARNING"},
        }
    )


@pytest.fixture()
def debug_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "databases": {
                "registry": {"name": "r"},
                "admin": {"name": "a"},
                "control": {"name": "c"},
            },
            "runtime": {"debug": True, "log_level": "DEBUG"},
        }
    )


@pytest.fixture()
def middleware_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/test")
    async def test_endpoint():
        return {"request_id": request_id_ctx.get()}

    return app


@pytest.fixture()
def client(middleware_app: FastAPI) -> TestClient:
    return TestClient(middleware_app)


def test_configure_logging_sets_root_logger_level(minimal_config: AppConfig):
    configure_logging(minimal_config)
    root = logging.getLogger()
    assert root.level == logging.WARNING


def test_configure_logging_sets_debug_level(debug_config: AppConfig):
    configure_logging(debug_config)
    root = logging.getLogger()
    assert root.level == logging.DEBUG


def test_configure_logging_clamps_httpcore_trace_logs_at_debug(debug_config: AppConfig):
    """httpcore's DEBUG trace lines repr transport exceptions (which can quote an
    outbound header value), so they stay off even when the app runs at DEBUG."""
    configure_logging(debug_config)
    assert logging.getLogger().level == logging.DEBUG
    assert not logging.getLogger("httpcore.http11").isEnabledFor(logging.DEBUG)


def test_http_wire_trace_toggle_lifts_the_httpcore_clamp() -> None:
    config = AppConfig.model_validate(
        {
            "databases": {
                "registry": {"name": "r"},
                "admin": {"name": "a"},
                "control": {"name": "c"},
            },
            "runtime": {"debug": True, "log_level": "DEBUG"},
            "logging": {"http_wire_trace": True},
        }
    )
    try:
        configure_logging(config)
        assert logging.getLogger("httpcore.http11").isEnabledFor(logging.DEBUG)
        assert logging.getLogger("hpack.hpack").isEnabledFor(logging.DEBUG)
    finally:
        logging.getLogger("httpcore").setLevel(logging.INFO)
        logging.getLogger("hpack").setLevel(logging.INFO)


def test_http2_header_encoding_never_logs_header_values_at_debug(
    debug_config: AppConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """hpack logs every HTTP/2 header it encodes at DEBUG (``:path`` with its
    query string, and injected header credentials); it stays clamped at DEBUG."""
    configure_logging(debug_config)
    conn = h2.connection.H2Connection(h2.config.H2Configuration(client_side=True))
    conn.initiate_connection()
    conn.send_headers(
        1,
        [
            (b":method", b"GET"),
            (b":authority", b"upstream.example"),
            (b":scheme", b"https"),
            (b":path", b"/v1/search?api_key=qk_live_query_secret"),
            (b"x-api-key", b"hk_live_header_secret"),
        ],
        end_stream=True,
    )
    for handler in logging.getLogger().handlers:
        handler.flush()

    out = capsys.readouterr().out
    assert "qk_live_query_secret" not in out
    assert "hk_live_header_secret" not in out


async def test_httpx_request_log_masks_query_param_api_key(
    minimal_config: AppConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """httpx logs every request at INFO with the full URL; a query-located API
    key must not reach the sink, while host and path stay readable."""
    config = minimal_config.model_copy(
        update={"runtime": minimal_config.runtime.model_copy(update={"log_level": "INFO"})}
    )
    configure_logging(config)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200)))
    try:
        await client.get("https://upstream.example/v1/search?api_key=qk_live_secret&q=term")
    finally:
        await client.aclose()
        for handler in logging.getLogger().handlers:
            handler.flush()

    out = capsys.readouterr().out
    assert "HTTP Request: GET https://upstream.example/v1/search?api_key=" in out
    assert "qk_live_secret" not in out
    assert "q=term" not in out


async def test_httpx_request_log_stays_masked_after_uvicorn_dictconfig(
    minimal_config: AppConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """uvicorn applies its own ``dictConfig`` after our logging setup; the mask is a
    logger filter, so it must survive that re-configuration."""
    config = minimal_config.model_copy(
        update={"runtime": minimal_config.runtime.model_copy(update={"log_level": "INFO"})}
    )
    configure_logging(config)
    logging.config.dictConfig(LOGGING_CONFIG)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200)))
    try:
        await client.get("https://upstream.example/v1/search?api_key=qk_live_secret")
    finally:
        await client.aclose()
        for handler in logging.getLogger().handlers:
            handler.flush()

    out = capsys.readouterr().out
    assert "HTTP Request: GET https://upstream.example/v1/search?api_key=" in out
    assert "qk_live_secret" not in out


def test_configure_logging_installs_the_httpx_url_filter_once(minimal_config: AppConfig) -> None:
    configure_logging(minimal_config)
    configure_logging(minimal_config)
    filters = logging.getLogger("httpx").filters
    assert sum(type(f).__name__ == "_OutboundUrlQueryFilter" for f in filters) == 1


def test_configure_logging_root_logger_has_single_handler(minimal_config: AppConfig):
    configure_logging(minimal_config)
    root = logging.getLogger()
    assert len(root.handlers) == 1


def test_configure_logging_adds_file_handler_and_writes_json(tmp_path: Path):
    config = AppConfig.model_validate(
        {
            "databases": {
                "registry": {"name": "r"},
                "admin": {"name": "a"},
                "control": {"name": "c"},
            },
            "runtime": {"debug": False, "log_level": "INFO"},
            "logging": {"file_enabled": True, "file_dir": str(tmp_path), "file_name": "app.log"},
        }
    )

    configure_logging(config)
    try:
        root = logging.getLogger()
        assert len(root.handlers) == 2

        log_file = tmp_path / "app.log"
        assert log_file.exists()

        structlog.get_logger("test").info("file_logging_works", answer=42)

        lines = [line for line in log_file.read_text().splitlines() if line.strip()]
        assert lines, "expected at least one log line in the file"
        record = json.loads(lines[-1])
        assert record["event"] == "file_logging_works"
        assert record["answer"] == 42
    finally:
        for handler in logging.getLogger().handlers:
            handler.close()


def test_add_otel_context_injects_trace_and_span_when_recording():
    mock_span_context = MagicMock()
    mock_span_context.trace_id = 0xABCDEF1234567890ABCDEF1234567890
    mock_span_context.span_id = 0x1234567890ABCDEF

    mock_span = MagicMock()
    mock_span.is_recording.return_value = True
    mock_span.get_span_context.return_value = mock_span_context

    with patch("opentelemetry.trace.get_current_span", return_value=mock_span):
        event_dict: dict[str, object] = {"event": "test"}
        result = add_otel_context(None, "info", event_dict)

    assert "trace_id" in result
    assert "span_id" in result
    assert result["trace_id"] == format(mock_span_context.trace_id, "032x")
    assert result["span_id"] == format(mock_span_context.span_id, "016x")


def test_add_otel_context_noop_when_not_recording():
    mock_span = MagicMock()
    mock_span.is_recording.return_value = False

    with patch("opentelemetry.trace.get_current_span", return_value=mock_span):
        event_dict: dict[str, object] = {"event": "test"}
        result = add_otel_context(None, "info", event_dict)

    assert "trace_id" not in result
    assert "span_id" not in result


def test_middleware_generates_request_id(client: TestClient):
    response = client.get("/test")
    assert response.status_code == 200
    assert "x-request-id" in response.headers
    assert response.headers["x-request-id"].startswith("req_")


def test_middleware_accepts_valid_inbound_request_id(client: TestClient):
    response = client.get("/test", headers={"x-request-id": "custom-id-123"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "custom-id-123"


def test_middleware_rejects_request_id_with_invalid_chars(client: TestClient):
    response = client.get("/test", headers={"x-request-id": "bad\nvalue"})
    assert response.status_code == 200
    assert response.headers["x-request-id"].startswith("req_")


def test_middleware_rejects_oversized_request_id(client: TestClient):
    response = client.get("/test", headers={"x-request-id": "a" * 200})
    assert response.status_code == 200
    assert response.headers["x-request-id"].startswith("req_")


def test_middleware_request_id_available_in_handler(client: TestClient):
    response = client.get("/test", headers={"x-request-id": "propagated-id"})
    assert response.json()["request_id"] == "propagated-id"


def test_middleware_does_not_duplicate_request_id_header(client: TestClient):
    response = client.get("/test", headers={"x-request-id": "single-id"})
    # raw_headers preserves duplicates; the ASGI send-wrapper must replace, not append.
    request_id_headers = [
        value for name, value in response.headers.raw if name.lower() == b"x-request-id"
    ]
    assert request_id_headers == [b"single-id"]


def test_middleware_unbinds_contextvar_after_request(client: TestClient):
    client.get("/test", headers={"x-request-id": "transient-id"})
    # The pure-ASGI middleware unbinds the structlog contextvar in a finally, so
    # nothing leaks into the next request's logging context.
    assert "request_id" not in structlog.contextvars.get_contextvars()


async def test_middleware_passes_through_non_http_scope():
    # Pure-ASGI middleware must forward lifespan/websocket scopes untouched
    # rather than assume an HTTP request.
    seen: dict[str, object] = {}

    async def downstream(scope, receive, send):
        seen["scope_type"] = scope["type"]

    middleware = RequestIDMiddleware(downstream)

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        pass

    await middleware({"type": "lifespan"}, receive, send)
    assert seen["scope_type"] == "lifespan"


async def test_middleware_does_not_trap_cancel_scope_on_disconnect(tmp_path: Path):
    """RequestIDMiddleware must not strand a DB connection on client disconnect (#627).

    The original leak came from ``BaseHTTPMiddleware`` wrapping the downstream
    app in an anyio task + cancel scope: on SSE client disconnect the
    ``CancelledError`` landed across that scope boundary while a pooled
    connection was held inside SQLAlchemy, and the connection was never returned.

    This drives the *pure-ASGI* ``RequestIDMiddleware`` by hand — no
    ``TestClient`` (which swallows disconnects) — with a downstream app that
    checks a real connection out of a SQLAlchemy pool and then blocks on
    ``receive()``. We simulate the client disconnect by raising
    ``CancelledError`` (exactly what the server does to the request task on
    ``http.disconnect``) and assert the pool returns to zero — proving the
    middleware propagates cancellation straight through ``async with`` cleanup
    rather than trapping it in a cancel scope.

    Uses a file-backed SQLite engine with a real ``QueuePool`` (so ``checkedout``
    is meaningful), keeping this a true unit test (no external services). The DB
    connection is real (DB mocking is banned) — only the transport-level
    disconnect is simulated.
    """
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'leak.db'}",
        poolclass=AsyncAdaptedQueuePool,
    )

    def checked_out() -> int:
        return cast("int", engine.pool.checkedout())  # type: ignore[attr-defined]

    try:
        assert checked_out() == 0

        connection_held = asyncio.Event()

        async def dummy_app(scope: Scope, receive: Receive, send: Send) -> None:
            # Hold a real pooled connection open across the await, mirroring a
            # streaming handler mid-query when the client drops.
            async with engine.connect():
                connection_held.set()
                await send({"type": "http.response.start", "status": 200, "headers": []})
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        # The server cancels the request task on disconnect; the
                        # CancelledError must unwind the ``async with`` above.
                        raise asyncio.CancelledError

        app = RequestIDMiddleware(dummy_app)

        scope: Scope = {"type": "http", "method": "GET", "path": "/stream", "headers": []}

        sent: list[Message] = []

        async def send(message: Message) -> None:
            sent.append(message)

        messages: list[Message] = [{"type": "http.request"}, {"type": "http.disconnect"}]

        async def receive() -> Message:
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        with pytest.raises(asyncio.CancelledError):
            await app(scope, receive, send)

        # The middleware did not trap the cancel scope: the downstream
        # ``async with engine.connect()`` unwound and the connection went home.
        assert connection_held.is_set(), "downstream never acquired the connection"
        assert checked_out() == 0
        # contextvar is unbound in the finally even though the request was cancelled.
        assert "request_id" not in structlog.contextvars.get_contextvars()
    finally:
        with contextlib.suppress(Exception):
            await engine.dispose()
