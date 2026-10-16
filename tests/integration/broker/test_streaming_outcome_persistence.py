"""Integration test: every streaming execute outcome persists exactly one record.

Pins #1712 and #1736. The broker's streaming handler runs behind a real uvicorn
server and proxies a real-socket upstream; the execution record and its
lifecycle event land in the real admin DB. Covered outcomes:

* the upstream completes the body → one COMPLETED record;
* the upstream drops the connection mid-body → one FAILED record carrying the
  upstream status, one ``execution.failed`` event, one structured
  ``upstream_network_failure`` log line bound to the request id, no
  ``unhandled_exception`` log, and a response the client sees as truncated;
* the client disconnects mid-body → one record;
* persisting the record fails → the failure is counted, nothing escapes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import AsyncGenerator, MutableMapping
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import structlog
import uvicorn
from fastapi import FastAPI, Request, Response
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.headers import JenticHeader
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.routers import execute as execute_router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.logging import RequestIDMiddleware, install_server_log_filters
from jentic_one.shared.models import ActorType, ExecutionStatus
from jentic_one.shared.models.events import EventType
from jentic_one.shared.schemas import OperationInfo
from jentic_one.shared.web.errors import unhandled_exception_handler

pytestmark = pytest.mark.integration

_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
_FIRST_CHUNK = b"partial-body-"

_IDENTITY = Identity(
    sub="agnt_stream_outcome_test",
    actor_type=ActorType.AGENT,
    permissions=["execute"],
    active=True,
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@dataclass
class _Upstream:
    """A chunked HTTP/1.1 upstream on a real socket.

    ``/ok`` sends a complete body; ``/drop`` sends one chunk then closes the
    socket without the terminating chunk; ``/hang`` sends one chunk then holds
    the connection open until the broker closes it.
    """

    port: int


@pytest.fixture()
async def upstream() -> AsyncGenerator[_Upstream, None]:
    async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        path = head.split(b" ", 2)[1].decode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
            b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
            + f"{len(_FIRST_CHUNK):x}\r\n".encode()
            + _FIRST_CHUNK
            + b"\r\n"
        )
        await writer.drain()
        if path == "/ok":
            writer.write(b"4\r\ndone\r\n0\r\n\r\n")
            await writer.drain()
        elif path == "/hang":
            # Hold the body open until the broker closes the upstream connection.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(reader.read(), 10)
        writer.close()

    server = await asyncio.start_server(_serve, "127.0.0.1", 0)
    try:
        yield _Upstream(port=server.sockets[0].getsockname()[1])
    finally:
        server.close()
        await server.wait_closed()


def _app(ctx: Context, runner: HttpRunner, upstream_port: int) -> FastAPI:
    """The streaming handler with the broker's request-id middleware + error handlers."""
    app = FastAPI()
    install_broker_error_handlers(app)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(RequestIDMiddleware)

    @app.get("/execute/{mode}")
    async def execute(mode: str, request: Request) -> Response:
        ctx_req = ExecuteRequestContext(
            upstream_url=f"http://127.0.0.1:{upstream_port}/{mode}",
            method=request.method,
            trace_id=_TRACE_ID,
            operation=OperationInfo(id="getStream", path="/stream", method="GET"),
        )
        return await execute_router._handle_streaming(
            request,
            ctx_req,
            ctx,
            _IDENTITY,
            runner,
            ctx.config.broker.resilience.upstream,
        )

    return app


