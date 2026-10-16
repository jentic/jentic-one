"""Integration tests: the shared-state counter gates the repeated-failure scan (#1708).

The repeated-failure detector's ``COUNT(*)`` + dedup query is the authoritative
confirmation; the shared-state counter is only a cheap pre-filter in front of
it. These tests count the SQL statements the detector issues against a real
database (no DB mocking) to pin that:

* a failure below the threshold issues **zero** statements,
* the threshold-crossing failure runs the confirmation and emits exactly once,
* the dedup still holds (and, once confirmed, suppresses further scans),
* a broken counter backend fails open to the unconditional scan.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, event, func, select, update

import jentic_one.shared.events.repeated_failure as repeated_failure_mod
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.shared.config import SecurityConfig
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.events.repeated_failure import (
    RepeatedFailureCounter,
    maybe_emit_repeated_failure,
)
from jentic_one.shared.executions import record_execution
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.models.events import EventType
from jentic_one.shared.schemas import OperationInfo
from jentic_one.shared.state import MemoryStateBackend

pytestmark = pytest.mark.integration

_PREFIX = "integ-rf-counter-"
_ACTOR = "agt_rf_counter"
_TOOLKIT = "tk_rf_counter"
_CREDENTIAL = "cred_rf_counter"
_OPERATION = OperationInfo(id="op_rf_counter", path="/v1/things", method="GET")
_OTHER_OPERATION = OperationInfo(id="op_rf_counter_other", path="/v1/other", method="GET")
_CONFIG = SecurityConfig(
    execution_repeated_failure_threshold=3,
    execution_repeated_failure_critical_threshold=50,
    execution_repeated_failure_window_s=300,
)


@pytest.fixture(autouse=True)
async def clean_rows(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _cleanup() -> None:
        async with admin_db.transaction() as session:
            await session.execute(
                delete(ExecutionRecord).where(ExecutionRecord.id.like(f"{_PREFIX}%"))
            )
            await session.execute(delete(Event).where(Event.actor_id == _ACTOR))

    await _cleanup()
    yield
    await _cleanup()


@contextmanager
def _statements(admin_db: DatabaseSession) -> Iterator[list[str]]:
    """Capture every SQL statement the admin engine issues inside the block."""
    captured: list[str] = []
    engine = admin_db.engine.sync_engine

    def _on_execute(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        captured.append(statement)

    event.listen(engine, "before_cursor_execute", _on_execute)
    try:
        yield captured
    finally:
        event.remove(engine, "before_cursor_execute", _on_execute)


class _Failures:
    """Records one FAILED execution per call, then runs the detector on it."""

    def __init__(self, admin_db: DatabaseSession, counter: RepeatedFailureCounter | None) -> None:
        self._db = admin_db
        self._counter = counter
        self._seq = 0

    async def fail(
        self,
        *,
        operation: OperationInfo = _OPERATION,
        toolkit_id: str | None = _TOOLKIT,
        credential_id: str | None = None,
    ) -> list[str]:
        """Persist a failure and return the statements the detector issued for it."""
        async with self._db.transaction() as session:
            await record_execution(
                session,
                execution_id=f"{_PREFIX}{self._seq}",
                toolkit_id=toolkit_id,
                trace_id="a" * 32,
                started_at=datetime.now(UTC),
                status=ExecutionStatus.FAILED,
                operation=operation,
                actor_id=_ACTOR,
                actor_type="agent",
                credential_id=credential_id,
            )
            self._seq += 1
            with _statements(self._db) as captured:
                await maybe_emit_repeated_failure(
                    session,
                    actor_id=_ACTOR,
                    actor_type="agent",
                    toolkit_id=toolkit_id,
                    credential_id=credential_id,
                    operation=operation,
                    trace_id="a" * 32,
                    config=_CONFIG,
                    failure_counter=self._counter,
                )
        return captured


def _scans(statements: list[str]) -> list[str]:
    """The detector's ``COUNT(*)`` over ``execution_records``."""
    return [s for s in statements if "count(" in s.lower() and "execution_records" in s]


async def _repeated_failure_events(admin_db: DatabaseSession) -> int:
    async with admin_db.session() as session:
        result = await session.execute(
            select(func.count())
            .select_from(Event)
            .where(
                Event.type == EventType.EXECUTION_REPEATED_FAILURE,
                Event.actor_id == _ACTOR,
            )
        )
        return int(result.scalar_one())


