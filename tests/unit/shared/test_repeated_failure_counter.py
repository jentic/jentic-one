"""Unit tests for the shared-state gate in front of the repeated-failure scan.

``RepeatedFailureCounter`` decides whether a failure must run the authoritative
DB scan. These pin the gating rule on the in-process memory backend with a
deterministic clock (the DB-backed statement counts live in
``tests/integration/shared/test_repeated_failure_counter.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from structlog.testing import capture_logs

from jentic_one.shared.config import SecurityConfig
from jentic_one.shared.events.repeated_failure import (
    AxisKey,
    RepeatedFailureCounter,
    _possible_severities,
    build_repeated_failure_counter,
)
from jentic_one.shared.models.events import EventSeverity
from jentic_one.shared.state import REDIS_BACKEND, MemoryStateBackend, StateBackendConfig

_WINDOW = 300
_CONFIG = SecurityConfig(
    execution_repeated_failure_threshold=3,
    execution_repeated_failure_critical_threshold=6,
    execution_repeated_failure_window_s=_WINDOW,
)
_KEY = AxisKey("agt_1", "toolkit", "tk_1", "op_1")
_OTHER_KEY = AxisKey("agt_1", "toolkit", "tk_1", "op_2")
_CRED_KEY = AxisKey("agt_1", "credential", "cred_1", "op_1")


class FakeClock:
    """Wall-clock stand-in shared by the counter (buckets) and the store (TTLs)."""

    def __init__(self) -> None:
        # Start on a bucket boundary so "advance one window" means "next bucket".
        self.now = 1_800_000_000.0 - (1_800_000_000.0 % _WINDOW)

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def counter(clock: FakeClock) -> RepeatedFailureCounter:
    return RepeatedFailureCounter(MemoryStateBackend(clock=clock), clock=clock)


async def _fail(
    counter: RepeatedFailureCounter,
    times: int = 1,
    key: AxisKey = _KEY,
    extra: tuple[AxisKey, ...] = (),
) -> list[bool]:
    return [await counter.should_scan(key, extra, _CONFIG) for _ in range(times)]


def _at(clock: FakeClock) -> datetime:
    return datetime.fromtimestamp(clock.now, tz=UTC)


async def test_scan_runs_only_once_the_threshold_is_reachable(
    counter: RepeatedFailureCounter,
) -> None:
    """Failures below the threshold skip the scan; the crossing one runs it."""
    assert await _fail(counter, 3) == [False, False, True]


async def test_previous_bucket_counts_toward_the_sliding_window(
    counter: RepeatedFailureCounter, clock: FakeClock
) -> None:
    """Failures late in one bucket still count after the bucket rolls over, so
    the estimate never falls below the scan's sliding-window count."""
    clock.now += _WINDOW - 1
    assert await _fail(counter, 2) == [False, False]
    clock.now += 2  # next bucket, both earlier failures still inside the window
    assert await _fail(counter) == [True]


async def test_failures_older_than_two_buckets_expire(
    counter: RepeatedFailureCounter, clock: FakeClock
) -> None:
    """Once a failure can no longer be inside the window it stops counting."""
    assert await _fail(counter, 2) == [False, False]
    clock.now += 2 * _WINDOW
    assert await _fail(counter, 2) == [False, False]
    assert await _fail(counter) == [True]


async def test_distinct_keys_are_counted_separately(counter: RepeatedFailureCounter) -> None:
    assert await _fail(counter, 2, key=_KEY) == [False, False]
    assert await _fail(counter, 2, key=_OTHER_KEY) == [False, False]


async def test_extra_keys_feed_the_credential_axis(counter: RepeatedFailureCounter) -> None:
    """A toolkit-path failure carrying a credential counts toward that credential."""
    assert await _fail(counter, 2, key=_KEY, extra=(_CRED_KEY,)) == [False, False]
    assert await _fail(counter, key=_CRED_KEY) == [True]


async def test_a_confirmed_dedup_suppresses_scans_until_the_event_ages_out(
    counter: RepeatedFailureCounter, clock: FakeClock
) -> None:
    """After a scan found an ERROR event, later sub-critical failures skip the scan
    until that event leaves the dedup window — and not a moment longer."""
    await _fail(counter, 3)
    await counter.note_dedup_hit(_KEY, EventSeverity.ERROR, _at(clock), _CONFIG)
    assert await _fail(counter) == [False]

    clock.now += _WINDOW  # the event's dedup window has closed
    assert await _fail(counter) == [True]