class _Records(logging.Handler):
    """Collects stdlib records (uvicorn's own server log)."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@dataclass
class _Served:
    base_url: str
    logs: list[MutableMapping[str, Any]]
    server_log: _Records


@pytest.fixture()
async def served(
    request: pytest.FixtureRequest, integration_context: Context, upstream: _Upstream
) -> AsyncGenerator[_Served, None]:
    """The app behind uvicorn; an indirect param sets the runner's response-size cap."""
    max_response_bytes: int = getattr(request, "param", 0)
    async with httpx.AsyncClient() as upstream_client:
        runner = HttpRunner(upstream_client, max_response_bytes=max_response_bytes)
        app = _app(integration_context, runner, upstream.port)
        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, lifespan="off")
        )
        install_server_log_filters()
        server_log = _Records()
        uvicorn_error = logging.getLogger("uvicorn.error")
        uvicorn_error.addHandler(server_log)
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            task = asyncio.create_task(server.serve())
            while not server.started:
                await asyncio.sleep(0.02)
            try:
                yield _Served(f"http://127.0.0.1:{port}", logs, server_log)
            finally:
                server.should_exit = True
                await task
                uvicorn_error.removeHandler(server_log)


@pytest.fixture()
async def execution_ids(integration_context: Context) -> AsyncGenerator[list[str], None]:
    """Execution ids the test produced; their records and events are removed after."""
    ids: list[str] = []
    yield ids
    if ids:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(delete(Event).where(Event.execution_id.in_(ids)))
            await session.execute(delete(ExecutionRecord).where(ExecutionRecord.id.in_(ids)))


async def _records(ctx: Context, execution_id: str) -> list[ExecutionRecord]:
    async with ctx.admin_db.session() as session:
        return list(
            (
                await session.execute(
                    select(ExecutionRecord).where(ExecutionRecord.id == execution_id)
                )
            ).scalars()
        )


async def _events(ctx: Context, execution_id: str) -> list[Event]:
    async with ctx.admin_db.session() as session:
        return list(
            (await session.execute(select(Event).where(Event.execution_id == execution_id)))
            .scalars()
            .all()
        )


async def _wait_for_records(ctx: Context, execution_id: str) -> list[ExecutionRecord]:
    """Poll briefly: the record is written after the response body has ended."""
    for _ in range(100):
        records = await _records(ctx, execution_id)
        if records:
            return records
        await asyncio.sleep(0.05)
    return []


def _unhandled(served: _Served) -> list[str]:
    """``unhandled_exception`` events plus every error-level server log line."""
    structured = [e["event"] for e in served.logs if e["event"] == "unhandled_exception"]
    server = [r.getMessage() for r in served.server_log.records if r.levelno >= logging.ERROR]
    return structured + server


async def test_completed_stream_persists_one_completed_record(
    integration_context: Context, served: _Served, execution_ids: list[str]
) -> None:
    async with httpx.AsyncClient(base_url=served.base_url) as client:
        resp = await client.get("/execute/ok")
    execution_id = resp.headers[JenticHeader.EXECUTION_ID.value]
    execution_ids.append(execution_id)

    assert resp.status_code == 200
    assert resp.content == _FIRST_CHUNK + b"done"
    records = await _wait_for_records(integration_context, execution_id)
    assert len(records) == 1
    assert records[0].status == ExecutionStatus.COMPLETED
    assert records[0].http_status == 200
    assert records[0].error is None
    events = await _events(integration_context, execution_id)
    assert [e.type for e in events] == [EventType.EXECUTION_COMPLETED]
    assert _unhandled(served) == []


async def test_midbody_upstream_drop_persists_one_failed_record(
    integration_context: Context, served: _Served, execution_ids: list[str]
) -> None:
    async with (
        httpx.AsyncClient(base_url=served.base_url) as client,
        client.stream("GET", "/execute/drop") as resp,
    ):
        execution_id = resp.headers[JenticHeader.EXECUTION_ID.value]
        request_id = resp.headers["x-request-id"]
        execution_ids.append(execution_id)
        assert resp.status_code == 200
        received = b""
        # The broker must abort the connection, not end the body cleanly:
        # the client has to be able to tell the response is incomplete.
        with pytest.raises(httpx.RemoteProtocolError):
            async for chunk in resp.aiter_raw():
                received += chunk
    assert received == _FIRST_CHUNK

    records = await _wait_for_records(integration_context, execution_id)
    assert len(records) == 1
    assert records[0].status == ExecutionStatus.FAILED
    assert records[0].http_status == 200
    assert records[0].error == "upstream_error: RemoteProtocolError"
    events = await _events(integration_context, execution_id)
    assert [e.type for e in events] == [EventType.EXECUTION_FAILED]

    failures = [e for e in served.logs if e["event"] == "upstream_network_failure"]
    assert len(failures) == 1
    failure = failures[0]
    assert failure["request_id"] == request_id
    assert failure["trace_id"] == _TRACE_ID
    assert failure["execution_id"] == execution_id
    assert failure["host"] == "127.0.0.1"
    assert failure["error_type"] == "RemoteProtocolError"
    assert failure["bytes_transferred"] == len(_FIRST_CHUNK)
    assert "exc_info" not in failure
    assert _unhandled(served) == []