@pytest.mark.asyncio
async def test_below_threshold_failures_issue_no_statements(admin_db: DatabaseSession) -> None:
    """Every failure under the threshold costs zero DB statements; the crossing
    failure runs the confirmation scan and emits exactly one event."""
    failures = _Failures(admin_db, RepeatedFailureCounter(MemoryStateBackend()))

    for _ in range(_CONFIG.execution_repeated_failure_threshold - 1):
        assert await failures.fail() == []
    assert await _repeated_failure_events(admin_db) == 0

    crossing = await failures.fail()
    assert len(_scans(crossing)) == 1
    assert await _repeated_failure_events(admin_db) == 1


@pytest.mark.asyncio
async def test_scan_renders_the_status_filter_inline(admin_db: DatabaseSession) -> None:
    """The scan's ``status`` filter is a SQL literal, not a bind parameter, so a
    generic prepared plan can still match the ``status = 'failed'`` partial
    indexes on Postgres."""
    failures = _Failures(admin_db, None)
    (scan,) = _scans(await failures.fail())
    assert "status = 'failed'" in scan


@pytest.mark.asyncio
async def test_dedup_holds_and_a_confirmed_dedup_suppresses_further_scans(
    admin_db: DatabaseSession,
) -> None:
    """Past the threshold the dedup still keeps it to one event; once a scan has
    confirmed the existing event, later failures in the window skip the DB."""
    failures = _Failures(admin_db, RepeatedFailureCounter(MemoryStateBackend()))
    for _ in range(_CONFIG.execution_repeated_failure_threshold):
        await failures.fail()
    assert await _repeated_failure_events(admin_db) == 1

    # The next failure re-confirms: the scan runs, the dedup finds the event.
    confirming = await failures.fail()
    assert len(_scans(confirming)) == 1
    assert await _repeated_failure_events(admin_db) == 1

    # The confirmed dedup is remembered for the event's window: no more scans.
    for _ in range(3):
        assert await failures.fail() == []
    assert await _repeated_failure_events(admin_db) == 1


@pytest.mark.asyncio
async def test_distinct_keys_are_counted_separately(admin_db: DatabaseSession) -> None:
    """Failures spread across two operations never pool into one key."""
    failures = _Failures(admin_db, RepeatedFailureCounter(MemoryStateBackend()))
    below = _CONFIG.execution_repeated_failure_threshold - 1
    for _ in range(below):
        assert await failures.fail(operation=_OPERATION) == []
        assert await failures.fail(operation=_OTHER_OPERATION) == []
    assert await _repeated_failure_events(admin_db) == 0


@pytest.mark.asyncio
async def test_toolkit_path_failures_count_toward_the_credential_key(
    admin_db: DatabaseSession,
) -> None:
    """The credential-axis scan counts every record carrying the credential, toolkit
    path included — so the counter must too, or it would hide a due emission."""
    failures = _Failures(admin_db, RepeatedFailureCounter(MemoryStateBackend()))
    for _ in range(_CONFIG.execution_repeated_failure_threshold - 1):
        await failures.fail(toolkit_id=_TOOLKIT, credential_id=_CREDENTIAL)

    direct = await failures.fail(toolkit_id=None, credential_id=_CREDENTIAL)
    assert len(_scans(direct)) == 1
    assert await _repeated_failure_events(admin_db) == 1


class _BrokenStore(MemoryStateBackend):
    """A shared-state backend whose every call fails (e.g. Redis is down)."""

    async def get(self, key: str) -> bytes | None:
        raise ConnectionError("state backend down")

    async def set(self, key: str, value: bytes, *, ttl_s: float) -> None:
        raise ConnectionError("state backend down")

    async def incr_with_ttl(self, key: str, *, ttl_s: float, amount: int = 1) -> int:
        raise ConnectionError("state backend down")


@pytest.mark.asyncio
async def test_backend_failure_fails_open_to_the_scan(admin_db: DatabaseSession) -> None:
    """A dead counter backend never hides an emission: every failure scans, as
    without a counter, and the event still fires exactly once."""
    failures = _Failures(admin_db, RepeatedFailureCounter(_BrokenStore()))
    for _ in range(_CONFIG.execution_repeated_failure_threshold - 1):
        assert len(_scans(await failures.fail())) == 1
    assert await _repeated_failure_events(admin_db) == 0

    assert len(_scans(await failures.fail())) == 1
    assert await _repeated_failure_events(admin_db) == 1


