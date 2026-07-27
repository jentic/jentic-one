"""Unit tests for streaming-path execution record persistence.

Proves that the streaming path attaches a BackgroundTask that persists an
execution record on success, client disconnect, and upstream error — and that
a persistence failure is logged but does not raise.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest, StreamingResult
from jentic_one.broker.core.exceptions import UpstreamTimeoutError
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.web.routers.execute import (
    _persist_streaming_outcome,
    _streaming_persist_failures,
)
from jentic_one.broker.web.streaming import (
    StreamingOutcome,
    guarded_body,
    open_streaming_response,
)
from jentic_one.shared.models import ExecutionStatus


def _ctx_req() -> ExecuteRequestContext:
    return ExecuteRequestContext(
        upstream_url="https://api.example.com/x",
        method="GET",
        trace_id="trace-1",
        toolkit_id="tk-1",
        operation_id="op-1",
        api_vendor="vendor",
        api_name="name",
        api_version="v1",
    )


@asynccontextmanager
async def _fake_stream_ctx(result: StreamingResult) -> AsyncIterator[StreamingResult]:
    yield result


async def _finite_iter(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


async def _slow_iter(chunks: list[bytes], delay_s: float) -> AsyncIterator[bytes]:
    for chunk in chunks:
        await asyncio.sleep(delay_s)
        yield chunk


# --- StreamingOutcome population tests ---


@pytest.mark.asyncio
async def test_outcome_success_full_body_consumed() -> None:
    chunks = [b"hello", b" ", b"world"]
    result = StreamingResult(
        status_code=200,
        headers={},
        content_type="text/plain",
        aiter=_finite_iter(chunks),
    )
    stack = AsyncExitStack()
    await stack.enter_async_context(_fake_stream_ctx(result))

    outcome = StreamingOutcome(execution_id="exec-1", http_status=200)
    body = guarded_body(result, stack, transfer_deadline_s=0, outcome=outcome)

    collected = b""
    async for chunk in body:
        collected += chunk

    assert collected == b"hello world"
    assert outcome.bytes_transferred == 11
    assert outcome.duration_ms >= 0
    assert outcome.error is None


@pytest.mark.asyncio
async def test_outcome_client_disconnect_sets_error() -> None:
    async def _endless() -> AsyncIterator[bytes]:
        while True:
            yield b"data"
            await asyncio.sleep(0.01)

    result = StreamingResult(status_code=200, headers={}, content_type=None, aiter=_endless())
    stack = AsyncExitStack()
    await stack.enter_async_context(_fake_stream_ctx(result))

    outcome = StreamingOutcome(execution_id="exec-2", http_status=200)
    body = guarded_body(result, stack, transfer_deadline_s=0, outcome=outcome)

    await body.__anext__()
    await body.aclose()

    assert outcome.error == "client_disconnect"
    assert outcome.duration_ms >= 0


@pytest.mark.asyncio
async def test_outcome_transfer_deadline_sets_error() -> None:
    result = StreamingResult(
        status_code=200,
        headers={},
        content_type=None,
        aiter=_slow_iter([b"a", b"b", b"c"], delay_s=0.05),
    )
    stack = AsyncExitStack()
    await stack.enter_async_context(_fake_stream_ctx(result))

    outcome = StreamingOutcome(execution_id="exec-3", http_status=200)
    body = guarded_body(result, stack, transfer_deadline_s=0.06, outcome=outcome)

    with pytest.raises(UpstreamTimeoutError):
        async for _ in body:
            pass

    assert outcome.error == "transfer_deadline_exceeded"
    assert outcome.duration_ms >= 0


@pytest.mark.asyncio
async def test_outcome_upstream_error_sets_error() -> None:
    async def _exploding() -> AsyncIterator[bytes]:
        yield b"partial"
        raise RuntimeError("upstream broke")

    result = StreamingResult(status_code=200, headers={}, content_type=None, aiter=_exploding())
    stack = AsyncExitStack()
    await stack.enter_async_context(_fake_stream_ctx(result))

    outcome = StreamingOutcome(execution_id="exec-4", http_status=200)
    body = guarded_body(result, stack, transfer_deadline_s=0, outcome=outcome)

    with pytest.raises(RuntimeError, match="upstream broke"):
        async for _ in body:
            pass

    assert outcome.error == "upstream_error: RuntimeError"
    assert outcome.bytes_transferred == 7


# --- open_streaming_response BackgroundTask tests ---


@pytest.mark.asyncio
async def test_attaches_background_task_when_callback_provided() -> None:
    result = StreamingResult(
        status_code=200,
        headers={"content-type": "text/plain"},
        content_type="text/plain",
        aiter=_finite_iter([b"ok"]),
    )

    class _Runner:
        def stream(self, _req: RunnerRequest) -> object:
            return _fake_stream_ctx(result)

    callback = AsyncMock()
    resp = await open_streaming_response(
        _Runner(),  # type: ignore[arg-type]
        RunnerRequest(method="GET", url="https://api.example.com/x"),
        _ctx_req(),
        "exec-bg-1",
        transfer_deadline_s=0,
        background_callback=callback,
    )

    assert resp.background is not None


@pytest.mark.asyncio
async def test_no_background_when_callback_is_none() -> None:
    result = StreamingResult(
        status_code=200,
        headers={},
        content_type="text/plain",
        aiter=_finite_iter([b"ok"]),
    )

    class _Runner:
        def stream(self, _req: RunnerRequest) -> object:
            return _fake_stream_ctx(result)

    resp = await open_streaming_response(
        _Runner(),  # type: ignore[arg-type]
        RunnerRequest(method="GET", url="https://api.example.com/x"),
        _ctx_req(),
        "exec-bg-2",
        transfer_deadline_s=0,
    )

    assert resp.background is None


@pytest.mark.asyncio
async def test_duration_includes_connect_and_ttfb() -> None:
    """Regression: `duration_ms` must cover the full round trip, not just body drain.

    The upstream connect + request send + response-header wait all happen inside
    `runner.stream()`; the outcome's perf anchor must predate it, otherwise real
    calls persist as ~0ms and the monitor's latency stats are garbage.
    """
    result = StreamingResult(
        status_code=200,
        headers={"content-type": "text/plain"},
        content_type="text/plain",
        aiter=_finite_iter([b"ok"]),
    )

    class _SlowOpenRunner:
        @asynccontextmanager
        async def stream(self, _req: RunnerRequest) -> AsyncIterator[StreamingResult]:
            await asyncio.sleep(0.05)  # simulated connect + TTFB
            yield result

    captured: list[StreamingOutcome] = []

    async def _callback(outcome: StreamingOutcome) -> None:
        captured.append(outcome)

    resp = await open_streaming_response(
        _SlowOpenRunner(),  # type: ignore[arg-type]
        RunnerRequest(method="GET", url="https://api.example.com/x"),
        _ctx_req(),
        "exec-dur-1",
        transfer_deadline_s=0,
        background_callback=_callback,
    )

    async for _ in resp.body_iterator:  # type: ignore[attr-defined]
        pass
    assert resp.background is not None
    await resp.background()

    assert len(captured) == 1
    assert captured[0].duration_ms >= 50


# --- Persist callback behaviour tests ---


@pytest.mark.asyncio
async def test_persist_callback_calls_persist_streaming_execution() -> None:
    """Exercise the real _persist_streaming_outcome with a successful outcome."""
    outcome = StreamingOutcome(execution_id="exec-p-1", http_status=200)
    outcome.duration_ms = 42
    outcome.bytes_transferred = 100

    mock_session = AsyncMock()
    mock_tx = AsyncMock()
    mock_tx.__aenter__.return_value = mock_session
    mock_tx.__aexit__.return_value = False
    mock_ctx = MagicMock()
    mock_ctx.admin_db.transaction.return_value = mock_tx

    started_at = datetime.now(UTC)

    with patch(
        "jentic_one.broker.web.routers.execute.persist_streaming_execution",
        new_callable=AsyncMock,
    ) as mock_persist:
        await _persist_streaming_outcome(
            outcome,
            ctx=mock_ctx,
            ctx_req=_ctx_req(),
            started_at=started_at,
            actor_id="agent-1",
            actor_type="agent",
        )

        mock_persist.assert_called_once_with(
            mock_session,
            execution_id="exec-p-1",
            started_at=started_at,
            status=ExecutionStatus.COMPLETED,
            http_status=200,
            duration_ms=42,
            error=None,
            ctx_req=_ctx_req(),
            actor_id="agent-1",
            actor_type="agent",
            origin=None,
            security_config=mock_ctx.config.security,
        )


@pytest.mark.asyncio
async def test_persist_callback_sets_failed_on_upstream_error() -> None:
    """Exercise the real _persist_streaming_outcome with an error outcome."""
    outcome = StreamingOutcome(execution_id="exec-p-3", http_status=502)
    outcome.duration_ms = 15
    outcome.error = None

    mock_session = AsyncMock()
    mock_tx = AsyncMock()
    mock_tx.__aenter__.return_value = mock_session
    mock_tx.__aexit__.return_value = False
    mock_ctx = MagicMock()
    mock_ctx.admin_db.transaction.return_value = mock_tx

    started_at = datetime.now(UTC)

    with patch(
        "jentic_one.broker.web.routers.execute.persist_streaming_execution",
        new_callable=AsyncMock,
    ) as mock_persist:
        await _persist_streaming_outcome(
            outcome,
            ctx=mock_ctx,
            ctx_req=_ctx_req(),
            started_at=started_at,
            actor_id="agent-1",
            actor_type="agent",
        )

        mock_persist.assert_called_once_with(
            mock_session,
            execution_id="exec-p-3",
            started_at=started_at,
            status=ExecutionStatus.FAILED,
            http_status=502,
            duration_ms=15,
            error="Upstream returned 502",
            ctx_req=_ctx_req(),
            actor_id="agent-1",
            actor_type="agent",
            origin=None,
            security_config=mock_ctx.config.security,
        )


@pytest.mark.asyncio
async def test_persist_failure_is_logged_not_raised() -> None:
    """A DB error during persistence must not propagate — it's best-effort."""
    outcome = StreamingOutcome(execution_id="exec-p-2", http_status=200)
    outcome.duration_ms = 10

    mock_ctx = MagicMock()
    mock_ctx.admin_db.transaction.side_effect = RuntimeError("DB down")

    with patch.object(_streaming_persist_failures, "add") as mock_counter:
        await _persist_streaming_outcome(
            outcome,
            ctx=mock_ctx,
            ctx_req=_ctx_req(),
            started_at=datetime.now(UTC),
            actor_id="agent-1",
            actor_type="agent",
        )
        mock_counter.assert_called_once_with(1)
