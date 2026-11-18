"""Unit tests for actor_id/actor_type threading in run_execution."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution.service import default_broker, run_execution
from jentic_one.shared.executions import ExecutionRecordInput, record_execution
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.schemas import OperationInfo
from tests.unit.fake_tx import SessionScope


class _StubRunner(UpstreamRunner):
    async def run(self, request: RunnerRequest) -> RunnerResult:
        return RunnerResult(
            status_code=200,
            headers={},
            body=b"ok",
            content_type="text/plain",
            duration_ms=10,
        )


def _ctx_req() -> ExecuteRequestContext:
    return ExecuteRequestContext(
        upstream_url="https://api.example.com/v1/things",
        method="GET",
        trace_id="a" * 32,
        toolkit_id="tk_test000000000000000000",
        operation=OperationInfo(id="getThing", path="/v1/things", method="GET"),
        api_vendor="example",
        api_name="api",
        api_version="1.0.0",
        prefer=None,
        pinned_revisions=None,
    )


def _session_mock() -> AsyncMock:
    """An AsyncSession stand-in whose synchronous ``add`` stays synchronous.

    ``AsyncSession.add`` is a plain (non-coroutine) method; leaving it as the
    default ``AsyncMock`` child would make ``session.add(...)`` return an
    un-awaited coroutine and emit ``RuntimeWarning``.
    """
    session = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.mark.asyncio
async def test_actor_fields_forwarded_to_record_execution() -> None:
    """actor_id and actor_type passed to run_execution reach record_execution."""
    session = _session_mock()
    broker = default_broker(_StubRunner())

    with patch(
        "jentic_one.broker.services.execution.service.record_execution", new_callable=AsyncMock
    ) as mock_record:
        mock_record.return_value = "exec_test"
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            admin_db=SessionScope(session),
            broker=broker,
            actor_id="agt_abc123",
            actor_type="agent",
        )

    mock_record.assert_called_once()
    record = mock_record.call_args.args[1]
    assert isinstance(record, ExecutionRecordInput)
    assert record.actor_id == "agt_abc123"
    assert record.actor_type == "agent"


@pytest.mark.asyncio
async def test_actor_fields_are_required() -> None:
    """actor_id and actor_type are required parameters for run_execution."""
    session = _session_mock()
    broker = default_broker(_StubRunner())

    with patch(
        "jentic_one.broker.services.execution.service.record_execution", new_callable=AsyncMock
    ) as mock_record:
        mock_record.return_value = "exec_test"
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            admin_db=SessionScope(session),
            broker=broker,
            actor_id="usr_default",
            actor_type="user",
        )

    record = mock_record.call_args.args[1]
    assert isinstance(record, ExecutionRecordInput)
    assert record.actor_id == "usr_default"
    assert record.actor_type == "user"


@pytest.mark.asyncio
async def test_operation_info_forwarded_to_record_execution() -> None:
    """The resolved operation (id + path template + method) reaches record_execution intact."""
    session = _session_mock()
    broker = default_broker(_StubRunner())

    with patch(
        "jentic_one.broker.services.execution.service.record_execution", new_callable=AsyncMock
    ) as mock_record:
        mock_record.return_value = "exec_test"
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            admin_db=SessionScope(session),
            broker=broker,
            actor_id="agt_abc123",
            actor_type="agent",
        )

    record = mock_record.call_args.args[1]
    assert isinstance(record, ExecutionRecordInput)
    assert record.operation == OperationInfo(id="getThing", path="/v1/things", method="GET")


@pytest.mark.asyncio
async def test_run_execution_invokes_on_outcome_before_admin_db_transaction() -> None:
    """``on_outcome`` runs as soon as the upstream call returns, before ``admin_db``
    persistence opens, so idempotency completion is durable even if the DB write fails."""
    events: list[str] = []

    async def _on_outcome(outcome: object) -> None:
        events.append("on_outcome")

    class _FailingTx:
        def transaction(self) -> object:
            events.append("tx_open")
            raise RuntimeError("db write failed")

    with pytest.raises(RuntimeError, match="db write failed"):
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            admin_db=_FailingTx(),  # type: ignore[arg-type]
            broker=default_broker(_StubRunner()),
            actor_id="agt_abc123",
            actor_type="agent",
            on_dispatched=lambda: events.append("dispatched"),
            on_outcome=_on_outcome,
        )

    assert events == ["dispatched", "on_outcome", "tx_open"]


@pytest.mark.asyncio
async def test_execution_record_input_is_frozen_slotted_and_validates_status() -> None:
    """ExecutionRecordInput is frozen/slotted and rejects non-terminal status."""
    record = ExecutionRecordInput(
        execution_id="exec_1",
        toolkit_id=None,
        trace_id="a" * 32,
        started_at=datetime.now(UTC),
        status=ExecutionStatus.COMPLETED,
        actor_id="agt_abc123",
        actor_type="agent",
    )
    assert not hasattr(record, "__dict__")
    with pytest.raises(FrozenInstanceError):
        record.status = ExecutionStatus.FAILED  # type: ignore[misc]

    bad_record = ExecutionRecordInput(
        execution_id="exec_bad",
        toolkit_id=None,
        trace_id="a" * 32,
        started_at=datetime.now(UTC),
        status="pending",  # type: ignore[arg-type]
        actor_id="agt_abc123",
        actor_type="agent",
    )
    with pytest.raises(ValueError, match="Only terminal statuses allowed, got: 'pending'"):
        await record_execution(_session_mock(), bad_record)