@pytest.mark.asyncio
async def test_without_a_counter_every_failure_scans(admin_db: DatabaseSession) -> None:
    """No counter wired (``failure_counter=None``) keeps the unconditional scan."""
    failures = _Failures(admin_db, None)
    for _ in range(_CONFIG.execution_repeated_failure_threshold):
        assert len(_scans(await failures.fail())) == 1
    assert await _repeated_failure_events(admin_db) == 1


class _VirtualClock:
    """Epoch-seconds clock the test advances by hand (counter buckets + TTLs)."""

    def __init__(self, start: float) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_sustained_critical_outage_keeps_the_gate_closed(
    admin_db: DatabaseSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins the sustained-outage case the gate exists for: one failure every 2s
    for ~12 minutes with the default thresholds (5 / 20, 300s window).

    ERROR fires on the 5th failure and escalates to CRITICAL on the 20th, at
    once. After that, scans happen only when a dedup marker expires — not on
    every failure once the first ERROR event leaves the window (an active
    CRITICAL also deduplicates ERROR). The timeline runs one day in the past on
    a virtual clock so the scan's window, the records' ``started_at``, the
    events' ``created_at``, and the counter all share one time base.
    """
    config = SecurityConfig()
    window = config.execution_repeated_failure_window_s
    start = datetime.now(UTC).timestamp() - 86_400
    real_start = datetime.now(UTC)
    clock = _VirtualClock(start)
    monkeypatch.setattr(
        repeated_failure_mod, "_utcnow", lambda: datetime.fromtimestamp(clock.now, tz=UTC)
    )
    counter = RepeatedFailureCounter(MemoryStateBackend(clock=clock), clock=clock)

    scanned_at: list[float] = []
    for i in range(1, 361):
        clock.now = start + 2 * i
        virtual_now = datetime.fromtimestamp(clock.now, tz=UTC)
        async with admin_db.transaction() as session:
            await record_execution(
                session,
                execution_id=f"{_PREFIX}outage-{i}",
                toolkit_id=_TOOLKIT,
                trace_id="a" * 32,
                started_at=virtual_now,
                status=ExecutionStatus.FAILED,
                operation=_OPERATION,
                actor_id=_ACTOR,
                actor_type="agent",
            )
            with _statements(admin_db) as captured:
                await maybe_emit_repeated_failure(
                    session,
                    actor_id=_ACTOR,
                    actor_type="agent",
                    toolkit_id=_TOOLKIT,
                    operation=_OPERATION,
                    trace_id="a" * 32,
                    config=config,
                    failure_counter=counter,
                )
            # Stamp any event emitted just now with the virtual time.
            await session.execute(
                update(Event)
                .where(Event.actor_id == _ACTOR, Event.created_at >= real_start)
                .values(created_at=virtual_now)
            )
        if _scans(captured):
            scanned_at.append(clock.now - start)

    async with admin_db.session() as session:
        rows = (
            await session.execute(
                select(Event.severity, Event.created_at)
                .where(
                    Event.type == EventType.EXECUTION_REPEATED_FAILURE,
                    Event.actor_id == _ACTOR,
                )
                .order_by(Event.created_at)
            )
        ).all()
    emitted = [(sev, round(ts.timestamp() - start)) for sev, ts in rows]

    # First window: one ERROR on the 5th failure, CRITICAL on the 20th (no lag).
    first_window = [(sev, t) for sev, t in emitted if t <= window]
    assert first_window == [("error", 10), ("critical", 40)]
    # No ERROR ever again while CRITICAL stays active; CRITICAL re-fires once per
    # window as each one ages out.
    assert [sev for sev, _ in emitted].count("error") == 1
    assert all(sev == "critical" for sev, _ in emitted[1:])

    # Scans: emit + confirm for ERROR and for CRITICAL, then — per CRITICAL
    # marker expiry — the scan that still sees the aging event, the re-emit and
    # its confirm. Never one per failure.
    marker_expiries = len(emitted) - 2
    assert len(scanned_at) <= 4 + 3 * marker_expiries, scanned_at
    # In particular nothing scans between the ERROR event leaving the window
    # (t=310) and the first CRITICAL leaving it (t=340).
    assert not [t for t in scanned_at if 310 <= t < 340], scanned_at
