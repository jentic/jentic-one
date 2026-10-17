"""Unit tests for the in-process memory state backend."""

from __future__ import annotations

import pytest

from jentic_one.shared.state import MemoryStateBackend
from jentic_one.shared.state.backend import _BUCKET_SWEEP_FLOOR


class FakeClock:
    """A controllable monotonic clock for deterministic TTL/refill tests."""

    def __init__(self) -> None:
        self._now = 1000.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def backend(clock: FakeClock) -> MemoryStateBackend:
    return MemoryStateBackend(clock=clock)


async def test_token_bucket_burst_then_denies(backend: MemoryStateBackend) -> None:
    """A fresh bucket allows ``burst`` immediate requests, then denies."""
    for _ in range(3):
        decision = await backend.token_bucket("caller", rate=1.0, burst=3)
        assert decision.allowed is True
        assert decision.limit == 3

    denied = await backend.token_bucket("caller", rate=1.0, burst=3)
    assert denied.allowed is False
    assert denied.remaining == 0
    assert denied.retry_after_s == pytest.approx(1.0)


async def test_token_bucket_refills_over_time(
    backend: MemoryStateBackend, clock: FakeClock
) -> None:
    """Tokens refill at ``rate`` per second, capped at ``burst``."""
    for _ in range(2):
        assert (await backend.token_bucket("c", rate=2.0, burst=2)).allowed is True
    assert (await backend.token_bucket("c", rate=2.0, burst=2)).allowed is False

    # 2 tokens/sec → 0.5s yields exactly one token back.
    clock.advance(0.5)
    assert (await backend.token_bucket("c", rate=2.0, burst=2)).allowed is True
    assert (await backend.token_bucket("c", rate=2.0, burst=2)).allowed is False


async def test_token_bucket_refill_capped_at_burst(
    backend: MemoryStateBackend, clock: FakeClock
) -> None:
    """Long idle periods never accumulate more than ``burst`` tokens."""
    assert (await backend.token_bucket("c", rate=5.0, burst=2)).allowed is True
    clock.advance(1000.0)
    # Only 2 should be available despite the long idle window.
    assert (await backend.token_bucket("c", rate=5.0, burst=2)).allowed is True
    assert (await backend.token_bucket("c", rate=5.0, burst=2)).allowed is True
    assert (await backend.token_bucket("c", rate=5.0, burst=2)).allowed is False


async def test_token_bucket_cost(backend: MemoryStateBackend) -> None:
    """A single high-cost request consumes multiple tokens."""
    decision = await backend.token_bucket("c", rate=1.0, burst=10, cost=4)
    assert decision.allowed is True
    assert decision.remaining == 6


async def test_set_if_absent_first_wins(backend: MemoryStateBackend, clock: FakeClock) -> None:
    """First claim succeeds; a second on the same key fails until TTL expiry."""
    assert await backend.set_if_absent("claim", b"a", ttl_s=10.0) is True
    assert await backend.set_if_absent("claim", b"b", ttl_s=10.0) is False

    clock.advance(10.1)
    assert await backend.set_if_absent("claim", b"c", ttl_s=10.0) is True


async def test_get_set_ttl_eviction(backend: MemoryStateBackend, clock: FakeClock) -> None:
    """``get`` returns the stored value until the TTL elapses, then ``None``."""
    await backend.set("k", b"v", ttl_s=5.0)
    assert await backend.get("k") == b"v"

    clock.advance(4.9)
    assert await backend.get("k") == b"v"

    clock.advance(0.2)
    assert await backend.get("k") is None


async def test_get_missing_key(backend: MemoryStateBackend) -> None:
    assert await backend.get("nope") is None


async def test_incr_with_ttl_accumulates_then_evicts(
    backend: MemoryStateBackend, clock: FakeClock
) -> None:
    """Counter accumulates within the window and resets after TTL eviction."""
    assert await backend.incr_with_ttl("count", ttl_s=10.0) == 1
    assert await backend.incr_with_ttl("count", ttl_s=10.0, amount=2) == 3

    clock.advance(10.1)
    assert await backend.incr_with_ttl("count", ttl_s=10.0) == 1


async def test_incr_ttl_not_extended_by_later_increments(
    backend: MemoryStateBackend, clock: FakeClock
) -> None:
    """A sliding burst must not indefinitely extend the original window."""
    assert await backend.incr_with_ttl("w", ttl_s=10.0) == 1
    clock.advance(9.0)
    assert await backend.incr_with_ttl("w", ttl_s=10.0) == 2
    clock.advance(1.1)  # original window (started at t0) has now elapsed
    assert await backend.incr_with_ttl("w", ttl_s=10.0) == 1


async def test_aclose_is_noop(backend: MemoryStateBackend) -> None:
    await backend.aclose()


async def test_token_bucket_sweeps_refilled_buckets(
    backend: MemoryStateBackend, clock: FakeClock
) -> None:
    """Caller-chosen keys cannot pin buckets forever: refilled ones are swept.

    A bucket back at capacity is indistinguishable from a fresh one, so a sweep
    drops it; a still-draining bucket keeps its state. Memory then tracks the
    keys active within one refill window, not every key ever seen.
    """
    floor = _BUCKET_SWEEP_FLOOR
    # One bucket kept below capacity (slow refill) throughout.
    await backend.token_bucket("hot", rate=0.001, burst=2)
    peak = 0
    for batch in range(10):
        for i in range(floor):
            await backend.token_bucket(f"cold-{batch}-{i}", rate=1.0, burst=1)
            peak = max(peak, len(backend._buckets))
        clock.advance(5.0)  # this batch refills; "hot" does not

    assert peak <= 2 * floor + 1
    assert "hot" in backend._buckets
    assert "cold-0-0" not in backend._buckets
    # The surviving bucket kept its drained state.
    assert (await backend.token_bucket("hot", rate=0.001, burst=2)).allowed is True
    assert (await backend.token_bucket("hot", rate=0.001, burst=2)).allowed is False
