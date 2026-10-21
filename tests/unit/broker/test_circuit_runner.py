"""Unit tests for the ``CircuitBreakerRunner`` decorator (§05 R5.1).

In ``blocking`` mode an open circuit fast-fails with ``CircuitOpenError`` (carries
``Retry-After``) and never calls the inner runner; in ``observation`` mode the
same open circuit lets the call through (dry-run). A ``5xx`` or transport error
counts as a failure; ``2xx``/``4xx`` count as success (the upstream answered).
"""

from __future__ import annotations

import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult
from jentic_one.broker.adapters.runners.circuit import _NO_HOST_KEY, CircuitBreakerRunner
from jentic_one.broker.core.exceptions import (
    BrokerError,
    CircuitOpenError,
    UpstreamTimeoutError,
)
from jentic_one.shared.resilience import CircuitBreaker
from jentic_one.shared.resilience.circuit import _OPEN_PREFIX
from jentic_one.shared.state.backend import MemoryStateBackend


class _FakeRunner:
    """Inner runner returning a fixed status (or raising) and counting calls."""

    def __init__(self, *, status: int = 200, raises: Exception | None = None) -> None:
        self.status = status
        self.raises = raises
        self.calls = 0

    async def run(self, request: RunnerRequest) -> RunnerResult:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return RunnerResult(
            status_code=self.status, body=b"", headers={}, content_type=None, duration_ms=1
        )


def _request(url: str = "https://api.example.com/v1/x") -> RunnerRequest:
    return RunnerRequest(method="GET", url=url)


def _breaker(backend: MemoryStateBackend) -> CircuitBreaker:
    return CircuitBreaker(backend, failure_ratio=0.5, min_calls=2, window_s=30, cooldown_s=15)


@pytest.mark.asyncio
async def test_blocking_fast_fails_when_open() -> None:
    backend = MemoryStateBackend()
    inner = _FakeRunner(status=500)
    runner = CircuitBreakerRunner(inner, _breaker(backend), enforcement_mode="blocking")

    # Two 500s trip the circuit (ratio 1.0, min_calls 2).
    await runner.run(_request())
    await runner.run(_request())
    calls_before = inner.calls

    with pytest.raises(CircuitOpenError) as exc:
        await runner.run(_request())
    assert exc.value.headers["Retry-After"] == "15"
    # The inner runner was NOT called for the shed request.
    assert inner.calls == calls_before


@pytest.mark.asyncio
async def test_observation_lets_through_when_open() -> None:
    backend = MemoryStateBackend()
    inner = _FakeRunner(status=500)
    runner = CircuitBreakerRunner(inner, _breaker(backend), enforcement_mode="observation")

    await runner.run(_request())
    await runner.run(_request())
    calls_before = inner.calls

    # Circuit is open, but observation mode still calls the upstream (no raise).
    result = await runner.run(_request())
    assert result.status_code == 500
    assert inner.calls == calls_before + 1


@pytest.mark.asyncio
async def test_transport_error_counts_as_failure_and_propagates() -> None:
    backend = MemoryStateBackend()
    inner = _FakeRunner(raises=UpstreamTimeoutError(detail="timeout"))
    runner = CircuitBreakerRunner(inner, _breaker(backend), enforcement_mode="blocking")

    with pytest.raises(UpstreamTimeoutError):
        await runner.run(_request())
    with pytest.raises(UpstreamTimeoutError):
        await runner.run(_request())

    # Two transport failures (min_calls 2, ratio 1.0) trip the circuit.
    with pytest.raises(CircuitOpenError):
        await runner.run(_request())


@pytest.mark.asyncio
async def test_local_protocol_error_does_not_trip_circuit() -> None:
    backend = MemoryStateBackend()
    inner = _FakeRunner(raises=BrokerError(detail="bad header", type="upstream_request_invalid"))
    runner = CircuitBreakerRunner(inner, _breaker(backend), enforcement_mode="blocking")

    for _ in range(3):
        with pytest.raises(BrokerError) as excinfo:
            await runner.run(_request())
        assert excinfo.value.type == "upstream_request_invalid"
    assert inner.calls == 3


@pytest.mark.asyncio
async def test_success_keeps_circuit_closed() -> None:
    backend = MemoryStateBackend()
    inner = _FakeRunner(status=200)
    runner = CircuitBreakerRunner(inner, _breaker(backend), enforcement_mode="blocking")

    for _ in range(5):
        result = await runner.run(_request())
        assert result.status_code == 200
    assert inner.calls == 5


@pytest.mark.asyncio
async def test_host_less_urls_isolated_from_real_hosts() -> None:
    # A host-less (malformed) URL must not share a real upstream's circuit: its
    # failures trip only the sentinel key, leaving the real host closed.
    backend = MemoryStateBackend()
    inner = _FakeRunner(status=500)
    runner = CircuitBreakerRunner(inner, _breaker(backend), enforcement_mode="blocking")

    # Two failures against a host-less URL trip ONLY the sentinel circuit.
    await runner.run(_request(url="/relative/path"))
    await runner.run(_request(url="/relative/path"))
    with pytest.raises(CircuitOpenError):
        await runner.run(_request(url="/relative/path"))

    # A real host is unaffected — its own circuit is still closed.
    inner.status = 200
    result = await runner.run(_request(url="https://api.example.com/v1/x"))
    assert result.status_code == 200

    # The sentinel key is stored under a fixed, non-empty name.
    assert await backend.get(f"{_OPEN_PREFIX}{_NO_HOST_KEY}") is not None
