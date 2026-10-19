"""Unit tests for the per-upstream circuit breaker (§05 R5.1).

Crossing ``failure_ratio`` within ``window_s`` (past ``min_calls``) opens the
latch; the breaker then fast-fails for ``cooldown_s`` and self-heals when the
latch TTL lapses. Failures below ``min_calls`` never trip it.
"""

from __future__ import annotations

import pytest
import structlog

from jentic_one.shared.resilience import CircuitBreaker, CircuitState
from jentic_one.shared.state.backend import MemoryStateBackend


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _breaker(backend: MemoryStateBackend) -> CircuitBreaker:
    return CircuitBreaker(backend, failure_ratio=0.5, min_calls=4, window_s=30, cooldown_s=15)


@pytest.mark.asyncio
async def test_trips_after_failure_ratio_crossed() -> None:
    backend = MemoryStateBackend(clock=_Clock())
    breaker = _breaker(backend)

    assert (await breaker.allow("api.example.com")).allowed

    # 4 calls, 3 failures => ratio 0.75 >= 0.5, past min_calls 4 => trips.
    await breaker.record("api.example.com", ok=False)
    await breaker.record("api.example.com", ok=False)
    await breaker.record("api.example.com", ok=True)
    await breaker.record("api.example.com", ok=False)

    decision = await breaker.allow("api.example.com")
    assert not decision.allowed
    assert decision.state is CircuitState.OPEN
    assert decision.retry_after_s == 15


@pytest.mark.asyncio
async def test_does_not_trip_below_min_calls() -> None:
    backend = MemoryStateBackend(clock=_Clock())
    breaker = _breaker(backend)

    # 3 calls all failing: 100% ratio but below min_calls=4 => stays closed.
    for _ in range(3):
        await breaker.record("api.example.com", ok=False)

    assert (await breaker.allow("api.example.com")).allowed


@pytest.mark.asyncio
async def test_self_heals_after_cooldown() -> None:
    clock = _Clock()
    backend = MemoryStateBackend(clock=clock)
    breaker = _breaker(backend)

    for _ in range(4):
        await breaker.record("api.example.com", ok=False)
    assert not (await breaker.allow("api.example.com")).allowed

    clock.advance(16.0)  # past the 15s cooldown latch TTL
    assert (await breaker.allow("api.example.com")).allowed


@pytest.mark.asyncio
async def test_per_host_isolation() -> None:
    backend = MemoryStateBackend(clock=_Clock())
    breaker = _breaker(backend)

    for _ in range(4):
        await breaker.record("bad.example.com", ok=False)

    assert not (await breaker.allow("bad.example.com")).allowed
    # A different host has its own counters/latch.
    assert (await breaker.allow("good.example.com")).allowed


@pytest.mark.asyncio
async def test_record_skips_when_the_store_is_full() -> None:
    """A refused counter write after the upstream call never fails the request."""
    backend = MemoryStateBackend(clock=_Clock(), max_entries=1)
    breaker = _breaker(backend)
    await breaker.record("a.example.com", ok=True)

    await breaker.record("b.example.com", ok=False)

    assert (await breaker.allow("b.example.com")).allowed


@pytest.mark.asyncio
async def test_a_refused_open_latch_is_logged_as_such() -> None:
    """A trip the store refuses is told apart from a refused counter."""
    backend = MemoryStateBackend(clock=_Clock(), max_entries=2)
    breaker = _breaker(backend)

    with structlog.testing.capture_logs() as logs:
        for _ in range(4):
            await breaker.record("api.example.com", ok=False)

    skipped = [e for e in logs if e["event"] == "circuit_breaker_record_skipped"]
    assert [e["reason"] for e in skipped] == ["open_latch_refused"]
    assert (await breaker.allow("api.example.com")).allowed
