"""Buffered-path failure records: unmapped errors and the stored duration.

* An exception the broker does not map (the caller gets a 500) counts one
  FAILED execution and is handed to the recorder as ``server_error:
  <ExceptionClass>`` with its elapsed time, then re-raised unchanged; a
  recorder that raises never masks it.
* The sync router's recorder only schedules the write on the app's
  background-task registry, so the 500 never waits on the database; without
  a registry it skips the record rather than write inside the router's open
  transaction.
* Only an exception the app answers with a 500 is classified: one an app
  handler maps to a 4xx or a 503 is re-raised untouched. The classifier is
  checked against the real broker app, whose handler set is pinned.
* A lost unmapped-failure record is counted as ``failed`` on
  ``app.background_tasks``, never as ``completed``.
* A ``BrokerError`` record stores the elapsed time as ``duration_ms``, as a
  stream that fails to open does.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError, WebSocketRequestValidationError
from jentic.problem_details import ProblemDetailException
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from starlette.applications import Starlette
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from jentic_one.__main__ import _expand_allowed_dbs
from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.exceptions import BrokerError, UpstreamTimeoutError
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution import service as service_module
from jentic_one.broker.services.execution.service import (
    UnmappedFailure,
    default_broker,
    run_execution,
)
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.broker.web.routers import execute as execute_router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseUnavailableError
from jentic_one.shared.models import ActorType, ExecutionStatus
from jentic_one.shared.schemas import OperationInfo
from jentic_one.shared.state.backend import StateBackendCapacityError
from jentic_one.shared.web import background_tasks as background_tasks_module
from jentic_one.shared.web.background_tasks import install_background_tasks
from tests.unit.fake_tx import SessionScope

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
        admin_db=SessionScope(AsyncMock()),
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


def _identity() -> Identity:
    return Identity(
        sub="agt_abc123", actor_type=ActorType.AGENT, permissions=["execute"], active=True
    )


def _failure() -> UnmappedFailure:
    return UnmappedFailure(
        execution_id="exec_buffered_unmapped",
        started_at=MagicMock(),
        duration_ms=12,
        error="server_error: _UnmappedError",
    )


@pytest.mark.asyncio
async def test_router_recorder_without_a_registry_skips_the_write() -> None:
    """No inline write: the 500 never waits on the admin database."""
    request = Request({"type": "http", "app": Starlette()})
    ctx = MagicMock()
    ctx.config.broker.streaming_persist_timeout_s = 5.0
    persist = AsyncMock()

    recorder = execute_router._unmapped_failure_recorder(request, ctx, _ctx_req(), _identity())
    with (
        patch.object(execute_router, "_persist_unmapped_failure", persist),
        structlog.testing.capture_logs() as logs,
    ):
        await recorder(_failure())

    persist.assert_not_called()
    ctx.admin_db.transaction.assert_not_called()
    (skipped,) = [e for e in logs if e["event"] == "unmapped_failure_record_skipped"]
    assert skipped["log_level"] == "warning"
    assert skipped["execution_id"] == "exec_buffered_unmapped"
    assert skipped["trace_id"] == "a" * 32


class _UnavailableError(RuntimeError):
    """Stands in for an exception an app handler answers with a 503."""


async def _handle_unavailable(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse({"status": 503}, status_code=503)


@pytest.fixture()
def broker_app(sample_config_dict: dict[str, Any]) -> Iterator[FastAPI]:
    """The real standalone broker app (never started, so no DB is reached)."""
    app_config = AppConfig.model_validate(sample_config_dict)
    ctx = Context(app_config, allowed_dbs=_expand_allowed_dbs(["broker"], app_config))
    app = create_broker_app(ctx)
    try:
        yield app
    finally:
        FastAPIInstrumentor.uninstrument_app(app)


def test_broker_app_handler_set_is_the_one_the_classifier_was_checked_against(
    broker_app: FastAPI,
) -> None:
    """A new handler on the broker app must be checked against the classifier.

    ``_server_error_classifier`` reads a dedicated handler's status from
    ``exc.status_code``; a handler that answers 500 for an exception without
    one would classify as not-a-500 and drop the count and the record.
    """
    assert set(broker_app.exception_handlers) == {
        Exception,
        StarletteHTTPException,
        RequestValidationError,
        WebSocketRequestValidationError,
        BrokerError,
        ProblemDetailException,
        DatabaseUnavailableError,
        StateBackendCapacityError,
    }


@pytest.mark.parametrize(
    "exc",
    [DatabaseUnavailableError("db down"), StateBackendCapacityError("idem", retry_after_s=1)],
)
def test_broker_apps_503_handlers_never_classify_as_server_errors(
    broker_app: FastAPI, exc: Exception
) -> None:
    # Both dedicated handlers answer 503, and neither exception carries a
    # ``status_code`` the classifier could misread as 500.
    assert execute_router._server_error_classifier(broker_app)(exc) is False


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_UnmappedError("boom"), True),
        (ProblemDetailException(status_code=500, detail="boom"), True),
        (StarletteHTTPException(status_code=500), True),
        (ProblemDetailException(status_code=404, detail="missing"), False),
        (StarletteHTTPException(status_code=409), False),
        (_UnavailableError("db down"), False),
    ],
)
def test_server_error_classifier_follows_the_apps_handlers(
    broker_app: FastAPI, exc: Exception, expected: bool
) -> None:
    # Stands in for #1727's 503 handler for an unavailable database.
    broker_app.add_exception_handler(_UnavailableError, _handle_unavailable)
    assert execute_router._server_error_classifier(broker_app)(exc) is expected


class _MappedRunner(UpstreamRunner):
    async def run(self, request: RunnerRequest) -> RunnerResult:
        raise ProblemDetailException(status_code=404, detail="missing")


@pytest.mark.asyncio
async def test_a_handler_mapped_exception_is_reraised_untouched(broker_app: FastAPI) -> None:
    recorder = AsyncMock()
    with (
        patch.object(service_module._executions_total, "add") as count,
        structlog.testing.capture_logs() as logs,
        pytest.raises(ProblemDetailException),
    ):
        await _run(
            _MappedRunner(),
            on_unmapped_failure=recorder,
            is_server_error=execute_router._server_error_classifier(broker_app),
        )

    recorder.assert_not_awaited()
    count.assert_not_called()
    assert not [e for e in logs if e["event"] == "execution_failed"]


@pytest.mark.asyncio
async def test_a_write_given_up_before_the_transaction_counts_as_failed() -> None:
    """``outcome=completed`` on ``kind=unmapped_failure_persist`` means the record landed.

    Anything that keeps the record from being written (here the admin
    transaction refusing to open) raises out of ``_persist_unmapped_failure``,
    so the registry counts it as ``failed``, the lost-record signal
    ``docs/operations/monitoring.md`` documents for this kind.
    """
    app = Starlette()
    registry = install_background_tasks(app)
    request = Request({"type": "http", "app": app})
    ctx = MagicMock()
    ctx.config.broker.streaming_persist_timeout_s = 5.0

    def _refuse(_ctx: Any) -> Any:
        raise TimeoutError("no admin connection within the backlog budget")

    recorder = execute_router._unmapped_failure_recorder(request, ctx, _ctx_req(), _identity())
    with (
        patch.object(execute_router, "_off_request_admin_transaction", _refuse),
        patch.object(background_tasks_module._task_outcomes, "add") as outcomes,
        patch.object(execute_router, "persist_unmapped_failure", new_callable=AsyncMock) as write,
    ):
        await recorder(_failure())
        await registry.drain(5)

    write.assert_not_awaited()
    outcomes.assert_called_once_with(1, {"kind": "unmapped_failure_persist", "outcome": "failed"})
