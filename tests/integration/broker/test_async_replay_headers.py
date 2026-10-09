"""Integration test: an async execution replays the caller's body headers.

Drives the router's async branch (``_handle_async``) with a real Context and
admin DB, then runs the stored job through the worker's ``ExecutionHandler``
and the broker's ``PipelineExecutor`` against a real local HTTP upstream. The
JSON body must arrive with its ``Content-Type`` and the vendor version header,
while the caller's ``Authorization`` and ``Cookie`` never reach the job payload.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from sqlalchemy import delete
from starlette.requests import Request

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.adapters.runners.registry import RunnerRegistry
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution.executor import PipelineExecutor
from jentic_one.broker.web.routers.execute import _handle_async
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import EgressConfig
from jentic_one.shared.context import Context
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.models import ActorType

pytestmark = pytest.mark.integration

_AGENT = Identity(sub="agnt_replayhdr", actor_type=ActorType.AGENT, permissions=[])
_BODY = b'{"parent": {"page_id": "p1"}}'


@dataclass
class _Upstream:
    """A minimal HTTP/1.1 upstream on a real socket that records requests."""

    port: int = 0
    heads: list[dict[str, str]] = field(default_factory=list)
    bodies: list[bytes] = field(default_factory=list)


@pytest.fixture()
async def upstream() -> AsyncGenerator[_Upstream, None]:
    state = _Upstream()

    async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")[1:]
        headers = {
            k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines if ln)
        }
        state.heads.append(headers)
        state.bodies.append(await reader.readexactly(int(headers.get("content-length", "0"))))
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: 2\r\nConnection: close\r\n\r\n{}"
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


@pytest.fixture()
async def clean(integration_context: Context) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(delete(Job).where(Job.created_by == _AGENT.sub))
            await session.execute(
                delete(ExecutionRecord).where(ExecutionRecord.actor_id == _AGENT.sub)
            )

    await _truncate()
    yield
    await _truncate()


def _request(body: bytes) -> Request:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "server": ("broker.local", 8080),
        "path": "/v1/pages",
        "raw_path": b"/v1/pages",
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"notion-version", b"2022-06-28"),
            (b"host", b"broker.local:8080"),
            (b"authorization", b"Bearer agent-token"),
            (b"cookie", b"sid=agent-session"),
            (b"prefer", b"respond-async"),
        ],
    }
    return Request(scope, receive)


async def test_async_post_reaches_upstream_with_content_and_version_headers(
    integration_context: Context, upstream: _Upstream, clean: None
) -> None:
    ctx = integration_context
    ctx_req = ExecuteRequestContext(
        upstream_url=f"http://127.0.0.1:{upstream.port}/v1/pages",
        method="POST",
        trace_id="c" * 32,
        api_vendor="notion.com",
        api_name="notion",
        api_version="1",
    )

    response = await _handle_async(_request(_BODY), ctx_req, ctx, _AGENT)
    assert response.status_code == 202
    job_id = json.loads(bytes(response.body))["job_id"]

    async with ctx.admin_db.session() as session:
        job = await session.get(Job, job_id)
    assert job is not None
    payload = job.payload or {}
    assert payload["headers"] == {
        "content-type": "application/json",
        "notion-version": "2022-06-28",
    }
    stored = json.dumps(payload)
    assert "agent-token" not in stored
    assert "agent-session" not in stored

    async with httpx.AsyncClient() as client:
        registry = RunnerRegistry()
        registry.register(["http", "https"], HttpRunner(client), required=True)
        handler = ExecutionHandler(
            executor=PipelineExecutor(registry),
            # The test upstream listens on loopback, which the default policy blocks.
            egress=EgressConfig(allowed_private_subnets=["127.0.0.0/8"]),
        )
        async with ctx.admin_db.transaction() as session:
            result = await handler.execute(
                job_id,
                session,
                payload=payload,
                created_by=_AGENT.sub,
                actor_type=_AGENT.actor_type.value,
            )

    assert result.body["status"] == "completed"
    assert result.body["http_status"] == 200
    assert len(upstream.heads) == 1
    head = upstream.heads[0]
    assert head["content-type"] == "application/json"
    assert head["notion-version"] == "2022-06-28"
    assert "authorization" not in head
    assert "cookie" not in head
    assert upstream.bodies[0] == _BODY
