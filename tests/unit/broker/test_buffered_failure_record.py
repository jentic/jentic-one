"""Buffered-path failure records: unmapped errors and the stored duration.

* An exception the broker does not map (the caller gets a 500) counts one
  FAILED execution and is handed to the recorder as ``server_error:
  <ExceptionClass>`` with its elapsed time, then re-raised unchanged; a
  recorder that raises never masks it.
* The sync router's recorder only schedules the write on the app's
  background-task registry, so the 500 never waits on the database.
* A ``BrokerError`` record stores the elapsed time as ``duration_ms``, as a
  stream that fails to open does.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog
from starlette.applications import Starlette
from starlette.requests import Request

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.exceptions import UpstreamTimeoutError
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution import service as service_module
from jentic_one.broker.services.execution.service import (
    UnmappedFailure,
    default_broker,
    run_execution,
)
from jentic_one.broker.web.routers import execute as execute_router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType, ExecutionStatus
from jentic_one.shared.schemas import OperationInfo
from jentic_one.shared.web.background_tasks import install_background_tasks

_DELAY_S = 0.03


class _UnmappedError(RuntimeError):
    """An exception no broker handler maps."""


class _UnmappedRunner(UpstreamRunner):
    async def run(self, request: RunnerRequest) -> RunnerResult:
        await asyncio.sleep(_DELAY_S)
        raise _UnmappedError("token=secret-material")


class _SlowTimeoutRunner(UpstreamRunner):
    async def run(self, request: RunnerRequest) -> RunnerResult:
        await asyncio.sleep(_DELAY_S)
        raise UpstreamTimeoutError(detail="Upstream timed out", type="upstream_timeout")


def _ctx_req() -> ExecuteRequestContext:
    return ExecuteRequestContext(
        upstream_url="https://api.example.com/v1/things",
        method="GET",
        trace_id="a" * 32,
        operation=OperationInfo(id="getThing"),
        prefer=None,
        pinned_revisions=None,
    )


async def _run(runner: UpstreamRunner, **kwargs: Any) -> None:
    await run_execution(
        _ctx_req(),
        body=None,
        headers=None,
        session=AsyncMock(),
        broker=default_broker(runner),
        execution_id="exec_buffered_unmapped",
        actor_id="agt_abc123",
        actor_type="agent",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_unmapped_failure_is_counted_handed_off_and_reraised() -> None:
    failures: list[UnmappedFailure] = []

    async def _recorder(failure: UnmappedFailure) -> None:
        failures.append(failure)

    with (
        patch.object(service_module, "record_execution", new_callable=AsyncMock) as inline,
        patch.object(service_module._executions_total, "add") as count,
        patch.object(service_module._execution_duration, "record") as duration,
        pytest.raises(_UnmappedError, match="secret-material"),
    ):
        await _run(_UnmappedRunner(), on_unmapped_failure=_recorder)

    (failure,) = failures
    assert failure.execution_id == "exec_buffered_unmapped"
    assert failure.error == "server_error: _UnmappedError"
    assert failure.duration_ms >= int(_DELAY_S * 1000) // 2
    # The record is the recorder's to write, never inline on the request session.
    inline.assert_not_awaited()
    count.assert_called_once_with(1, {"operation": "getThing", "status": ExecutionStatus.FAILED})
    duration.assert_not_called()


@pytest.mark.asyncio
async def test_a_failing_recorder_never_masks_the_cause() -> None:
    async def _recorder(failure: UnmappedFailure) -> None:
        raise ConnectionError("admin db down")

    with structlog.testing.capture_logs() as logs, pytest.raises(_UnmappedError):
        await _run(_UnmappedRunner(), on_unmapped_failure=_recorder)

    (logged,) = [e for e in logs if e["event"] == "unmapped_failure_record_failed"]
    assert logged["execution_id"] == "exec_buffered_unmapped"
    assert logged["error_type"] == "_UnmappedError"


@pytest.mark.asyncio
async def test_without_a_recorder_the_failure_is_counted_and_reraised() -> None:
    with (
        patch.object(service_module, "record_execution", new_callable=AsyncMock) as inline,
        patch.object(service_module._executions_total, "add") as count,
        pytest.raises(_UnmappedError),
    ):
        await _run(_UnmappedRunner())

    inline.assert_not_awaited()
    count.assert_called_once()


@pytest.mark.asyncio
async def test_broker_error_record_stores_the_elapsed_duration() -> None:
    with (
        patch.object(service_module, "record_execution", new_callable=AsyncMock) as record,
        patch.object(service_module, "emit_event", new_callable=AsyncMock),
        pytest.raises(UpstreamTimeoutError),
    ):
        await _run(_SlowTimeoutRunner())

    record.assert_awaited_once()
    assert record.call_args.kwargs["error"] == "upstream_timeout"
    assert record.call_args.kwargs["http_status"] is None
    assert record.call_args.kwargs["duration_ms"] >= int(_DELAY_S * 1000) // 2


@pytest.mark.asyncio
async def test_router_recorder_schedules_the_write_and_returns() -> None:
    """The recorder spawns the write on the registry; it does not await it."""
    app = Starlette()
    registry = install_background_tasks(app)
    request = Request({"type": "http", "app": app})
    ctx = MagicMock()
    ctx.config.broker.streaming_persist_timeout_s = 5.0
    identity = Identity(
        sub="agt_abc123", actor_type=ActorType.AGENT, permissions=["execute"], active=True
    )
    release = asyncio.Event()
    written: list[str] = []

    async def _slow_persist(failure: UnmappedFailure, *args: Any, **kwargs: Any) -> None:
        await release.wait()
        written.append(failure.execution_id)

    recorder = execute_router._unmapped_failure_recorder(request, ctx, _ctx_req(), identity)
    failure = UnmappedFailure(
        execution_id="exec_buffered_unmapped",
        started_at=MagicMock(),
        duration_ms=12,
        error="server_error: _UnmappedError",
    )
    with patch.object(execute_router, "_persist_unmapped_failure", _slow_persist):
        async with asyncio.timeout(1):
            await recorder(failure)
        assert written == []
        assert registry.pending == 1
        release.set()
        await registry.drain(5)

    assert written == ["exec_buffered_unmapped"]
