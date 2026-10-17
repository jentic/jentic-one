"""Unit tests for the in-process memory state backend."""

from __future__ import annotations

import pytest
import structlog

from jentic_one.shared.state import (
    MemoryStateBackend,
    NamespaceBudget,
    StateBackendCapacityError,
)
from jentic_one.shared.state.backend import COMPACTION_STEP, ENTRY_OVERHEAD_BYTES


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
    """Keys written once and never read (single-use markers) must not accumulate.

    Without the write-time sweep every one of these unique keys stays resident
    until process exit.
    """
    backend = MemoryStateBackend(clock=clock, sweep_interval_s=1.0)
    ttl_s = 5.0
    peak = 0
    for i in range(10_000):
        assert await backend.set_if_absent(f"consent-handle-used:{i}", b"1", ttl_s=ttl_s) is True
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
    # Charged in full, the rewrite would need both entries' sizes (~20 KB).
    backend = MemoryStateBackend(clock=clock, max_bytes=11_000)
    await backend.set("idem:a", b"x" * 9_000, ttl_s=60.0)
    await backend.set("idem:a", b"x" * 9_500, ttl_s=60.0)
    assert await backend.get("idem:a") == b"x" * 9_500


async def test_namespaces_have_separate_budgets(clock: FakeClock) -> None:
    """Filling one key kind (CSRF nonces) never refuses another (single-use markers)."""
    backend = MemoryStateBackend(clock=clock, max_entries=5)
    for i in range(5):
        await backend.set(f"login-csrf:{i}", b"d", ttl_s=300.0)
    with pytest.raises(StateBackendCapacityError):
        await backend.set("login-csrf:overflow", b"d", ttl_s=300.0)

    assert await backend.set_if_absent("consent-handle-used:h1", b"1", ttl_s=300.0) is True
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


