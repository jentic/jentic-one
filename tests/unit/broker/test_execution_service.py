"""Unit tests for run_execution: actor/operation threading and the persistence scope."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.exceptions import BrokerError
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution.service import default_broker, run_execution
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.schemas import OperationInfo


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
            session=session,
            broker=broker,
            actor_id="agt_abc123",
            actor_type="agent",
        )

    mock_record.assert_called_once()
    call_kwargs = mock_record.call_args.kwargs
    assert call_kwargs["actor_id"] == "agt_abc123"
    assert call_kwargs["actor_type"] == "agent"


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
            session=session,
            broker=broker,
            actor_id="usr_default",
            actor_type="user",
        )

    call_kwargs = mock_record.call_args.kwargs
    assert call_kwargs["actor_id"] == "usr_default"
    assert call_kwargs["actor_type"] == "user"


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
            session=session,
            broker=broker,
            actor_id="agt_abc123",
            actor_type="agent",
        )

    operation = mock_record.call_args.kwargs["operation"]
    assert operation == OperationInfo(id="getThing", path="/v1/things", method="GET")


class _RecordingTransaction:
    """A ``transaction`` factory that records when it is opened and how it exits."""

    def __init__(self, session: AsyncMock) -> None:
        self.session = session
        self.events: list[str] = []

    @contextlib.asynccontextmanager
    async def __call__(self) -> AsyncIterator[AsyncMock]:
        self.events.append("open")
        try:
            yield self.session
        except BaseException:
            self.events.append("rollback")
            raise
        self.events.append("commit")


class _OrderedRunner(UpstreamRunner):
    """Records the upstream call in the shared event log, then answers or fails."""

    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self._events = events
        self._fail = fail

    async def run(self, request: RunnerRequest) -> RunnerResult:
        self._events.append("upstream")
        if self._fail:
            raise BrokerError(detail="upstream timeout")
        return RunnerResult(
            status_code=200, headers={}, body=b"ok", content_type="text/plain", duration_ms=10
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_transaction_opens_only_after_the_upstream_call(fail: bool) -> None:
    """With ``transaction``, the record is written in a transaction opened after upstream I/O.

    On a BrokerError the transaction still commits (no manual commit) before the
    error re-raises, so the FAILED record survives.
    """
    session = _session_mock()
    txn = _RecordingTransaction(session)
    broker = default_broker(_OrderedRunner(txn.events, fail=fail))

    with (
        patch(
            "jentic_one.broker.services.execution.service.record_execution",
            new_callable=AsyncMock,
        ) as mock_record,
        pytest.raises(BrokerError) if fail else contextlib.nullcontext(),
    ):
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            transaction=txn,
            broker=broker,
            actor_id="agt_abc123",
            actor_type="agent",
        )

    assert txn.events == ["upstream", "open", "commit"]
    assert mock_record.call_args.args[0] is session
    expected_status = ExecutionStatus.FAILED if fail else ExecutionStatus.COMPLETED
    assert mock_record.call_args.kwargs["status"] == expected_status
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("both", [False, True])
async def test_requires_exactly_one_of_session_or_transaction(both: bool) -> None:
    """Passing neither or both persistence targets is a programming error."""
    session = _session_mock()
    kwargs: dict[str, Any] = (
        {"session": session, "transaction": _RecordingTransaction(session)} if both else {}
    )
    with pytest.raises(TypeError, match="exactly one"):
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            broker=default_broker(_StubRunner()),
            actor_id="agt_abc123",
            actor_type="agent",
            **kwargs,
        )
