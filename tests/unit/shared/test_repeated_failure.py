"""Unit tests for execution.repeated_failure detection.

Exercises ``maybe_emit_repeated_failure`` against a real in-memory SQLite admin
DB (no DB mocking — ``tests/arch/test_no_db_mocking.py``). Both the count and the
dedup query run on the same session, so a single admin engine is enough.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import String, Table, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

import jentic_one.shared.events.repeated_failure as repeated_failure_mod
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.repos.event_repo import EventRepository
from jentic_one.shared.config import SecurityConfig
from jentic_one.shared.events import emit_event
from jentic_one.shared.events.repeated_failure import maybe_emit_repeated_failure
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.protocols import UpstreamExecResult
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.models.events import EVENT_TYPE_SEVERITIES, EventSeverity, EventType
from jentic_one.shared.schemas import OperationInfo

_ACTOR = "agt_repeat"
_TOOLKIT = "tk_repeat0000000000000000000"
_OPERATION = "doThing"
_TRACE = "a" * 32


def _create_admin_tables(sync_conn: Connection) -> None:
    """Create the two admin tables this suite needs on SQLite.

    The ORM models carry Postgres-only ``server_default``s (``generate_ksuid``,
    ``::jsonb`` casts) that SQLite can't render in ``CREATE TABLE``. The tests
    supply explicit ids, so we drop those server defaults for the SQLite DDL —
    leaving real columns, types, and indexes intact (still a real DB, no mocking).
    """
    tables = (cast(Table, Event.__table__), cast(Table, ExecutionRecord.__table__))
    for table in tables:
        # Strip the Postgres-only server defaults so SQLite can compile the DDL;
        # the tests insert explicit ids, so the defaults are never exercised.
        # Save/restore so the shared model definition is left untouched.
        saved = {col: col.server_default for col in table.columns}
        for col in table.columns:
            col.server_default = None
        try:
            sync_conn.execute(CreateTable(table, if_not_exists=True))
        finally:
            for col, default in saved.items():
                col.server_default = default


@pytest.fixture()
async def session() -> AsyncGenerator[AsyncSession, None]:
    """A real in-memory SQLite admin session with the admin tables created."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(_create_admin_tables)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


async def _add_failures(
    session: AsyncSession,
    count: int,
    *,
    started_at: datetime | None = None,
    status: ExecutionStatus = ExecutionStatus.FAILED,
    operation_id: str = _OPERATION,
) -> None:
    started_at = started_at or datetime.now(UTC)
    for i in range(count):
        session.add(
            ExecutionRecord(
                id=f"exec_{operation_id}_{status}_{i}_{started_at.timestamp()}",
                toolkit_id=_TOOLKIT,
                trace_id=_TRACE,
                started_at=started_at,
                status=status,
                operation_id=operation_id,
                actor_id=_ACTOR,
                actor_type="agent",
            )
        )
    await session.flush()


async def _emit(
    session: AsyncSession,
    config: SecurityConfig | None = None,
    *,
    operation: OperationInfo | None = None,
) -> None:
    await maybe_emit_repeated_failure(
        session,
        actor_id=_ACTOR,
        actor_type="agent",
        toolkit_id=_TOOLKIT,
        operation=operation or OperationInfo(id=_OPERATION),
        trace_id=_TRACE,
        config=config or SecurityConfig(),
    )


async def _repeated_events(session: AsyncSession) -> list[Event]:
    return await EventRepository.list_all(
        session, event_type=[EventType.EXECUTION_REPEATED_FAILURE]
    )


async def test_below_threshold_emits_nothing(session: AsyncSession) -> None:
    config = SecurityConfig(execution_repeated_failure_threshold=5)
    await _add_failures(session, 4)
    await _emit(session, config)
    assert await _repeated_events(session) == []


async def test_at_threshold_emits_one_error(session: AsyncSession) -> None:
    config = SecurityConfig(
        execution_repeated_failure_threshold=5,
        execution_repeated_failure_critical_threshold=20,
    )
    await _add_failures(session, 5)
    await _emit(session, config)

    events = await _repeated_events(session)
    assert len(events) == 1
    assert events[0].severity == EventSeverity.ERROR.value
    assert events[0].requires_action is True
    assert events[0].data["failure_count"] == 5
    assert events[0].data["actor_id"] == _ACTOR
    assert events[0].data["toolkit_id"] == _TOOLKIT
    assert events[0].data["operation_id"] == _OPERATION
    # Cross-check against the documented severity matrix (issue #907).
    allowed = EVENT_TYPE_SEVERITIES[EventType.EXECUTION_REPEATED_FAILURE]
    assert EventSeverity(events[0].severity) in allowed