async def test_a_refused_write_does_not_scan_live_entries(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full namespace with nothing expired refuses in O(1), not O(entries)."""
    backend = MemoryStateBackend(clock=clock, max_entries=10_000)
    for i in range(10_000):
        await backend.set(f"ns:{i}", b"v", ttl_s=600.0)
    checked = 0
    is_current = backend._is_current

    def counting(item: tuple[float, int, str]) -> bool:
        nonlocal checked
        checked += 1
        return is_current(item)

    monkeypatch.setattr(backend, "_is_current", counting)
    for i in range(100):
        clock.advance(1.0)
        with pytest.raises(StateBackendCapacityError):
            await backend.set(f"ns:new-{i}", b"v", ttl_s=600.0)
    assert checked <= 100 * 4


async def test_a_sweep_touches_only_expired_entries(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = MemoryStateBackend(clock=clock, sweep_interval_s=1.0)
    for i in range(5_000):
        await backend.set(f"live:{i}", b"v", ttl_s=600.0)
    for i in range(10):
        await backend.set(f"short:{i}", b"v", ttl_s=0.5)
    deleted: list[str] = []
    delete = backend._delete

    def recording(key: str) -> None:
        deleted.append(key)
        delete(key)

    monkeypatch.setattr(backend, "_delete", recording)
    clock.advance(1.0)
    await backend.set("tick:x", b"v", ttl_s=600.0)
    assert sorted(deleted) == sorted(f"short:{i}" for i in range(10))
    assert len(backend._kv) == 5_001


async def test_retry_after_is_when_the_earliest_entry_expires(clock: FakeClock) -> None:
    backend = MemoryStateBackend(clock=clock, max_entries=2)
    await backend.set("ns:a", b"v", ttl_s=500.0)
    await backend.set("ns:b", b"v", ttl_s=90.0)
    clock.advance(10.0)
    with pytest.raises(StateBackendCapacityError) as refused:
        await backend.set("ns:c", b"v", ttl_s=60.0)
    assert refused.value.retry_after_s == 80


async def test_retry_after_is_clamped_to_an_hour(clock: FakeClock) -> None:
    backend = MemoryStateBackend(clock=clock, max_entries=1)
    await backend.set("ns:a", b"v", ttl_s=86_400.0)
    with pytest.raises(StateBackendCapacityError) as refused:
        await backend.set("ns:b", b"v", ttl_s=60.0)
    assert refused.value.retry_after_s == 3600


async def test_owner_share_refuses_only_that_owner(clock: FakeClock) -> None:
    backend = MemoryStateBackend(
        clock=clock,
        namespace_budgets={
            "idem": NamespaceBudget(max_entries=30, max_bytes=10**9, max_owner_share=0.1)
        },
    )
    for i in range(3):
        await backend.set(f"idem:alice:{i}", b"v", ttl_s=60.0)
    with pytest.raises(StateBackendCapacityError) as refused:
        await backend.set("idem:alice:3", b"v", ttl_s=60.0)
    assert refused.value.owner == "alice"
    assert refused.value.retry_after_s == 60
    await backend.set("idem:bob:0", b"v", ttl_s=60.0)

    # An expired share entry is reclaimed for its owner.
    clock.advance(61.0)
    await backend.set("idem:alice:3", b"v", ttl_s=60.0)


async def test_owner_share_bounds_bytes(clock: FakeClock) -> None:
    backend = MemoryStateBackend(
        clock=clock,
        namespace_budgets={
            "idem-body": NamespaceBudget(max_entries=1_000, max_bytes=100_000, max_owner_share=0.1)
        },
    )
    await backend.set("idem-body:alice:a", b"x" * 8_000, ttl_s=60.0)
    with pytest.raises(StateBackendCapacityError):
        await backend.set("idem-body:alice:b", b"x" * 8_000, ttl_s=60.0)
    await backend.set("idem-body:bob:a", b"x" * 8_000, ttl_s=60.0)


async def test_an_overwrite_no_larger_than_the_entry_fits_a_full_namespace(
    clock: FakeClock,
) -> None:
    # Holds one ~800-byte entry, not two.
    backend = MemoryStateBackend(clock=clock, max_entries=1, max_bytes=1_000)
    await backend.set("ns:a", b"x" * 50, ttl_s=10.0)
    await backend.set("ns:a", b"y" * 50, ttl_s=3_600.0)
    clock.advance(20.0)
    assert await backend.get("ns:a") == b"y" * 50


async def test_overwrites_do_not_grow_the_expiry_index(clock: FakeClock) -> None:
    backend = MemoryStateBackend(clock=clock)
    for i in range(10_000):
        await backend.set("ns:a", b"v", ttl_s=60.0 + i)
        await backend.incr_with_ttl("ns:counter", ttl_s=60.0)
    assert len(backend._usage["ns"].index) <= 2 * 2 + 64 + 1


def test_owner_share_must_be_a_fraction() -> None:
    with pytest.raises(ValueError, match="max_owner_share"):
        MemoryStateBackend(
            namespace_budgets={
                "idem": NamespaceBudget(max_entries=1, max_bytes=1, max_owner_share=1.5)
            }
        )


async def test_the_total_cap_bounds_all_namespaces_together(clock: FakeClock) -> None:
    """Each namespace is under its own budget, but together they hit the cap."""
    entry = len("a:k") + 1 + ENTRY_OVERHEAD_BYTES
    backend = MemoryStateBackend(clock=clock, max_total_bytes=2 * entry)
    await backend.set("a:k", b"v", ttl_s=500.0)
    await backend.set("b:k", b"v", ttl_s=90.0)
    clock.advance(10.0)

    with pytest.raises(StateBackendCapacityError) as refused:
        await backend.set("c:k", b"v", ttl_s=60.0)
    assert refused.value.total is True
    assert refused.value.namespace == "c"
    # The earliest live entry of any namespace frees room first.
    assert refused.value.retry_after_s == 80
    assert await backend.get("a:k") == b"v"
    # An overwrite no larger than the entry still fits a full backend.
    await backend.set("a:k", b"w", ttl_s=3_600.0)


async def test_the_total_cap_reclaims_expired_entries_of_other_namespaces(
    clock: FakeClock,
) -> None:
    entry = len("a:k") + 1 + ENTRY_OVERHEAD_BYTES
    backend = MemoryStateBackend(clock=clock, max_total_bytes=2 * entry)
    await backend.set("a:k", b"v", ttl_s=1.0)
    await backend.set("b:k", b"v", ttl_s=600.0)
    clock.advance(2.0)
    await backend.set("c:k", b"v", ttl_s=600.0)
    assert set(backend._kv) == {"b:k", "c:k"}


def test_the_total_cap_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_total_bytes"):
        MemoryStateBackend(max_total_bytes=0)


async def test_index_compaction_is_spread_over_writes(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No single write rebuilds a large expiry index.

    Every overwrite with a new deadline supersedes an index item. A one-shot
    rebuild would check all ~2x``entries`` items in one write (about 28 ms at
    150k entries); compaction instead checks at most ``COMPACTION_STEP`` per
    write while the index stays bounded.
    """
    entries = 20_000
    backend = MemoryStateBackend(clock=clock, max_entries=entries + 1, max_bytes=10**9)
    for i in range(entries):
        await backend.set(f"ns:{i}", b"v", ttl_s=600.0)
    checked = 0
    is_current = backend._is_current

    def counting(item: tuple[float, int, str]) -> bool:
        nonlocal checked
        checked += 1
        return is_current(item)

    monkeypatch.setattr(backend, "_is_current", counting)
    usage = backend._usage["ns"]
    most_per_write = 0
    compacted = False
    for i in range(3 * entries):
        checked = 0
        await backend.set(f"ns:{i % entries}", b"v", ttl_s=700.0 + i)
        most_per_write = max(most_per_write, checked)
        compacted = compacted or bool(usage.draining)
        assert len(usage.index) + len(usage.draining) <= 3 * entries + 2 * 64
    assert compacted
    assert most_per_write <= COMPACTION_STEP
    # Every live entry is still indexed exactly once.
    current = [item for item in usage.index + usage.draining if is_current(item)]
    assert len(current) == entries


async def test_expiry_is_found_in_a_compacting_index(clock: FakeClock) -> None:
    backend = MemoryStateBackend(clock=clock, max_entries=200, sweep_interval_s=10**6)
    for i in range(200):
        await backend.set(f"ns:{i}", b"v", ttl_s=1_000.0 + i)
    # The 265th superseding write takes the index past 2 x 200 + 64 items and
    # starts a compaction, which has moved only one step when we refuse.
    for i in range(265):
        await backend.set(f"ns:{i % 200}", b"v", ttl_s=2_000.0 + i)
    usage = backend._usage["ns"]
    assert usage.draining
    with pytest.raises(StateBackendCapacityError) as refused:
        await backend.set("ns:new", b"v", ttl_s=60.0)
    # The earliest live deadline is ns:65's (2_065 s), wherever it is indexed.
    assert refused.value.retry_after_s == 2_065
