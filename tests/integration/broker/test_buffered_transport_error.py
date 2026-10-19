"""Integration test: a buffered upstream transport failure answers 502 (#1706).

Drives the buffered execution service (``run_execution``) over a real socket
against an upstream that drops the connection partway through the response
body, against a port nothing listens on, and — through the production client
stack (``build_client`` with DNS pinning) — against a host that does not
resolve at connect time, then renders the raised error through the broker's own
problem+json handlers. The broker answers ``502 Bad Gateway`` and still
persists the FAILED execution record to the real admin DB.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncGenerator, Callable

import httpx
import pytest
from fastapi import FastAPI, Response
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.broker.adapters.http_client import build_client
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.execution import mint_execution_id
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution.service import default_broker, run_execution
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.shared import dns_cache
from jentic_one.shared.config import EgressConfig, UpstreamClientConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.schemas import OperationInfo

pytestmark = pytest.mark.integration


@pytest.fixture()
async def dropping_upstream() -> AsyncGenerator[str, None]:
    """An HTTP/1.1 upstream that promises 64 body bytes, sends 8, then hangs up."""

    async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 64\r\n\r\n"
            b'{"part":'
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(_serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/thing"
    finally:
        server.close()
        await server.wait_closed()


@pytest.fixture()
def refused_upstream() -> str:
    """A loopback URL on a port that was just released, so connecting is refused."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/thing"


@pytest.fixture()
def unresolvable_upstream(monkeypatch: pytest.MonkeyPatch) -> str:
    """An upstream name the pinning transport's resolver cannot resolve at connect time.

    Only the egress resolver is swapped (not ``socket.getaddrinfo``), so the
    database connections keep resolving normally.
    """

    def _nxdomain(_host: str) -> tuple[()]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    dns_cache.clear_resolution_cache()
    monkeypatch.setattr(dns_cache, "resolve_host_blocking", _nxdomain)
    return "http://upstream-vanished.example.com/thing"


def _app(ctx: Context, upstream_client: httpx.AsyncClient, upstream_url: str) -> FastAPI:
    app = FastAPI()
    install_broker_error_handlers(app)

    @app.post("/execute/{execution_id}")
    async def execute(execution_id: str) -> Response:
        ctx_req = ExecuteRequestContext(
            upstream_url=upstream_url,
            method="POST",
            trace_id="0af7651916cd43dd8448eb211c80319c",
            operation=OperationInfo(id="postThing", path="/thing", method="POST"),
        )
        outcome = await run_execution(
            ctx_req,
            body=b"{}",
            headers={"content-type": "application/json"},
            admin_db=ctx.admin_db,
            timeout=5.0,
            broker=default_broker(HttpRunner(upstream_client)),
            execution_id=execution_id,
            actor_id="agnt_transport_test",
            actor_type="agent",
        )
        return Response(content=outcome.result.body, status_code=outcome.result.status_code)

    return app


async def _execute_and_fetch_record(
    ctx: Context,
    upstream_url: str,
    upstream_client_factory: Callable[[], httpx.AsyncClient] = httpx.AsyncClient,
) -> tuple[httpx.Response, ExecutionRecord]:
    execution_id = mint_execution_id()
    try:
        async with upstream_client_factory() as upstream_client:
            app = _app(ctx, upstream_client, upstream_url)
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
                resp = await client.post(f"/execute/{execution_id}")
        async with ctx.admin_db.session() as session:
            record = (
                await session.execute(
                    select(ExecutionRecord).where(ExecutionRecord.id == execution_id)
                )
            ).scalar_one()
        return resp, record
    finally:
        async with ctx.admin_db.transaction() as session:
            await session.execute(delete(ExecutionRecord).where(ExecutionRecord.id == execution_id))


async def test_mid_body_disconnect_answers_502_and_records_failure(
    integration_context: Context, dropping_upstream: str
) -> None:
    resp, record = await _execute_and_fetch_record(integration_context, dropping_upstream)

    assert resp.status_code == 502
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.headers["jentic-error-origin"] == "broker"
    body = json.loads(resp.content)
    assert body["status"] == 502
    assert body["type"] == "upstream_transport_error"
    assert body["detail"] == "Upstream transport error (RemoteProtocolError)"
    assert body["error_origin"] == "broker"

    assert record.status == ExecutionStatus.FAILED
    assert record.error == "Upstream transport error (RemoteProtocolError)"
    # No upstream answer exists, so no upstream status is recorded.
    assert record.http_status is None


async def test_refused_connection_answers_502_and_records_failure(
    integration_context: Context, refused_upstream: str
) -> None:
    resp, record = await _execute_and_fetch_record(integration_context, refused_upstream)

    assert resp.status_code == 502
    body = json.loads(resp.content)
    assert body["status"] == 502
    assert body["type"] == "upstream_connect_failed"
    assert body["error_origin"] == "broker"

    assert record.status == ExecutionStatus.FAILED
    assert record.error == "The upstream connection could not be established."
    assert record.http_status is None


async def test_unresolvable_host_under_dns_pinning_answers_502_and_records_failure(
    integration_context: Context, unresolvable_upstream: str
) -> None:
    egress = EgressConfig()
    assert egress.dns_pinning_enabled

    resp, record = await _execute_and_fetch_record(
        integration_context,
        unresolvable_upstream,
        lambda: build_client(UpstreamClientConfig(), egress),
    )

    assert resp.status_code == 502
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.headers["jentic-error-origin"] == "broker"
    body = json.loads(resp.content)
    assert body["status"] == 502
    assert body["type"] == "upstream_connect_failed"
    assert body["error_origin"] == "broker"
    assert "upstream-vanished" not in json.dumps(body)

    assert record.status == ExecutionStatus.FAILED
    assert record.error == "The upstream connection could not be established."
    assert record.http_status is None