async def test_summary_renders_the_human_operation_identity(session: AsyncSession) -> None:
    """The summary shows method + path template — never the opaque op_… hash
    when the human identity is available (the id stays in ``data`` for
    machines and keeps keying the aggregation)."""
    config = SecurityConfig(execution_repeated_failure_threshold=5)
    await _add_failures(session, 5)
    await _emit(
        session,
        config,
        operation=OperationInfo(id=_OPERATION, path="/v1/things/{id}", method="GET"),
    )

    events = await _repeated_events(session)
    assert len(events) == 1
    assert "GET /v1/things/{id}" in events[0].summary
    assert _OPERATION not in events[0].summary
    assert events[0].data["operation_id"] == _OPERATION
    # The structured human identity rides ``data`` too, so the UI can render
    # and filter it the same way it does execution rows.
    assert events[0].data["operation_path"] == "/v1/things/{id}"
    assert events[0].data["operation_method"] == "GET"


async def test_summary_falls_back_to_the_id_for_legacy_operations(
    session: AsyncSession,
) -> None:
    """A legacy in-flight job carries only the id — the summary uses it rather
    than naming no operation at all (an ops signal needs an identity)."""
    config = SecurityConfig(execution_repeated_failure_threshold=5)
    await _add_failures(session, 5)
    await _emit(session, config, operation=OperationInfo(id=_OPERATION))

    events = await _repeated_events(session)
    assert len(events) == 1
    assert _OPERATION in events[0].summary


async def test_summary_fits_the_event_column_for_a_long_path(session: AsyncSession) -> None:
    """The operation label cannot overflow ``Event.summary``: the registry path
    template it renders is unbounded Text, and Postgres rejecting the INSERT
    would abort the caller's transaction and discard the execution record
    already flushed into it. SQLite accepts any width, so assert the bound."""
    summary_width = cast(String, Event.__table__.c.summary.type).length
    assert summary_width is not None, "Event.summary must be a bounded String column"
    config = SecurityConfig(execution_repeated_failure_threshold=5)
    await _add_failures(session, 5)
    await _emit(
        session,
        config,
        operation=OperationInfo(id=_OPERATION, path="/v1/" + "x" * 900, method="GET"),
    )

    events = await _repeated_events(session)
    assert len(events) == 1
    assert len(events[0].summary) <= summary_width
    # The cut is marked, so a clipped template isn't read as the real one.
    assert "…" in events[0].summary