async def test_an_error_marker_does_not_hide_critical_escalation(
    counter: RepeatedFailureCounter, clock: FakeClock
) -> None:
    """Reaching the critical threshold must scan even while ERROR is deduped."""
    await _fail(counter, 3)
    await counter.note_dedup_hit(_KEY, EventSeverity.ERROR, _at(clock), _CONFIG)
    assert await _fail(counter, 2) == [False, False]
    assert await _fail(counter) == [True]  # estimate hits critical (6)

    await counter.note_dedup_hit(_KEY, EventSeverity.CRITICAL, _at(clock), _CONFIG)
    assert await _fail(counter) == [False]


async def test_sustained_critical_keeps_the_gate_closed_after_the_error_ages_out(
    counter: RepeatedFailureCounter, clock: FakeClock
) -> None:
    """With the current bucket alone at/above the critical threshold, only a
    CRITICAL marker matters: it covers ERROR, so the gate stays closed after
    the original ERROR event (and its marker) has left the window."""
    await _fail(counter, 3)
    await counter.note_dedup_hit(_KEY, EventSeverity.ERROR, _at(clock), _CONFIG)
    clock.now += 30
    await _fail(counter, 3)  # current bucket reaches critical (6)
    await counter.note_dedup_hit(_KEY, EventSeverity.CRITICAL, _at(clock), _CONFIG)

    clock.now += _WINDOW - 10  # ERROR marker expired, CRITICAL marker still live
    await _fail(counter, _CONFIG.execution_repeated_failure_critical_threshold)
    assert await _fail(counter, 5) == [False] * 5


def test_possible_severities_with_the_current_bucket_at_critical() -> None:
    """The counter yields no lower bound on the scan's count (a counted failure
    can roll back or start before the window), so ERROR stays possible even when
    the current bucket alone is at the critical threshold; the sustained case is
    handled by CRITICAL markers covering ERROR instead."""
    critical = _CONFIG.execution_repeated_failure_critical_threshold
    assert _possible_severities(critical, _CONFIG) == [
        EventSeverity.ERROR,
        EventSeverity.CRITICAL,
    ]
    assert _possible_severities(_CONFIG.execution_repeated_failure_threshold, _CONFIG) == [
        EventSeverity.ERROR
    ]
    only_critical = SecurityConfig(
        execution_repeated_failure_threshold=6,
        execution_repeated_failure_critical_threshold=6,
    )
    assert _possible_severities(6, only_critical) == [EventSeverity.CRITICAL]


async def test_a_stale_dedup_hit_sets_no_marker(
    counter: RepeatedFailureCounter, clock: FakeClock
) -> None:
    """An event already outside the window (naive timestamps read as UTC) is ignored."""
    stale = datetime.fromtimestamp(clock.now - _WINDOW - 1, tz=UTC).replace(tzinfo=None)
    await _fail(counter, 3)
    await counter.note_dedup_hit(_KEY, EventSeverity.ERROR, stale, _CONFIG)
    assert await _fail(counter) == [True]


class _BrokenStore(MemoryStateBackend):
    """A backend that is down until ``up`` is set (e.g. Redis restarting)."""

    def __init__(self, *, clock: FakeClock) -> None:
        super().__init__(clock=clock)
        self.up = False

    async def set(self, key: str, value: bytes, *, ttl_s: float) -> None:
        if not self.up:
            raise ConnectionError("down")
        await super().set(key, value, ttl_s=ttl_s)

    async def incr_and_read(
        self, incr_keys: Sequence[str], read_keys: Sequence[str], *, ttl_s: float
    ) -> tuple[list[int], list[bytes | None]]:
        if not self.up:
            raise ConnectionError("down")
        return await super().incr_and_read(incr_keys, read_keys, ttl_s=ttl_s)


async def test_backend_failure_fails_open_and_warns_once(clock: FakeClock) -> None:
    """A dead backend scans on every failure; only the first error is a warning."""
    counter = RepeatedFailureCounter(_BrokenStore(clock=clock), clock=clock)
    with capture_logs() as logs:
        assert await _fail(counter, 3) == [True, True, True]
        await counter.note_dedup_hit(_KEY, EventSeverity.ERROR, _at(clock), _CONFIG)

    unavailable = [e for e in logs if e["event"] == "repeated_failure_counter_unavailable"]
    assert [e["log_level"] for e in unavailable] == ["warning", "debug", "debug", "debug"]
    # An expected degraded path: the warning names the error, no traceback.
    assert unavailable[0]["error_type"] == "ConnectionError"
    assert "exc_info" not in unavailable[0]
    assert all(e.get("exc_info") for e in unavailable[1:])


