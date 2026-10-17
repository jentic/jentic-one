"""Unit tests for circuit-breaker event emission in execution service."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.adapters.runners.circuit import CircuitBreakerRunner
from jentic_one.broker.core.exceptions import CircuitOpenError
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution.service import default_broker, run_execution
from jentic_one.shared.models.events import EVENT_TYPE_SEVERITIES, EventType
from jentic_one.shared.resilience import CircuitBreaker
from jentic_one.shared.schemas import OperationInfo
from jentic_one.shared.state import MemoryStateBackend


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


class _FailingRunner(UpstreamRunner):
    """An upstream that always answers 500, so the breaker trips."""

    async def run(self, request: RunnerRequest) -> RunnerResult:
        return RunnerResult(status_code=500, body=b"", headers={}, content_type=None, duration_ms=1)


async def _open_breaker_runner(backend: MemoryStateBackend, host: str) -> CircuitBreakerRunner:
    """A blocking breaker runner on ``backend`` whose circuit for ``host`` is open."""
    breaker = CircuitBreaker(backend, failure_ratio=0.5, min_calls=2, window_s=30, cooldown_s=15)
    runner = CircuitBreakerRunner(_FailingRunner(), breaker, enforcement_mode="blocking")
    for _ in range(2):
        await runner.run(RunnerRequest(method="GET", url=f"https://{host}/v1/things"))
    return runner


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
    # Cross-check against the documented severity matrix (issue #907): a future
    # change to this emit site must also update EVENT_TYPE_SEVERITIES, or this
    # assertion — not just the hardcoded literal above — catches the drift.
    assert call_kwargs["severity"] in EVENT_TYPE_SEVERITIES[EventType.UPSTREAM_CIRCUIT_OPEN]
    assert "api.example.com" in call_kwargs["summary"]
    assert call_kwargs["data"] == {"host": "api.example.com"}


@pytest.mark.asyncio
async def test_circuit_open_dedup_suppresses_second_emit() -> None:
    """A second CircuitOpenError for the same host within the cooldown should not emit."""
    session = AsyncMock()
    broker = default_broker(await _open_breaker_runner(MemoryStateBackend(), "api.example.com"))

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
    backend = MemoryStateBackend()

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
            broker = default_broker(await _open_breaker_runner(backend, host))
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


@pytest.mark.asyncio
async def test_two_brokers_sharing_a_backend_report_an_open_circuit_once() -> None:
    """The report claim is on the shared backend, so a second process stays quiet."""
    backend = MemoryStateBackend()
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
        # The first "process" trips the circuit; the second builds its own
        # breaker and runner over the same backend and sees the open latch.
        first = await _open_breaker_runner(backend, "api.example.com")
        second = CircuitBreakerRunner(
            _FailingRunner(),
            CircuitBreaker(backend, failure_ratio=0.5, min_calls=2, window_s=30, cooldown_s=15),
        )
        for runner in (first, second):
            broker = default_broker(runner)
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
async def test_an_open_circuit_reports_again_after_the_cooldown() -> None:
    now = [0.0]
    backend = MemoryStateBackend(clock=lambda: now[0])
    breaker = CircuitBreaker(backend, failure_ratio=0.5, min_calls=2, window_s=30, cooldown_s=15)

    assert await breaker.claim_open_report("api.example.com") is True
    assert await breaker.claim_open_report("api.example.com") is False
    now[0] = 15.0
    assert await breaker.claim_open_report("api.example.com") is True


@pytest.mark.asyncio
async def test_a_refused_report_claim_reports_nothing() -> None:
    backend = MemoryStateBackend(max_entries=1)
    await backend.set("cb:other", b"1", ttl_s=60)
    breaker = CircuitBreaker(backend, failure_ratio=0.5, min_calls=2, window_s=30, cooldown_s=15)

    assert await breaker.claim_open_report("api.example.com") is False


class _ReportClaimFails(MemoryStateBackend):
    """A backend whose open-circuit report claim errors (a flaky shared store)."""

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool:
        if key.startswith("cb:report:"):
            raise ConnectionError("store unreachable")
        return await super().set_if_absent(key, value, ttl_s=ttl_s)


@pytest.mark.asyncio
async def test_a_failing_report_claim_still_answers_circuit_open_unreported() -> None:
    """The report claim only gates telemetry: a store error never replaces the 503."""
    runner = await _open_breaker_runner(_ReportClaimFails(), "api.example.com")

    with pytest.raises(CircuitOpenError) as exc:
        await runner.run(RunnerRequest(method="GET", url="https://api.example.com/v1/things"))

    assert exc.value.report is False
