"""Integration test: the retry gate sees a caller's ``Idempotency-Key`` (#1711).

Drives the buffered execution step (``run_execution``) through the real
``Deadline → Retry → HttpRunner`` envelope against a local HTTP upstream on a
real socket that answers ``503, 503, 200``, and persists the execution record to
the real admin DB. A keyed ``POST`` is status-retried to the ``200``; a key-less
``POST`` gets the first ``503`` verbatim; a ``GET`` is retried by method. The
``Idempotency-Key`` header is broker-consumed and never reaches the upstream.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

import httpx
import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.proxy_headers import forward_headers
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution.pipeline import build_runner
from jentic_one.broker.services.execution.service import default_broker, run_execution
from jentic_one.shared.config import RetryConfig
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration

_ACTOR = "agnt_retry_idem_key"
_STATUS_SEQUENCE = (503, 503, 200)


@dataclass
class _Upstream:
    """An HTTP/1.1 upstream on a real socket answering a fixed status sequence."""

    port: int = 0
    heads: list[dict[str, str]] = field(default_factory=list)


@pytest.fixture()
async def upstream() -> AsyncGenerator[_Upstream, None]:
    state = _Upstream()

    async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")[1:]
        headers = {
            k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines if ln)
        }
        await reader.readexactly(int(headers.get("content-length", "0")))
        status = _STATUS_SEQUENCE[min(len(state.heads), len(_STATUS_SEQUENCE) - 1)]
        state.heads.append(headers)
        body = f'{{"status": {status}}}'.encode()
        writer.write(
            f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("latin-1")
            + body
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
            await session.execute(delete(ExecutionRecord).where(ExecutionRecord.actor_id == _ACTOR))

    await _truncate()
    yield
    await _truncate()


@pytest.mark.parametrize(
    ("method", "keyed", "expected_status", "expected_attempts"),
    [
        pytest.param("POST", True, 200, 3, id="keyed-post-retried"),
        pytest.param("POST", False, 503, 1, id="keyless-post-not-retried"),
        pytest.param("GET", False, 200, 3, id="get-retried-by-method"),
    ],
)
async def test_status_retry_gate_honours_idempotency_key(
    integration_context: Context,
    upstream: _Upstream,
    clean: None,
    method: str,
    keyed: bool,
    expected_status: int,
    expected_attempts: int,
) -> None:
    inbound = {"Content-Type": "application/json", "Host": "broker.local"}
    if keyed:
        inbound["Idempotency-Key"] = "0b5c0d8e-1711-4c1e-9f00-000000001711"
    body = b'{"amount": 1}' if method == "POST" else None
    ctx_req = ExecuteRequestContext(
        upstream_url=f"http://127.0.0.1:{upstream.port}/charges",
        method=method,
        trace_id="d" * 32,
    )
    retry = RetryConfig(enabled=True, max_attempts=3, base_backoff_s=0.0, max_backoff_s=0.0)

    async with httpx.AsyncClient() as client:
        runner = build_runner(HttpRunner(client), deadline_s=10.0, retry=retry)
        outcome = await run_execution(
            ctx_req,
            body=body,
            headers=forward_headers(inbound, {}),
            admin_db=integration_context.admin_db,
            broker=default_broker(runner),
            actor_id=_ACTOR,
            actor_type="agent",
            has_idempotency_key=keyed,
        )

    assert outcome.result.status_code == expected_status
    assert len(upstream.heads) == expected_attempts
    assert all("idempotency-key" not in head for head in upstream.heads)
