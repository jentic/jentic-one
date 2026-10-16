"""Unit tests for the in-process memory state backend."""

from __future__ import annotations

import pytest

from jentic_one.shared.state import MemoryStateBackend, StateCapacityError


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


async def test_bounded_backend_rejects_new_keys_when_full(clock: FakeClock) -> None:
    """At ``max_entries`` live keys a new key raises instead of evicting a live one;
    existing keys stay writable."""
    backend = MemoryStateBackend(clock=clock, max_entries=2)
    await backend.set("a", b"1", ttl_s=10.0)
    assert await backend.incr_with_ttl("b", ttl_s=10.0) == 1

    with pytest.raises(StateCapacityError):
        await backend.set("c", b"1", ttl_s=10.0)
    with pytest.raises(StateCapacityError):
        await backend.incr_with_ttl("c", ttl_s=10.0)
    with pytest.raises(StateCapacityError):
        await backend.set_if_absent("c", b"1", ttl_s=10.0)

    assert await backend.incr_with_ttl("b", ttl_s=10.0) == 2
    await backend.set("a", b"2", ttl_s=10.0)
    assert await backend.get("a") == b"2"
    assert await backend.get("b") == b"2"


async def test_bounded_backend_sweeps_expired_entries_to_make_room(clock: FakeClock) -> None:
    """Expired-but-unread entries are swept when the map is full."""
    backend = MemoryStateBackend(clock=clock, max_entries=2)
    await backend.set("short", b"1", ttl_s=1.0)
    await backend.set("long", b"1", ttl_s=100.0)
    with pytest.raises(StateCapacityError):
        await backend.set("new", b"1", ttl_s=10.0)

    clock.advance(1.5)  # "short" expired without ever being read again
    await backend.set("new", b"1", ttl_s=10.0)
    assert await backend.get("new") == b"1"
    assert await backend.get("long") == b"1"


async def test_incr_and_read_increments_then_reads_in_order(clock: FakeClock) -> None:
    """One step increments every counter (TTL set only on creation) and reads the
    rest; missing keys read as ``None``."""
    backend = MemoryStateBackend(clock=clock)
    await backend.set("marker", b"1", ttl_s=100.0)

    assert await backend.incr_and_read(["a", "b"], ["prev", "marker"], ttl_s=10.0) == (
        [1, 1],
        [None, b"1"],
    )
    clock.advance(6.0)
    assert await backend.incr_and_read(["a"], ["b"], ttl_s=10.0) == ([2], [b"1"])

    clock.advance(5.0)  # past the creation TTL: the window does not slide
    assert await backend.incr_and_read(["a"], [], ttl_s=10.0) == ([1], [])


async def test_incr_and_read_respects_the_bound(clock: FakeClock) -> None:
    backend = MemoryStateBackend(clock=clock, max_entries=1)
    await backend.incr_and_read(["a"], [], ttl_s=10.0)
    with pytest.raises(StateCapacityError):
        await backend.incr_and_read(["a", "b"], [], ttl_s=10.0)


def test_bounded_backend_rejects_non_positive_bound() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        MemoryStateBackend(max_entries=0)
