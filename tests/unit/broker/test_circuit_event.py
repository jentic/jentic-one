"""Unit tests for circuit-breaker event emission in execution service."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.exceptions import CircuitOpenError
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution import service as service_module
from jentic_one.broker.services.execution.service import (
    _circuit_event_last_emitted,
    default_broker,
    run_execution,
)
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.models.events import EVENT_TYPE_SEVERITIES, EventType
from jentic_one.shared.schemas import OperationInfo
from tests.unit.fake_tx import SessionScope


class _CircuitOpenRunner(UpstreamRunner):
    """A runner that always raises CircuitOpenError."""

    async def run(self, request: RunnerRequest) -> RunnerResult:
        raise CircuitOpenError(
            detail="Upstream circuit open; the broker is fast-failing to let it recover.",
            type="circuit_open",
            headers={"Retry-After": "15"},
        )


def _ctx_req(host: str = "api.example.com") -> ExecuteRequestContext:
    return ExecuteRequestContext(
        upstream_url=f"https://{host}/v1/things",
        method="GET",
        trace_id="a" * 32,
        toolkit_id="tk_test000000000000000000",
        operation=OperationInfo(id="getThing"),
        api_vendor="example",
        api_name="api",
        api_version="1.0.0",
        prefer=None,
        pinned_revisions=None,
    )


@pytest.fixture(autouse=True)
def _clear_dedup() -> None:
    """Ensure dedup state is clean between tests."""
    _circuit_event_last_emitted.clear()


@pytest.mark.asyncio
async def test_circuit_open_emits_event() -> None:
    """When CircuitOpenError is raised, emit_event should be called."""
    session = AsyncMock()
    scope = SessionScope(session)
    broker = default_broker(_CircuitOpenRunner())

    with (
        patch(
            "jentic_one.broker.services.execution.service.record_execution",
            new_callable=AsyncMock,
        ) as mock_record,
        patch(
            "jentic_one.broker.services.execution.service.emit_event",
            new_callable=AsyncMock,
        ) as mock_emit,
        pytest.raises(CircuitOpenError),
    ):
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            admin_db=scope,
            broker=broker,
            actor_id="agt_abc123",
            actor_type="agent",
        )

    circuit_calls = [
        c for c in mock_emit.call_args_list if c.kwargs.get("type") == "upstream.circuit_open"
    ]
    assert len(circuit_calls) == 1
    call_kwargs = circuit_calls[0].kwargs
    assert call_kwargs["severity"].value == "warning"
    # Cross-check against the documented severity matrix (issue #907): a future
    # change to this emit site must also update EVENT_TYPE_SEVERITIES, or this
    # assertion — not just the hardcoded literal above — catches the drift.
    assert call_kwargs["severity"] in EVENT_TYPE_SEVERITIES[EventType.UPSTREAM_CIRCUIT_OPEN]
    assert "api.example.com" in call_kwargs["summary"]
    assert call_kwargs["data"] == {"host": "api.example.com"}
    # The record and every event (incl. the circuit-open event) share one transaction.
    assert scope.opened == 1
    assert mock_record.call_args.args[0] is session
    assert all(c.args[0] is session for c in mock_emit.call_args_list)


@pytest.mark.asyncio
async def test_circuit_open_dedup_suppresses_second_emit() -> None:
    """A second CircuitOpenError for the same host within the cooldown should not emit."""
    session = AsyncMock()
    broker = default_broker(_CircuitOpenRunner())

    with (
        patch(
            "jentic_one.broker.services.execution.service.record_execution",
            new_callable=AsyncMock,
        ),
        patch(
            "jentic_one.broker.services.execution.service.emit_event",
            new_callable=AsyncMock,
        ) as mock_emit,
    ):
        for _ in range(3):
            with pytest.raises(CircuitOpenError):
                await run_execution(
                    _ctx_req(),
                    body=None,
                    headers=None,
                    admin_db=SessionScope(session),
                    broker=broker,
                    actor_id="agt_abc123",
                    actor_type="agent",
                )

    circuit_calls = [
        c for c in mock_emit.call_args_list if c.kwargs.get("type") == "upstream.circuit_open"
    ]
    assert len(circuit_calls) == 1


@pytest.mark.asyncio
async def test_circuit_open_different_hosts_emit_independently() -> None:
    """Different hosts should each emit their own event."""
    session = AsyncMock()

    with (
        patch(
            "jentic_one.broker.services.execution.service.record_execution",
            new_callable=AsyncMock,
        ),
        patch(
            "jentic_one.broker.services.execution.service.emit_event",
            new_callable=AsyncMock,
        ) as mock_emit,
    ):
        for host in ("alpha.example.com", "beta.example.com"):
            broker = default_broker(_CircuitOpenRunner())
            with pytest.raises(CircuitOpenError):
                await run_execution(
                    _ctx_req(host=host),
                    body=None,
                    headers=None,
                    admin_db=SessionScope(session),
                    broker=broker,
                    actor_id="agt_abc123",
                    actor_type="agent",
                )

    circuit_calls = [
        c for c in mock_emit.call_args_list if c.kwargs.get("type") == "upstream.circuit_open"
    ]
    assert len(circuit_calls) == 2


@pytest.mark.asyncio
async def test_buffered_circuit_open_records_the_problem_type_and_counts_a_failure() -> None:
    """The buffered record uses the streaming vocabulary: the problem type, not the detail.

    It counts one FAILED execution but adds no duration sample (no upstream
    round trip), the same policy as the streaming path.
    """
    with (
        patch.object(service_module, "record_execution", new_callable=AsyncMock) as mock_record,
        patch.object(service_module, "emit_event", new_callable=AsyncMock) as mock_emit,
        patch.object(service_module._executions_total, "add") as count,
        patch.object(service_module._execution_duration, "record") as duration,
        pytest.raises(CircuitOpenError),
    ):
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            admin_db=SessionScope(AsyncMock()),
            broker=default_broker(_CircuitOpenRunner()),
            actor_id="agt_abc123",
            actor_type="agent",
        )

    mock_record.assert_awaited_once()
    assert mock_record.call_args.kwargs["error"] == "circuit_open"
    (failed,) = [
        c for c in mock_emit.call_args_list if c.kwargs.get("type") == EventType.EXECUTION_FAILED
    ]
    assert failed.kwargs["summary"] == "Execution failed: circuit_open"
    count.assert_called_once_with(1, {"operation": "getThing", "status": ExecutionStatus.FAILED})
    duration.assert_not_called()


@pytest.mark.asyncio
async def test_streaming_circuit_open_emits_the_same_rate_limited_event() -> None:
    """A streaming circuit fast-fail raises ``upstream.circuit_open`` under the same cooldown."""
    with (
        patch.object(service_module, "record_execution", new_callable=AsyncMock),
        patch.object(service_module, "emit_event", new_callable=AsyncMock) as mock_emit,
    ):
        for _ in range(2):
            await service_module.persist_streaming_execution(
                AsyncMock(),
                execution_id="exec_stream_circuit",
                started_at=datetime.now(UTC),
                status=ExecutionStatus.FAILED,
                http_status=None,
                duration_ms=0,
                error="circuit_open",
                ctx_req=_ctx_req(),
                actor_id="agt_abc123",
                actor_type="agent",
                circuit_open=True,
            )
        await service_module.persist_streaming_execution(
            AsyncMock(),
            execution_id="exec_stream_timeout",
            started_at=datetime.now(UTC),
            status=ExecutionStatus.FAILED,
            http_status=None,
            duration_ms=0,
            error="upstream_timeout",
            ctx_req=_ctx_req(host="other.example.com"),
            actor_id="agt_abc123",
            actor_type="agent",
        )

    circuit_calls = [
        c for c in mock_emit.call_args_list if c.kwargs.get("type") == "upstream.circuit_open"
    ]
    assert len(circuit_calls) == 1
    assert circuit_calls[0].kwargs["data"] == {"host": "api.example.com"}
