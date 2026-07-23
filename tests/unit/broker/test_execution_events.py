"""Unit tests verifying that run_execution and persist_streaming_execution emit lifecycle events."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.exceptions import BrokerError
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution.service import (
    default_broker,
    persist_streaming_execution,
    run_execution,
)
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.models.events import ErrorSource, EventSeverity, EventType


class _StatusRunner(UpstreamRunner):
    """Runner that returns a fixed upstream status code."""

    def __init__(self, status_code: int) -> None:
        self._status_code = status_code

    async def run(self, request: RunnerRequest) -> RunnerResult:
        return RunnerResult(
            status_code=self._status_code,
            headers={},
            body=b"",
            content_type="text/plain",
            duration_ms=10,
        )


class _SuccessRunner(UpstreamRunner):
    async def run(self, request: RunnerRequest) -> RunnerResult:
        return RunnerResult(
            status_code=200,
            headers={},
            body=b"ok",
            content_type="text/plain",
            duration_ms=10,
        )


class _FailRunner(UpstreamRunner):
    async def run(self, request: RunnerRequest) -> RunnerResult:
        raise BrokerError(detail="upstream timeout")


def _ctx_req() -> ExecuteRequestContext:
    return ExecuteRequestContext(
        upstream_url="https://api.example.com/v1/test",
        method="GET",
        trace_id="a" * 32,
        toolkit_id="tk_test000000000000000000",
        operation_id="testOp",
        api_vendor="example",
        api_name="api",
        api_version="1.0.0",
        prefer=None,
        pinned_revisions=None,
    )


@pytest.mark.asyncio
async def test_run_execution_emits_completed_event() -> None:
    """Successful execution emits EXECUTION_COMPLETED."""
    session = AsyncMock()
    broker = default_broker(_SuccessRunner())

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
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            session=session,
            broker=broker,
            actor_id="agt_abc",
            actor_type="agent",
        )

    mock_emit.assert_called_once()
    call_kwargs = mock_emit.call_args.kwargs
    assert call_kwargs["type"] == EventType.EXECUTION_COMPLETED
    assert call_kwargs["severity"] == EventSeverity.INFO
    assert call_kwargs["actor_id"] == "agt_abc"
    assert call_kwargs["actor_type"] == "agent"


@pytest.mark.asyncio
async def test_run_execution_emits_failed_event_on_broker_error() -> None:
    """BrokerError during execution emits EXECUTION_FAILED before re-raising."""
    session = AsyncMock()
    broker = default_broker(_FailRunner())

    with (
        patch(
            "jentic_one.broker.services.execution.service.record_execution",
            new_callable=AsyncMock,
        ),
        patch(
            "jentic_one.broker.services.execution.service.emit_event",
            new_callable=AsyncMock,
        ) as mock_emit,
        pytest.raises(BrokerError),
    ):
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            session=session,
            broker=broker,
            actor_id="agt_abc",
            actor_type="agent",
        )

    emit_calls = [
        c for c in mock_emit.call_args_list if c.kwargs.get("type") == EventType.EXECUTION_FAILED
    ]
    assert len(emit_calls) == 1
    call_kwargs = emit_calls[0].kwargs
    assert call_kwargs["severity"] == EventSeverity.ERROR
    assert call_kwargs["requires_action"] is True


@pytest.mark.asyncio
async def test_persist_streaming_execution_emits_completed_event() -> None:
    """Streaming-path persistence emits EXECUTION_COMPLETED."""
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
        await persist_streaming_execution(
            session,
            execution_id="exec_test123",
            started_at=datetime.now(UTC),
            status=ExecutionStatus.COMPLETED,
            http_status=200,
            duration_ms=50,
            error=None,
            ctx_req=_ctx_req(),
            actor_id="agt_stream",
            actor_type="agent",
        )

    mock_emit.assert_called_once()
    call_kwargs = mock_emit.call_args.kwargs
    assert call_kwargs["type"] == EventType.EXECUTION_COMPLETED
    assert call_kwargs["actor_id"] == "agt_stream"


@pytest.mark.asyncio
async def test_persist_streaming_execution_emits_failed_event() -> None:
    """Streaming-path persistence emits EXECUTION_FAILED for error outcomes."""
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
        await persist_streaming_execution(
            session,
            execution_id="exec_fail456",
            started_at=datetime.now(UTC),
            status=ExecutionStatus.FAILED,
            http_status=502,
            duration_ms=10,
            error="Upstream returned 502",
            ctx_req=_ctx_req(),
            actor_id="agt_stream",
            actor_type="agent",
        )

    mock_emit.assert_called_once()
    call_kwargs = mock_emit.call_args.kwargs
    assert call_kwargs["type"] == EventType.EXECUTION_FAILED
    assert call_kwargs["severity"] == EventSeverity.ERROR
    assert call_kwargs["requires_action"] is True


@pytest.mark.asyncio
async def test_emit_failure_does_not_propagate() -> None:
    """If emit_event raises, the execution still completes without error."""
    session = AsyncMock()

    with (
        patch(
            "jentic_one.broker.services.execution.service.record_execution",
            new_callable=AsyncMock,
        ),
        patch(
            "jentic_one.broker.services.execution.service.emit_event",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB down"),
        ),
    ):
        await persist_streaming_execution(
            session,
            execution_id="exec_no_raise",
            started_at=datetime.now(UTC),
            status=ExecutionStatus.COMPLETED,
            http_status=200,
            duration_ms=10,
            error=None,
            ctx_req=_ctx_req(),
            actor_id="agt_stream",
            actor_type="agent",
        )


@pytest.mark.parametrize(
    ("status_code", "expected_tag"),
    [
        (401, ErrorSource.AUTH_THIRDPARTY_UNAUTHORIZED),
        (403, ErrorSource.AUTH_THIRDPARTY_FORBIDDEN),
    ],
)
@pytest.mark.asyncio
async def test_run_execution_tags_failed_event_with_thirdparty_auth(
    status_code: int, expected_tag: ErrorSource
) -> None:
    """An upstream 401/403 tags EXECUTION_FAILED with the granular third-party source.

    The tag rides on the single EXECUTION_FAILED event rather than a separate
    auth event — the flat, correlation-id-free telemetry payload can't dedupe two
    same-timestamp events, so a second event would skew the funnel.
    """
    session = AsyncMock()
    broker = default_broker(_StatusRunner(status_code))

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
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            session=session,
            broker=broker,
            actor_id="agt_abc",
            actor_type="agent",
        )

    # The split rides on the single EXECUTION_FAILED event.
    failed_calls = [
        c for c in mock_emit.call_args_list if c.kwargs.get("type") == EventType.EXECUTION_FAILED
    ]
    assert len(failed_calls) == 1
    call_kwargs = failed_calls[0].kwargs
    assert call_kwargs["tags"] == {expected_tag}
    assert call_kwargs["severity"] == EventSeverity.ERROR
    assert call_kwargs["actor_id"] == "agt_abc"


@pytest.mark.asyncio
async def test_run_execution_no_auth_tag_on_success() -> None:
    """A 2xx upstream response emits no tagged failure event."""
    session = AsyncMock()
    broker = default_broker(_SuccessRunner())

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
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            session=session,
            broker=broker,
            actor_id="agt_abc",
            actor_type="agent",
        )

    assert not [
        c for c in mock_emit.call_args_list if c.kwargs.get("type") == EventType.EXECUTION_FAILED
    ]


@pytest.mark.parametrize(
    ("http_status", "expected_tag"),
    [
        (401, ErrorSource.AUTH_THIRDPARTY_UNAUTHORIZED),
        (403, ErrorSource.AUTH_THIRDPARTY_FORBIDDEN),
    ],
)
@pytest.mark.asyncio
async def test_persist_streaming_execution_tags_failed_event_with_thirdparty_auth(
    http_status: int, expected_tag: ErrorSource
) -> None:
    """Streaming path tags EXECUTION_FAILED by source on an upstream 401/403."""
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
        await persist_streaming_execution(
            session,
            execution_id="exec_stream_auth",
            started_at=datetime.now(UTC),
            status=ExecutionStatus.FAILED,
            http_status=http_status,
            duration_ms=10,
            error=f"Upstream returned {http_status}",
            ctx_req=_ctx_req(),
            actor_id="agt_stream",
            actor_type="agent",
        )

    failed_calls = [
        c for c in mock_emit.call_args_list if c.kwargs.get("type") == EventType.EXECUTION_FAILED
    ]
    assert len(failed_calls) == 1
    call_kwargs = failed_calls[0].kwargs
    assert call_kwargs["tags"] == {expected_tag}
    assert call_kwargs["severity"] == EventSeverity.ERROR
    assert call_kwargs["actor_id"] == "agt_stream"


@pytest.mark.asyncio
async def test_persist_streaming_execution_no_auth_tag_on_success() -> None:
    """A 2xx streaming outcome emits no failure event (only EXECUTION_COMPLETED)."""
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
        await persist_streaming_execution(
            session,
            execution_id="exec_stream_ok",
            started_at=datetime.now(UTC),
            status=ExecutionStatus.COMPLETED,
            http_status=200,
            duration_ms=10,
            error=None,
            ctx_req=_ctx_req(),
            actor_id="agt_stream",
            actor_type="agent",
        )

    assert not [
        c for c in mock_emit.call_args_list if c.kwargs.get("type") == EventType.EXECUTION_FAILED
    ]


@pytest.mark.asyncio
async def test_persist_streaming_execution_no_auth_tag_without_vendor() -> None:
    """A vendorless streaming 401 emits an untagged EXECUTION_FAILED (no auth split)."""
    session = AsyncMock()
    ctx_req = ExecuteRequestContext(
        upstream_url="https://api.example.com/v1/test",
        method="GET",
        trace_id="a" * 32,
        toolkit_id="tk_test000000000000000000",
        operation_id="testOp",
        api_vendor="",
        api_name="",
        api_version="",
        prefer=None,
        pinned_revisions=None,
    )

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
        await persist_streaming_execution(
            session,
            execution_id="exec_stream_novendor",
            started_at=datetime.now(UTC),
            status=ExecutionStatus.FAILED,
            http_status=401,
            duration_ms=10,
            error="Upstream returned 401",
            ctx_req=ctx_req,
            actor_id="agt_stream",
            actor_type="agent",
        )

    failed_calls = [
        c for c in mock_emit.call_args_list if c.kwargs.get("type") == EventType.EXECUTION_FAILED
    ]
    assert len(failed_calls) == 1
    assert not failed_calls[0].kwargs.get("tags")


@pytest.mark.asyncio
async def test_run_execution_no_auth_tag_without_vendor() -> None:
    """A vendorless call (no credential path) emits an untagged failure on 401."""
    session = AsyncMock()
    broker = default_broker(_StatusRunner(401))
    ctx_req = ExecuteRequestContext(
        upstream_url="https://api.example.com/v1/test",
        method="GET",
        trace_id="a" * 32,
        toolkit_id="tk_test000000000000000000",
        operation_id="testOp",
        api_vendor="",
        api_name="",
        api_version="",
        prefer=None,
        pinned_revisions=None,
    )

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
        await run_execution(
            ctx_req,
            body=None,
            headers=None,
            session=session,
            broker=broker,
            actor_id="agt_abc",
            actor_type="agent",
        )

    failed_calls = [
        c for c in mock_emit.call_args_list if c.kwargs.get("type") == EventType.EXECUTION_FAILED
    ]
    assert len(failed_calls) == 1
    assert not failed_calls[0].kwargs.get("tags")
