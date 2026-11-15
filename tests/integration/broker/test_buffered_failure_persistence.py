"""Integration tests for buffered execution failures and their audit records.

Failures originate in the runner or a post-response stage. The ASGI app uses
the broker's real exception handlers and background-task supervisor, and every
execution/event assertion reads the real admin database.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler
from sqlalchemy import delete, select
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from structlog.testing import capture_logs

import jentic_one.broker.services.execution.service as execution_service
import jentic_one.broker.web.routers.execute as execute_router
import jentic_one.shared.web.background_tasks as background_tasks_module
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.errors import UpstreamTimeoutError
from jentic_one.broker.core.execution import mint_execution_id
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.default_broker import DefaultBroker
from jentic_one.broker.services.execution.pipeline import (
    BrokerExecutionPipeline,
    ExecutionOutcome,
)
from jentic_one.broker.services.execution.service import (
    UnmappedFailure,
    default_broker,
    run_execution,
)
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import (
    DatabaseCommitOutcomeUnknownError,
    DatabaseUnavailableError,
)
from jentic_one.shared.discovery.errors import DiscoveryError, OperationNotFoundError
from jentic_one.shared.models import ActorType, ExecutionStatus
from jentic_one.shared.models.events import EventType
from jentic_one.shared.schemas import OperationInfo
from jentic_one.shared.state.backend import StateBackendCapacityError
from jentic_one.shared.web.background_tasks import (
    BackgroundTaskRegistry,
    install_background_tasks,
)
from jentic_one.shared.web.errors import (
    state_backend_capacity_error_handler,
    unhandled_exception_handler,
)

pytestmark = pytest.mark.integration

_ACTOR_ID = "agt_buffered_failure_test"
_IDENTITY = Identity(sub=_ACTOR_ID, actor_type=ActorType.AGENT, permissions=[])
_SECRET = "UPSTREAM-EXCEPTION-SECRET-9f27e2"  # pragma: allowlist secret
_REQUEST_SECRET = "REQUEST-SECRET-81b2"  # pragma: allowlist secret


class _SecretBearingError(Exception):
    pass


class _ExtensionError(Exception):
    status_code = 503


class _ExtensionServerError(Exception):
    pass


class _FailingRunner:
    def __init__(self, error: Exception, *, delay_s: float = 0.0) -> None:
        self.error = error
        self.delay_s = delay_s

    async def run(self, request: RunnerRequest) -> RunnerResult:
        await asyncio.sleep(self.delay_s)
        raise self.error


class _ResultRunner:
    def __init__(self, result: RunnerResult) -> None:
        self.result = result

    async def run(self, request: RunnerRequest) -> RunnerResult:
        return self.result


@dataclass(frozen=True)
class _Harness:
    app: FastAPI
    background_tasks: BackgroundTaskRegistry
    trace_id: str


def _ctx_req(trace_id: str) -> ExecuteRequestContext:
    return ExecuteRequestContext(
        upstream_url="https://api.example.com/things/concrete-id",
        method="GET",
        trace_id=trace_id,
        operation=OperationInfo(id="getThing", path="/things/{thingId}", method="GET"),
    )


def _app(ctx: Context, runner: UpstreamRunner, trace_id: str) -> _Harness:
    app = FastAPI()
    background_tasks = install_background_tasks(app)
    install_broker_error_handlers(app)
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StateBackendCapacityError, state_backend_capacity_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.state.broker = default_broker(runner)
    ctx_req = _ctx_req(trace_id)

    @app.get("/execute")
    async def execute(request: Request) -> Response:
        outcome = await execute_router._execute_buffered(
            request, ctx_req, ctx, _IDENTITY, runner, None
        )
        return execute_router._assemble_response(outcome, ctx_req)

    return _Harness(app, background_tasks, trace_id)


async def _get(harness: _Harness) -> httpx.Response:
    transport = httpx.ASGITransport(app=harness.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(
            "/execute?access_token=QUERY-SECRET-37aa",
            headers={"Authorization": f"Bearer {_REQUEST_SECRET}"},
        )


@pytest.fixture()
async def clean_records(integration_context: Context) -> AsyncGenerator[None, None]:
    async def _clean() -> None:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(delete(Event).where(Event.actor_id == _ACTOR_ID))
            await session.execute(
                delete(ExecutionRecord).where(ExecutionRecord.actor_id == _ACTOR_ID)
            )

    await _clean()
    yield
    await _clean()


async def _records(ctx: Context, trace_id: str) -> list[ExecutionRecord]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(ExecutionRecord).where(ExecutionRecord.trace_id == trace_id)
        )
        return list(result.scalars())


async def _events(ctx: Context, trace_id: str) -> list[Event]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(select(Event).where(Event.trace_id == trace_id))
        return list(result.scalars())


async def _wait_for_records(ctx: Context, trace_id: str) -> list[ExecutionRecord]:
    async with asyncio.timeout(3):
        while True:
            records = await _records(ctx, trace_id)
            if records:
                return records
            await asyncio.sleep(0.01)


def _assert_no_secret(response: httpx.Response, record: ExecutionRecord, event: Event) -> None:
    persisted = json.dumps(
        {
            "error": record.error,
            "summary": event.summary,
            "detail": event.detail,
            "data": event.data,
        }
    )
    for secret in (_SECRET, _REQUEST_SECRET, "QUERY-SECRET-37aa", "concrete-id"):
        assert secret not in response.text
        assert secret not in persisted


async def test_unmapped_failure_persists_once_with_duration_and_generic_500(
    integration_context: Context, clean_records: None
) -> None:
    trace_id = "1054" + "a" * 28
    harness = _app(
        integration_context,
        _FailingRunner(_SecretBearingError(_SECRET), delay_s=0.04),
        trace_id,
    )
    with (
        patch.object(
            execution_service._executions_total,
            "add",
            wraps=execution_service._executions_total.add,
        ) as executions,
        patch.object(execution_service._execution_duration, "record") as duration,
    ):
        response = await _get(harness)
        await harness.background_tasks.drain(3)

    assert response.status_code == 500
    assert response.json()["type"] == "server_error"
    assert response.json()["detail"] == "An unexpected error occurred."
    (record,) = await _records(integration_context, trace_id)
    (event,) = await _events(integration_context, trace_id)
    assert record.status == ExecutionStatus.FAILED
    assert record.http_status is None
    assert record.duration_ms is not None and 20 <= record.duration_ms < 5_000
    assert record.error == "server_error: _SecretBearingError"
    assert record.actor_id == _ACTOR_ID
    assert record.operation_path == "/things/{thingId}"
    assert event.type == EventType.EXECUTION_FAILED
    assert event.execution_id == record.id
    assert event.summary == "Execution failed: server_error: _SecretBearingError"
    executions.assert_called_once_with(
        1, {"operation": "getThing", "status": ExecutionStatus.FAILED}
    )
    duration.assert_not_called()
    _assert_no_secret(response, record, event)


async def test_discovery_error_500_fallback_persists_once_without_changing_response(
    integration_context: Context, clean_records: None
) -> None:
    trace_id = "1054" + "1" * 28
    harness = _app(
        integration_context,
        _FailingRunner(DiscoveryError("discovery failed"), delay_s=0.04),
        trace_id,
    )
    with (
        patch.object(
            execution_service._executions_total,
            "add",
            wraps=execution_service._executions_total.add,
        ) as executions,
        patch.object(execution_service._execution_duration, "record") as duration,
    ):
        response = await _get(harness)
        await harness.background_tasks.drain(3)

    assert response.status_code == 500
    assert response.json()["type"] == "about:blank"
    assert response.json()["status"] == 500
    assert response.json()["detail"] == "discovery failed"
    (record,) = await _records(integration_context, trace_id)
    (event,) = await _events(integration_context, trace_id)
    assert record.status == ExecutionStatus.FAILED
    assert record.http_status is None
    assert record.duration_ms is not None and 20 <= record.duration_ms < 5_000
    assert record.error == "server_error: DiscoveryError"
    assert event.type == EventType.EXECUTION_FAILED
    assert event.execution_id == record.id
    assert event.summary == "Execution failed: server_error: DiscoveryError"
    executions.assert_called_once_with(
        1, {"operation": "getThing", "status": ExecutionStatus.FAILED}
    )
    duration.assert_not_called()
    _assert_no_secret(response, record, event)


async def test_buffered_broker_error_has_elapsed_duration_and_one_metric(
    integration_context: Context, clean_records: None
) -> None:
    trace_id = "1054" + "b" * 28
    harness = _app(
        integration_context,
        _FailingRunner(UpstreamTimeoutError("Upstream timed out"), delay_s=0.04),
        trace_id,
    )
    with (
        patch.object(
            execution_service._executions_total,
            "add",
            wraps=execution_service._executions_total.add,
        ) as executions,
        patch.object(execution_service._execution_duration, "record") as duration,
    ):
        response = await _get(harness)

    assert response.status_code == 504
    assert response.json()["type"] == "upstream_timeout"
    assert harness.background_tasks.pending == 0
    (record,) = await _records(integration_context, trace_id)
    (event,) = await _events(integration_context, trace_id)
    assert record.status == ExecutionStatus.FAILED
    assert record.duration_ms is not None and 20 <= record.duration_ms < 5_000
    assert record.error == "Upstream timed out"
    assert event.type == EventType.EXECUTION_FAILED
    executions.assert_called_once_with(
        1, {"operation": "getThing", "status": ExecutionStatus.FAILED}
    )
    duration.assert_not_called()


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ProblemDetailException(status_code=409, detail="Conflict", type="conflict"), 409),
        (OperationNotFoundError("missing"), 404),
        (DatabaseUnavailableError("database detail"), 503),
        (DatabaseCommitOutcomeUnknownError("commit detail"), 503),
        (StateBackendCapacityError("idempotency", retry_after_s=1), 503),
        (RequestValidationError([]), 422),
        (HTTPException(status_code=409, detail="Conflict"), 409),
    ],
)
async def test_mapped_non_500_runner_errors_create_no_execution_evidence(
    integration_context: Context,
    clean_records: None,
    error: Exception,
    expected_status: int,
) -> None:
    trace_id = "1054" + "c" * 28
    harness = _app(integration_context, _FailingRunner(error), trace_id)
    with (
        capture_logs() as logs,
        patch.object(execution_service._executions_total, "add") as executions,
    ):
        response = await _get(harness)

    assert response.status_code == expected_status
    assert harness.background_tasks.pending == 0
    assert await _records(integration_context, trace_id) == []
    assert await _events(integration_context, trace_id) == []
    executions.assert_not_called()
    database_logs = [entry for entry in logs if entry["event"] == "broker_database_unavailable"]
    assert len(database_logs) == (1 if isinstance(error, DatabaseUnavailableError) else 0)
    capacity_logs = [entry for entry in logs if entry["event"] == "shared_state_write_refused"]
    assert len(capacity_logs) == (1 if isinstance(error, StateBackendCapacityError) else 0)


async def test_declared_non_500_extension_handler_runs_only_for_the_actual_response(
    integration_context: Context, clean_records: None
) -> None:
    trace_id = "1054" + "2" * 28
    harness = _app(integration_context, _FailingRunner(_ExtensionError()), trace_id)
    handler_calls = 0

    async def _handle_extension_error(_request: Request, _exc: _ExtensionError) -> Response:
        nonlocal handler_calls
        handler_calls += 1
        return Response(status_code=503)

    harness.app.add_exception_handler(_ExtensionError, _handle_extension_error)  # type: ignore[arg-type]
    with patch.object(execution_service._executions_total, "add") as executions:
        response = await _get(harness)

    assert response.status_code == 503
    assert handler_calls == 1
    assert harness.background_tasks.pending == 0
    assert await _records(integration_context, trace_id) == []
    assert await _events(integration_context, trace_id) == []
    executions.assert_not_called()


async def test_unknown_extension_status_defaults_to_one_audited_500(
    integration_context: Context, clean_records: None
) -> None:
    trace_id = "1054" + "3" * 28
    harness = _app(
        integration_context,
        _FailingRunner(_ExtensionServerError(), delay_s=0.04),
        trace_id,
    )
    handler_calls = 0

    async def _handle_extension_error(_request: Request, _exc: _ExtensionServerError) -> Response:
        nonlocal handler_calls
        handler_calls += 1
        return Response(status_code=500)

    harness.app.add_exception_handler(_ExtensionServerError, _handle_extension_error)  # type: ignore[arg-type]
    with (
        patch.object(
            execution_service._executions_total,
            "add",
            wraps=execution_service._executions_total.add,
        ) as executions,
        patch.object(execution_service._execution_duration, "record") as duration,
    ):
        response = await _get(harness)
        await harness.background_tasks.drain(3)

    assert response.status_code == 500
    assert handler_calls == 1
    (record,) = await _records(integration_context, trace_id)
    (event,) = await _events(integration_context, trace_id)
    assert record.status == ExecutionStatus.FAILED
    assert record.http_status is None
    assert record.duration_ms is not None and 20 <= record.duration_ms < 5_000
    assert record.error == "server_error: _ExtensionServerError"
    assert event.type == EventType.EXECUTION_FAILED
    assert event.execution_id == record.id
    executions.assert_called_once_with(
        1, {"operation": "getThing", "status": ExecutionStatus.FAILED}
    )
    duration.assert_not_called()


async def test_status_specific_handler_precedence_audits_one_actual_500(
    integration_context: Context, clean_records: None
) -> None:
    trace_id = "1054" + "4" * 28
    harness = _app(
        integration_context,
        _FailingRunner(HTTPException(status_code=404, detail=_SECRET)),
        trace_id,
    )
    handler_calls = 0

    async def _handle_not_found(_request: Request, _exc: HTTPException) -> Response:
        nonlocal handler_calls
        handler_calls += 1
        return Response(status_code=500)

    harness.app.add_exception_handler(404, _handle_not_found)  # type: ignore[arg-type]
    with (
        patch.object(
            execution_service._executions_total,
            "add",
            wraps=execution_service._executions_total.add,
        ) as executions,
        patch.object(execution_service._execution_duration, "record") as duration,
    ):
        response = await _get(harness)
        await harness.background_tasks.drain(3)

    assert response.status_code == 500
    assert handler_calls == 1
    (record,) = await _records(integration_context, trace_id)
    (event,) = await _events(integration_context, trace_id)
    assert record.status == ExecutionStatus.FAILED
    assert record.http_status is None
    assert record.error == "server_error: HTTPException"
    assert event.type == EventType.EXECUTION_FAILED
    executions.assert_called_once_with(
        1, {"operation": "getThing", "status": ExecutionStatus.FAILED}
    )
    duration.assert_not_called()
    _assert_no_secret(response, record, event)


async def test_declared_non_500_exception_override_creates_no_execution_evidence(
    integration_context: Context, clean_records: None
) -> None:
    trace_id = "1054" + "5" * 28
    harness = _app(integration_context, _FailingRunner(_ExtensionError()), trace_id)
    handler_calls = 0

    async def _handle_exception(_request: Request, _exc: Exception) -> Response:
        nonlocal handler_calls
        handler_calls += 1
        return Response(status_code=503)

    harness.app.add_exception_handler(Exception, _handle_exception)
    with patch.object(execution_service._executions_total, "add") as executions:
        response = await _get(harness)

    assert response.status_code == 503
    assert handler_calls == 1
    assert harness.background_tasks.pending == 0
    assert await _records(integration_context, trace_id) == []
    assert await _events(integration_context, trace_id) == []
    executions.assert_not_called()


async def test_post_response_unmapped_failure_uses_null_status_and_elapsed_duration(
    integration_context: Context, clean_records: None
) -> None:
    trace_id = "1054" + "6" * 28
    result = RunnerResult(
        status_code=201,
        body=b'{"created":true}',
        headers={"content-type": "application/json"},
        content_type="application/json",
        duration_ms=42.75,
    )
    error = _SecretBearingError(_SECRET)
    harness = _app(integration_context, _ResultRunner(result), trace_id)

    def _fail_post_response(outcome: ExecutionOutcome) -> ExecutionOutcome:
        raise error

    harness.app.state.broker = DefaultBroker(
        BrokerExecutionPipeline(_ResultRunner(result), post_response=[_fail_post_response])
    )
    handled: list[Exception] = []

    async def _capture_unhandled(request: Request, exc: Exception) -> Response:
        handled.append(exc)
        return await unhandled_exception_handler(request, exc)

    harness.app.add_exception_handler(Exception, _capture_unhandled)
    with (
        patch.object(
            execution_service._executions_total,
            "add",
            wraps=execution_service._executions_total.add,
        ) as executions,
        patch.object(execution_service._execution_duration, "record") as duration,
        patch.object(execution_service, "_elapsed_ms", return_value=87),
    ):
        response = await _get(harness)
        await harness.background_tasks.drain(3)

    assert response.status_code == 500
    assert response.json()["type"] == "server_error"
    assert handled == [error]
    (record,) = await _records(integration_context, trace_id)
    (event,) = await _events(integration_context, trace_id)
    assert record.status == ExecutionStatus.FAILED
    assert record.http_status is None
    assert record.duration_ms == 87
    assert record.error == "server_error: _SecretBearingError"
    assert event.type == EventType.EXECUTION_FAILED
    assert event.execution_id == record.id
    executions.assert_called_once_with(
        1, {"operation": "getThing", "status": ExecutionStatus.FAILED}
    )
    duration.assert_not_called()
    _assert_no_secret(response, record, event)


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (OperationNotFoundError("missing"), 404),
        (DatabaseUnavailableError("database detail"), 503),
    ],
)
async def test_mapped_post_response_errors_create_no_execution_evidence(
    integration_context: Context,
    clean_records: None,
    error: Exception,
    expected_status: int,
) -> None:
    trace_id = "1054" + "8" * 28
    result = RunnerResult(
        status_code=200,
        body=b"{}",
        headers={"content-type": "application/json"},
        content_type="application/json",
        duration_ms=42.75,
    )
    harness = _app(integration_context, _ResultRunner(result), trace_id)

    def _fail_post_response(outcome: ExecutionOutcome) -> ExecutionOutcome:
        raise error

    harness.app.state.broker = DefaultBroker(
        BrokerExecutionPipeline(_ResultRunner(result), post_response=[_fail_post_response])
    )
    with patch.object(execution_service._executions_total, "add") as executions:
        response = await _get(harness)

    assert response.status_code == expected_status
    assert harness.background_tasks.pending == 0
    assert await _records(integration_context, trace_id) == []
    assert await _events(integration_context, trace_id) == []
    executions.assert_not_called()


async def test_unmapped_failure_error_is_bounded_without_the_exception_message(
    integration_context: Context, clean_records: None
) -> None:
    trace_id = "1054" + "7" * 28
    long_name = "DynamicallyGenerated" + "X" * 160
    error_type = type(long_name, (Exception,), {})
    harness = _app(integration_context, _FailingRunner(error_type(_SECRET)), trace_id)

    response = await _get(harness)
    await harness.background_tasks.drain(3)

    assert response.status_code == 500
    (record,) = await _records(integration_context, trace_id)
    (event,) = await _events(integration_context, trace_id)
    assert record.error == f"server_error: {long_name}"[:128]
    assert len(record.error) == 128
    assert event.type == EventType.EXECUTION_FAILED
    _assert_no_secret(response, record, event)


async def test_recorder_failure_preserves_the_original_error(
    integration_context: Context, clean_records: None
) -> None:
    trace_id = "1054" + "d" * 28
    error = _SecretBearingError(_SECRET)

    async def _fail_to_schedule(failure: UnmappedFailure) -> None:
        raise RuntimeError("scheduler unavailable")

    def _is_server_error(_: Exception) -> bool:
        return True

    with (
        capture_logs() as logs,
        patch.object(execution_service._executions_total, "add") as executions,
        pytest.raises(_SecretBearingError) as caught,
    ):
        await run_execution(
            _ctx_req(trace_id),
            body=None,
            headers=None,
            admin_db=integration_context.admin_db,
            broker=default_broker(_FailingRunner(error)),
            actor_id=_ACTOR_ID,
            actor_type=ActorType.AGENT.value,
            on_unmapped_failure=_fail_to_schedule,
            is_server_error=_is_server_error,
        )

    assert caught.value is error
    executions.assert_called_once_with(
        1, {"operation": "getThing", "status": ExecutionStatus.FAILED}
    )
    assert await _records(integration_context, trace_id) == []
    assert await _events(integration_context, trace_id) == []
    (failure_log,) = [entry for entry in logs if entry["event"] == "unmapped_failure_record_failed"]
    assert failure_log["error_type"] == "RuntimeError"
    assert failure_log["exc_info"] is True


async def test_duplicate_execution_write_fails_task_without_recursive_evidence(
    integration_context: Context, clean_records: None
) -> None:
    trace_id = "1054" + "e" * 28
    execution_id = mint_execution_id()
    harness = _app(integration_context, _FailingRunner(_SecretBearingError(_SECRET)), trace_id)
    with (
        patch.object(execution_service, "mint_execution_id", return_value=execution_id),
        patch.object(
            execution_service._executions_total,
            "add",
            wraps=execution_service._executions_total.add,
        ) as executions,
        patch.object(execution_service._execution_duration, "record") as duration,
        patch.object(execute_router._streaming_persist_failures, "add") as failures,
        patch.object(background_tasks_module._task_outcomes, "add") as outcomes,
    ):
        first = await _get(harness)
        assert len(await _wait_for_records(integration_context, trace_id)) == 1
        second = await _get(harness)
        await harness.background_tasks.drain(3)

    assert first.status_code == second.status_code == 500
    failures.assert_called_once_with(1, {"kind": "unmapped_failure", "reason": "write_error"})
    task_outcomes = [call.args[1] for call in outcomes.call_args_list]
    assert task_outcomes.count({"kind": "unmapped_failure_persist", "outcome": "completed"}) == 1
    assert task_outcomes.count({"kind": "unmapped_failure_persist", "outcome": "failed"}) == 1
    assert executions.call_count == 2
    duration.assert_not_called()
    (record,) = await _records(integration_context, trace_id)
    (event,) = await _events(integration_context, trace_id)
    assert record.id == execution_id
    assert event.execution_id == execution_id
    gate = execute_router._streaming_persist_gate(integration_context)
    assert gate.semaphore._value == gate.limit


async def test_backlog_timeout_is_a_failed_background_task(
    integration_context: Context, clean_records: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_id = "1054" + "f" * 28
    app = Starlette()
    background_tasks = install_background_tasks(app)
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "app": app})
    gate = execute_router._streaming_persist_gate(integration_context)
    monkeypatch.setattr(
        integration_context.config.broker, "streaming_persist_acquire_timeout_s", 0.02
    )
    failure = UnmappedFailure(
        execution_id=mint_execution_id(),
        started_at=datetime.now(UTC),
        duration_ms=12,
        error="server_error: TestError",
    )
    for _ in range(gate.limit):
        await gate.semaphore.acquire()
    try:
        with (
            patch.object(execute_router._streaming_persist_failures, "add") as failures,
            patch.object(background_tasks_module._task_outcomes, "add") as outcomes,
        ):
            await execute_router._unmapped_failure_recorder(
                request, integration_context, _ctx_req(trace_id), _IDENTITY
            )(failure)
            await background_tasks.drain(2)

        failures.assert_called_once_with(
            1, {"kind": "unmapped_failure", "reason": "backlog_timeout"}
        )
        outcomes.assert_called_once_with(
            1, {"kind": "unmapped_failure_persist", "outcome": "failed"}
        )
        assert await _records(integration_context, trace_id) == []
        assert await _events(integration_context, trace_id) == []
    finally:
        for _ in range(gate.limit):
            gate.semaphore.release()


async def test_cancelled_unmapped_persist_is_counted_without_recursive_evidence(
    integration_context: Context, clean_records: None
) -> None:
    trace_id = "1054" + "0" * 28
    app = Starlette()
    background_tasks = install_background_tasks(app)
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "app": app})
    gate = execute_router._streaming_persist_gate(integration_context)
    failure = UnmappedFailure(
        execution_id=mint_execution_id(),
        started_at=datetime.now(UTC),
        duration_ms=12,
        error="server_error: TestError",
    )
    for _ in range(gate.limit):
        await gate.semaphore.acquire()
    try:
        with (
            patch.object(execute_router._streaming_persist_failures, "add") as failures,
            patch.object(background_tasks_module._task_outcomes, "add") as outcomes,
            patch.object(
                execute_router._streaming_persist_pending,
                "add",
                wraps=execute_router._streaming_persist_pending.add,
            ) as pending,
        ):
            await execute_router._unmapped_failure_recorder(
                request, integration_context, _ctx_req(trace_id), _IDENTITY
            )(failure)
            async with asyncio.timeout(2):
                while not any(
                    call.args[1] == {"kind": "unmapped_failure", "state": "waiting"}
                    for call in pending.call_args_list
                ):
                    await asyncio.sleep(0)
            await background_tasks.drain(0)

        failures.assert_called_once_with(1, {"kind": "unmapped_failure", "reason": "cancelled"})
        outcomes.assert_called_once_with(
            1, {"kind": "unmapped_failure_persist", "outcome": "cancelled"}
        )
        assert await _records(integration_context, trace_id) == []
        assert await _events(integration_context, trace_id) == []
    finally:
        for _ in range(gate.limit):
            gate.semaphore.release()
