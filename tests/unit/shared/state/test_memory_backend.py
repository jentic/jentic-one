"""Unit tests for the in-process memory state backend."""

from __future__ import annotations

import pytest

from jentic_one.shared.state import MemoryStateBackend


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


async def test_write_only_keys_are_swept_so_memory_stays_bounded(clock: FakeClock) -> None:
    """Keys written once and never read (jti replay records) must not accumulate.

    Without the write-time sweep every one of these unique keys stays resident
    until process exit.
    """
    backend = MemoryStateBackend(clock=clock, sweep_interval_s=1.0)
    ttl_s = 5.0
    peak = 0
    for i in range(10_000):
        assert await backend.set_if_absent(f"jti:{i}", b"1", ttl_s=ttl_s) is True
        clock.advance(0.01)  # 100 writes per second
        peak = max(peak, len(backend._kv))
    # Live records are rate x (ttl + sweep interval) at most, not the 10k written.
    assert peak <= 100 * (ttl_s + 1.0) + 1


async def test_expired_keys_swept_on_any_write_after_the_interval(clock: FakeClock) -> None:
    backend = MemoryStateBackend(clock=clock, sweep_interval_s=1.0)
    for i in range(50):
        await backend.set(f"k{i}", b"v", ttl_s=0.5)
    clock.advance(1.0)
    await backend.set("fresh", b"v", ttl_s=10.0)
    assert set(backend._kv) == {"fresh"}


async def test_max_entries_is_a_hard_ceiling(clock: FakeClock) -> None:
    """With every entry still live the store evicts the one closest to expiry."""
    backend = MemoryStateBackend(clock=clock, max_entries=3)
    await backend.set("soon", b"v", ttl_s=10.0)
    await backend.set("mid", b"v", ttl_s=20.0)
    await backend.set("late", b"v", ttl_s=30.0)
    assert await backend.set_if_absent("new", b"v", ttl_s=40.0) is True

    assert len(backend._kv) == 3
    assert await backend.get("soon") is None
    assert await backend.get("mid") == b"v"
    assert await backend.get("new") == b"v"


async def test_capacity_eviction_frees_a_batch(clock: FakeClock) -> None:
    """A full store frees a tenth of its bound at once, not one entry per write."""
    backend = MemoryStateBackend(clock=clock, max_entries=20)
    for i in range(20):
        await backend.set(f"k{i:02d}", b"v", ttl_s=100.0 + i)
    await backend.set("new", b"v", ttl_s=500.0)
    assert len(backend._kv) == 19
    assert "k00" not in backend._kv
    assert "k01" not in backend._kv
    assert "k02" in backend._kv


async def test_max_entries_prefers_dropping_expired_entries(clock: FakeClock) -> None:
    backend = MemoryStateBackend(clock=clock, max_entries=2)
    await backend.set("expired", b"v", ttl_s=1.0)
    await backend.set("live", b"v", ttl_s=60.0)
    clock.advance(2.0)
    await backend.set("new", b"v", ttl_s=60.0)
    assert set(backend._kv) == {"live", "new"}


async def test_overwriting_an_existing_key_at_capacity_evicts_nothing(
    clock: FakeClock,
) -> None:
    backend = MemoryStateBackend(clock=clock, max_entries=2)
    await backend.set("a", b"1", ttl_s=60.0)
    await backend.set("b", b"1", ttl_s=60.0)
    await backend.set("a", b"2", ttl_s=60.0)
    assert await backend.get("a") == b"2"
    assert await backend.get("b") == b"1"


async def test_refilled_token_buckets_are_swept(clock: FakeClock) -> None:
    """A bucket that refilled to burst equals a fresh one, so the sweep drops it."""
    backend = MemoryStateBackend(clock=clock, sweep_interval_s=1.0)
    for i in range(100):
        await backend.token_bucket(f"caller:{i}", rate=1.0, burst=2)
    clock.advance(1.5)
    await backend.token_bucket("active", rate=1.0, burst=2)
    await backend.token_bucket("active", rate=1.0, burst=2)
    assert set(backend._buckets) == {"active"}

    # The drained bucket is kept until it has refilled, so its deficit survives.
    clock.advance(1.0)
    await backend.set("tick", b"v", ttl_s=1.0)
    assert "active" in backend._buckets
    assert (await backend.token_bucket("active", rate=1.0, burst=2)).remaining == 0


def test_max_entries_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        MemoryStateBackend(max_entries=0)
