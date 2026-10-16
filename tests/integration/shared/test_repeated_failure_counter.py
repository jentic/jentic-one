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
from sqlalchemy import delete, event, func, select

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
