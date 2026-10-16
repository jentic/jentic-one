"""Unit tests for the in-process memory state backend."""

from __future__ import annotations

import pytest
import structlog

from jentic_one.shared.state import (
    MemoryStateBackend,
    NamespaceBudget,
    StateBackendCapacityError,
)


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


async def test_live_key_is_never_evicted_at_the_cap(clock: FakeClock) -> None:
    """A full namespace refuses the new write; every live claim survives."""
    backend = MemoryStateBackend(clock=clock, max_entries=3)
    for key in ("ns:soon", "ns:mid", "ns:late"):
        assert await backend.set_if_absent(key, b"v", ttl_s=10.0) is True

    with pytest.raises(StateBackendCapacityError) as refused:
        await backend.set_if_absent("ns:new", b"v", ttl_s=40.0)

    assert refused.value.namespace == "ns"
    assert refused.value.retry_after_s >= 1
    for key in ("ns:soon", "ns:mid", "ns:late"):
        assert await backend.set_if_absent(key, b"v", ttl_s=10.0) is False
    assert await backend.get("ns:new") is None


async def test_every_write_kind_past_the_cap_raises(clock: FakeClock) -> None:
    backend = MemoryStateBackend(clock=clock, max_entries=1)
    await backend.set("ns:a", b"v", ttl_s=60.0)
    with pytest.raises(StateBackendCapacityError):
        await backend.set("ns:b", b"v", ttl_s=60.0)
    with pytest.raises(StateBackendCapacityError):
        await backend.incr_with_ttl("ns:c", ttl_s=60.0)


async def test_byte_budget_refuses_a_large_value(clock: FakeClock) -> None:
    """The byte budget bounds memory for large values, not just the entry count."""
    backend = MemoryStateBackend(clock=clock, max_entries=1_000, max_bytes=10_000)
    await backend.set("idem:a", b"x" * 6_000, ttl_s=60.0)
    with pytest.raises(StateBackendCapacityError):
        await backend.set("idem:b", b"x" * 6_000, ttl_s=60.0)
    assert await backend.get("idem:a") == b"x" * 6_000
    # A small value still fits in what is left.
    await backend.set("idem:c", b"x" * 100, ttl_s=60.0)


async def test_overwrite_counts_only_the_size_difference(clock: FakeClock) -> None:
    backend = MemoryStateBackend(clock=clock, max_bytes=10_000)
    await backend.set("idem:a", b"x" * 9_000, ttl_s=60.0)
    await backend.set("idem:a", b"x" * 9_500, ttl_s=60.0)
    assert await backend.get("idem:a") == b"x" * 9_500


async def test_namespaces_have_separate_budgets(clock: FakeClock) -> None:
    """Filling one key kind (CSRF nonces) never refuses another (jti records)."""
    backend = MemoryStateBackend(clock=clock, max_entries=5)
    for i in range(5):
        await backend.set(f"login-csrf:{i}", b"d", ttl_s=300.0)
    with pytest.raises(StateBackendCapacityError):
        await backend.set("login-csrf:overflow", b"d", ttl_s=300.0)

    assert await backend.set_if_absent("auth:jwt_bearer_jti:j1", b"1", ttl_s=300.0) is True
    assert await backend.set_if_absent("idem:caller:k", b"c", ttl_s=35.0) is True


async def test_namespace_budget_override(clock: FakeClock) -> None:
    backend = MemoryStateBackend(
        clock=clock,
        max_entries=1,
        namespace_budgets={"idem": NamespaceBudget(max_entries=3, max_bytes=1_000_000)},
    )
    for i in range(3):
        await backend.set(f"idem:{i}", b"v", ttl_s=60.0)
    with pytest.raises(StateBackendCapacityError):
        await backend.set("idem:3", b"v", ttl_s=60.0)
    await backend.set("cb:total:h", b"1", ttl_s=60.0)
    with pytest.raises(StateBackendCapacityError):
        await backend.set("cb:fail:h", b"1", ttl_s=60.0)


async def test_refusal_is_logged_once_per_interval(clock: FakeClock) -> None:
    backend = MemoryStateBackend(clock=clock, max_entries=1, sweep_interval_s=30.0)
    await backend.set("ns:a", b"v", ttl_s=600.0)
    with structlog.testing.capture_logs() as logs:
        for i in range(20):
            with pytest.raises(StateBackendCapacityError):
                await backend.set(f"ns:{i}x", b"v", ttl_s=600.0)
    refused = [e for e in logs if e["event"] == "memory_state_backend_capacity_refused"]
    assert len(refused) == 1
    assert refused[0]["namespace"] == "ns"


async def test_expired_entries_are_reclaimed_before_refusing(clock: FakeClock) -> None:
    backend = MemoryStateBackend(clock=clock, max_entries=2)
    await backend.set("ns:expired", b"v", ttl_s=1.0)
    await backend.set("ns:live", b"v", ttl_s=60.0)
    clock.advance(2.0)
    await backend.set("ns:new", b"v", ttl_s=60.0)
    assert set(backend._kv) == {"ns:live", "ns:new"}


async def test_overwriting_an_existing_key_at_capacity_succeeds(
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


def test_budgets_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        MemoryStateBackend(max_entries=0)
    with pytest.raises(ValueError, match="max_bytes"):
        MemoryStateBackend(max_bytes=0)
    with pytest.raises(ValueError, match="idem"):
        MemoryStateBackend(namespace_budgets={"idem": NamespaceBudget(max_entries=0, max_bytes=1)})