@pytest.mark.parametrize("served", [len(_FIRST_CHUNK) + 1], indirect=True)
async def test_size_cap_exceeded_midbody_persists_one_failed_record(
    integration_context: Context, served: _Served, execution_ids: list[str]
) -> None:
    async with (
        httpx.AsyncClient(base_url=served.base_url) as client,
        client.stream("GET", "/execute/ok") as resp,
    ):
        execution_id = resp.headers[JenticHeader.EXECUTION_ID.value]
        execution_ids.append(execution_id)
        with pytest.raises(httpx.RemoteProtocolError):
            async for _chunk in resp.aiter_raw():
                pass

    records = await _wait_for_records(integration_context, execution_id)
    assert len(records) == 1
    assert records[0].status == ExecutionStatus.FAILED
    assert records[0].error == "upstream_error: UpstreamResponseTooLargeError"
    assert len(await _events(integration_context, execution_id)) == 1
    aborts = [e for e in served.logs if e["event"] == "streaming_response_aborted"]
    assert len(aborts) == 1
    assert aborts[0]["reason"] == "upstream_response_too_large"
    assert aborts[0]["execution_id"] == execution_id
    assert "request_id" in aborts[0]
    assert _unhandled(served) == []


async def test_client_disconnect_midbody_persists_one_record(
    integration_context: Context, served: _Served, execution_ids: list[str]
) -> None:
    async with (
        httpx.AsyncClient(base_url=served.base_url) as client,
        client.stream("GET", "/execute/hang") as resp,
    ):
        execution_id = resp.headers[JenticHeader.EXECUTION_ID.value]
        execution_ids.append(execution_id)
        async for _chunk in resp.aiter_raw():
            break
    # Leaving the block closes the client connection mid-body.

    records = await _wait_for_records(integration_context, execution_id)
    assert len(records) == 1
    assert records[0].status == ExecutionStatus.FAILED
    assert records[0].error == "client_disconnect"
    assert len(await _events(integration_context, execution_id)) == 1
    assert _unhandled(served) == []


async def test_persist_failure_is_counted_and_never_escapes(
    integration_context: Context, served: _Served, execution_ids: list[str]
) -> None:
    """A real DB failure (duplicate execution id) is logged and counted only."""
    execution_id = "exec_pr1712duplicateid0001"
    execution_ids.append(execution_id)
    with (
        patch.object(execute_router, "mint_execution_id", return_value=execution_id),
        patch.object(execute_router._streaming_persist_failures, "add") as failures,
    ):
        async with httpx.AsyncClient(base_url=served.base_url) as client:
            first = await client.get("/execute/ok")
            assert await _wait_for_records(integration_context, execution_id)
            second = await client.get("/execute/ok")
        for _ in range(100):
            if failures.called:
                break
            await asyncio.sleep(0.05)

    assert first.status_code == second.status_code == 200
    assert second.content == _FIRST_CHUNK + b"done"
    failures.assert_called_once_with(1)
    assert len(await _records(integration_context, execution_id)) == 1
    assert [e["event"] for e in served.logs].count("streaming_execution_persist_failed") == 1
    assert _unhandled(served) == []