async def test_recovery_logs_once_and_rearms_the_warning(clock: FakeClock) -> None:
    """After the backend comes back, the next outage warns again (not debug)."""
    store = _BrokenStore(clock=clock)
    counter = RepeatedFailureCounter(store, clock=clock)
    with capture_logs() as logs:
        await _fail(counter, 2)  # outage 1
        store.up = True
        await _fail(counter, 2)  # recovered
        store.up = False
        await _fail(counter)  # outage 2

    events = [
        (e["event"], e["log_level"])
        for e in logs
        if e["event"].startswith("repeated_failure_counter_")
    ]
    assert events == [
        ("repeated_failure_counter_unavailable", "warning"),
        ("repeated_failure_counter_unavailable", "debug"),
        ("repeated_failure_counter_recovered", "info"),
        ("repeated_failure_counter_unavailable", "warning"),
    ]


async def test_a_full_bounded_store_fails_open(clock: FakeClock) -> None:
    """At the memory bound a new key is not tracked — its failures scan instead
    of being silently undercounted (and, below, so does every other key)."""
    counter = RepeatedFailureCounter(MemoryStateBackend(clock=clock, max_entries=1), clock=clock)
    assert await _fail(counter, key=_KEY) == [False]
    assert await _fail(counter, key=_OTHER_KEY) == [True]


async def test_gate_stays_open_for_two_windows_after_a_backend_error(clock: FakeClock) -> None:
    """A dropped increment could leave a bucket short while it is still inside a
    window, so every failure scans for two windows after the last error."""
    counter = RepeatedFailureCounter(MemoryStateBackend(clock=clock, max_entries=1), clock=clock)
    await _fail(counter, key=_KEY)
    assert await _fail(counter, key=_OTHER_KEY) == [True]  # untracked → error
    assert await _fail(counter, key=_KEY) == [True]  # tracked, but degraded

    clock.now += 2 * _WINDOW  # the dropped increment can no longer be in a window
    assert await _fail(counter, key=_KEY) == [False]


async def test_a_full_bounded_store_degrades_every_key(clock: FakeClock) -> None:
    """Pins the documented capacity behaviour: one rejected key makes *every*
    key scan, warm ones included, until two windows after the last rejection."""
    counter = RepeatedFailureCounter(MemoryStateBackend(clock=clock, max_entries=2), clock=clock)
    assert await _fail(counter, key=_KEY) == [False]  # 1 bucket entry
    assert await _fail(counter, key=_OTHER_KEY) == [False]  # 2: full
    assert await _fail(counter, key=_CRED_KEY) == [True]  # rejected
    assert await _fail(counter, key=_KEY) == [True]  # warm key, still degraded
    clock.now += _WINDOW
    assert await _fail(counter, key=_CRED_KEY) == [True]  # rejected again: renewed
    clock.now += 2 * _WINDOW - 1
    assert await _fail(counter, key=_KEY) == [True]


def test_memory_backend_gets_a_dedicated_bounded_store() -> None:
    shared = MemoryStateBackend()
    config = SecurityConfig(execution_repeated_failure_counter_max_entries=7)
    counter = build_repeated_failure_counter(StateBackendConfig(), shared, config)
    assert counter is not None
    store = counter._store
    assert isinstance(store, MemoryStateBackend)
    assert store is not shared
    assert store._budget("repeated_failure").max_entries == 7


def test_redis_backend_is_shared_as_is() -> None:
    shared = MemoryStateBackend()  # stands in for the process's Redis backend
    counter = build_repeated_failure_counter(
        StateBackendConfig(backend=REDIS_BACKEND), shared, SecurityConfig()
    )
    assert counter is not None
    assert counter._store is shared


def test_memory_backend_across_several_processes_builds_no_gate() -> None:
    """Per-process memory counts would each see only a share of a key's failures
    and could hide an emission, so several processes on memory scan every failure;
    Redis stays shared whatever the process count."""
    shared = MemoryStateBackend()
    config = SecurityConfig()
    assert build_repeated_failure_counter(StateBackendConfig(), shared, config, processes=2) is None
    assert (
        build_repeated_failure_counter(StateBackendConfig(), shared, config, processes=1)
        is not None
    )
    redis_counter = build_repeated_failure_counter(
        StateBackendConfig(backend=REDIS_BACKEND), shared, config, processes=4
    )
    assert redis_counter is not None
    assert redis_counter._store is shared
