"""Unit tests for circuit-breaker event emission in execution service."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.exceptions import CircuitOpenError
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution.service import (
    _circuit_event_last_emitted,
    default_broker,
    run_execution,
)


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
        operation_id="getThing",
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
        pytest.raises(CircuitOpenError),
    ):
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            session=session,
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
    assert "api.example.com" in call_kwargs["summary"]
    assert call_kwargs["data"] == {"host": "api.example.com"}


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
                    session=session,
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
                    session=session,
                    broker=broker,
                    actor_id="agt_abc123",
                    actor_type="agent",
                )

    circuit_calls = [
        c for c in mock_emit.call_args_list if c.kwargs.get("type") == "upstream.circuit_open"
    ]
    assert len(circuit_calls) == 2
