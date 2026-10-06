"""Integration test: the streaming execute path frames the outbound request itself.

A caller may send a body (and its ``Content-Length``) on a GET. The broker does
not forward a body on body-less methods, so a forwarded inbound length would
declare bytes that never arrive and the HTTP client would reject the request
before it reached the upstream. This drives the router's streaming handler
against a real local HTTP upstream over a real socket, so httpx's wire-level
framing checks apply, and persists the execution record to the real admin DB.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.headers import JenticHeader
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.web.routers.execute import _handle_streaming
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.schemas import OperationInfo

pytestmark = pytest.mark.integration

_UPSTREAM_STATUS = 418
_UPSTREAM_BODY = b"teapot"

_IDENTITY = Identity(
    sub="agnt_framing_test",
    actor_type=ActorType.AGENT,
    permissions=["execute"],
    active=True,
)


@dataclass
class _Upstream:
    """A minimal HTTP/1.1 upstream on a real socket that records request heads."""

    port: int = 0
    request_heads: list[dict[str, str]] = field(default_factory=list)


@pytest.fixture()
async def upstream() -> AsyncGenerator[_Upstream, None]:
    state = _Upstream()

    async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")[1:]
        state.request_heads.append(
            {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines if ln)}
        )
        writer.write(
            f"HTTP/1.1 {_UPSTREAM_STATUS} I'm a teapot\r\n"
            "Content-Type: text/plain\r\n"
            f"Content-Length: {len(_UPSTREAM_BODY)}\r\n"
            "Connection: close\r\n\r\n".encode("latin-1")
            + _UPSTREAM_BODY
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(_serve, "127.0.0.1", 0)
    state.port = server.sockets[0].getsockname()[1]
    try:
        yield state
    finally:
        server.close()
        await server.wait_closed()


def _app(ctx: Context, runner: HttpRunner, upstream_url: str) -> FastAPI:
    app = FastAPI()

    @app.get("/execute")
    async def execute(request: Request) -> Response:
        ctx_req = ExecuteRequestContext(
            upstream_url=upstream_url,
            method=request.method,
            trace_id="0af7651916cd43dd8448eb211c80319c",
            operation=OperationInfo(id="getThing", path="/thing", method="GET"),
        )
        return await _handle_streaming(
            request,
            ctx_req,
            ctx,
            _IDENTITY,
            runner,
            ctx.config.broker.resilience.upstream,
        )

    return app


async def test_streaming_get_with_body_returns_upstream_status(
    integration_context: Context, upstream: _Upstream
) -> None:
    """A GET carrying a body reaches the upstream without a stale ``Content-Length``."""
    upstream_url = f"http://127.0.0.1:{upstream.port}/thing"
    execution_id: str | None = None
    async with httpx.AsyncClient() as upstream_client:
        app = _app(integration_context, HttpRunner(upstream_client), upstream_url)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
            try:
                resp = await client.request(
                    "GET",
                    "/execute",
                    content=b'{"ignored": true}',
                    headers={"Content-Type": "application/json"},
                )
                execution_id = resp.headers.get(JenticHeader.EXECUTION_ID.value)

                assert resp.status_code == _UPSTREAM_STATUS
                assert resp.content == _UPSTREAM_BODY
                assert resp.headers[JenticHeader.UPSTREAM_STATUS.value] == str(_UPSTREAM_STATUS)
                assert len(upstream.request_heads) == 1
                assert "content-length" not in upstream.request_heads[0]

                async with integration_context.admin_db.session() as session:
                    record = (
                        await session.execute(
                            select(ExecutionRecord).where(ExecutionRecord.id == execution_id)
                        )
                    ).scalar_one()
                assert record.http_status == _UPSTREAM_STATUS
            finally:
                if execution_id is not None:
                    async with integration_context.admin_db.transaction() as session:
                        await session.execute(
                            delete(ExecutionRecord).where(ExecutionRecord.id == execution_id)
                        )
