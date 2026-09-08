"""Unit tests for actor_id/actor_type threading in run_execution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution.service import default_broker, run_execution


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
        operation_id="getThing",
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