async def test_failed_emit_rolls_back_only_its_own_writes(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The detector runs in a SAVEPOINT: a failure after the event INSERT rolls
    back just that write — the caller's already-flushed ExecutionRecords survive
    and the session stays usable (on Postgres a bare try/except would leave the
    whole transaction aborted, losing the execution record)."""

    async def emit_then_fail(*args: Any, **kwargs: Any) -> None:
        await emit_event(*args, **kwargs)
        raise RuntimeError("simulated failure after the event INSERT")

    monkeypatch.setattr(repeated_failure_mod, "emit_event", emit_then_fail)
    config = SecurityConfig(execution_repeated_failure_threshold=5)
    await _add_failures(session, 5)

    await _emit(session, config)

    assert await _repeated_events(session) == []
    records = await session.execute(select(func.count()).select_from(ExecutionRecord))
    assert records.scalar_one() == 5


async def test_repeated_calls_within_window_dedup(session: AsyncSession) -> None:
    config = SecurityConfig(execution_repeated_failure_threshold=5)
    await _add_failures(session, 6)
    await _emit(session, config)
    await _emit(session, config)
    await _emit(session, config)
    assert len(await _repeated_events(session)) == 1


async def test_critical_threshold_emits_critical(session: AsyncSession) -> None:
    config = SecurityConfig(
        execution_repeated_failure_threshold=5,
        execution_repeated_failure_critical_threshold=20,
    )
    await _add_failures(session, 20)
    await _emit(session, config)

    events = await _repeated_events(session)
    assert len(events) == 1
    assert events[0].severity == EventSeverity.CRITICAL.value
    # Cross-check against the documented severity matrix (issue #907).
    allowed = EVENT_TYPE_SEVERITIES[EventType.EXECUTION_REPEATED_FAILURE]
    assert EventSeverity(events[0].severity) in allowed


async def test_incremental_failures_escalate_error_then_critical(session: AsyncSession) -> None:
    """The realistic path: failures accumulate, crossing ERROR then CRITICAL.

    The helper runs once per failed execution, so the count crosses the ERROR
    threshold first and only later reaches CRITICAL. Severity-aware dedup must
    let the CRITICAL fire despite the pre-existing ERROR — exactly one of each.
    """
    config = SecurityConfig(
        execution_repeated_failure_threshold=5,
        execution_repeated_failure_critical_threshold=20,
    )

    # Cross the ERROR threshold first — one ERROR, no CRITICAL yet.
    await _add_failures(session, 5)
    await _emit(session, config)
    events = await _repeated_events(session)
    assert [e.severity for e in events] == [EventSeverity.ERROR.value]

    # Keep failing within the window but stay below CRITICAL — still just the
    # one ERROR (ERROR-level dedup holds).
    await _add_failures(session, 10)
    await _emit(session, config)
    events = await _repeated_events(session)
    assert [e.severity for e in events] == [EventSeverity.ERROR.value]

    # Cross the CRITICAL threshold — the CRITICAL now escalates past the ERROR.
    await _add_failures(session, 5)
    await _emit(session, config)
    severities = sorted(e.severity for e in await _repeated_events(session))
    assert severities == [EventSeverity.CRITICAL.value, EventSeverity.ERROR.value]

    # Further failures past CRITICAL don't double-emit (CRITICAL-level dedup).
    await _add_failures(session, 5)
    await _emit(session, config)
    severities = sorted(e.severity for e in await _repeated_events(session))
    assert severities == [EventSeverity.CRITICAL.value, EventSeverity.ERROR.value]


async def test_failures_outside_window_not_counted(session: AsyncSession) -> None:
    config = SecurityConfig(
        execution_repeated_failure_threshold=5,
        execution_repeated_failure_window_s=300,
    )
    old = datetime.now(UTC) - timedelta(seconds=600)
    await _add_failures(session, 10, started_at=old)
    await _emit(session, config)
    assert await _repeated_events(session) == []


async def test_completed_executions_not_counted(session: AsyncSession) -> None:
    config = SecurityConfig(execution_repeated_failure_threshold=5)
    await _add_failures(session, 10, status=ExecutionStatus.COMPLETED)
    await _emit(session, config)
    assert await _repeated_events(session) == []


async def test_new_event_after_window_rolls_off(session: AsyncSession) -> None:
    config = SecurityConfig(
        execution_repeated_failure_threshold=5,
        execution_repeated_failure_window_s=300,
    )
    await _add_failures(session, 5)
    await _emit(session, config)
    assert len(await _repeated_events(session)) == 1

    # Backdate the first event past the window so it no longer dedupes, then add
    # fresh failures and re-emit — a second event should fire.
    for event in await _repeated_events(session):
        event.created_at = datetime.now(UTC) - timedelta(seconds=600)
    await session.flush()

    await _add_failures(session, 5)
    await _emit(session, config)
    assert len(await _repeated_events(session)) == 2


async def test_missing_toolkit_or_operation_is_noop(session: AsyncSession) -> None:
    await _add_failures(session, 10)
    await maybe_emit_repeated_failure(
        session,
        actor_id=_ACTOR,
        actor_type="agent",
        toolkit_id=None,
        operation=OperationInfo(id=_OPERATION),
        trace_id=_TRACE,
        config=SecurityConfig(execution_repeated_failure_threshold=5),
    )
    assert await _repeated_events(session) == []


async def test_empty_operation_id_is_noop(session: AsyncSession) -> None:
    """An operation with an empty id has no aggregation key — nothing to count."""
    await _add_failures(session, 10)
    await _emit(
        session,
        SecurityConfig(execution_repeated_failure_threshold=5),
        operation=OperationInfo(id=""),
    )
    assert await _repeated_events(session) == []


async def test_execution_handler_emits_repeated_failure(session: AsyncSession) -> None:
    """The async-worker handler drives repeated-failure detection on a FAILED call."""

    class _FailingExecutor:
        async def execute(self, request: object, *, session: object) -> UpstreamExecResult:
            return UpstreamExecResult(
                status_code=500, body=b"boom", content_type="text/plain", duration_ms=1
            )

    config = SecurityConfig(execution_repeated_failure_threshold=3)
    handler = ExecutionHandler(executor=_FailingExecutor(), security_config=config)

    payload = {
        "execution_id": "exec_handler",
        "upstream_url": "https://api.example.com/v1/things",
        "method": "GET",
        "trace_id": _TRACE,
        "toolkit_id": _TOOLKIT,
        "operation_id": _OPERATION,
    }

    # In production the executor persists the failing ExecutionRecord before the
    # handler's lifecycle hook runs; the fake executor here does not, so seed the
    # window with enough failed records to cross the threshold at detection time.
    await _add_failures(session, 3)
    await handler.execute(
        "job_handler", session, payload=payload, created_by=_ACTOR, actor_type="agent"
    )

    events = await _repeated_events(session)
    assert len(events) == 1
    assert events[0].severity == EventSeverity.ERROR.value
