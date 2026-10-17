"""Integration tests: the hot-path event and execution writes skip the ORM flush (#1822).

``EventRepository.create`` (behind ``emit_event``) and ``record_execution`` write
append-only rows with a Core ``INSERT``. These tests pin three things on both
backends: no unit-of-work flush runs for those writes; the rows carry every
default the ORM would have applied; and a failed insert has the transaction
semantics of a failed flush. That last one rests on private SQLAlchemy API in
``execute_core_insert``, so a SQLAlchemy upgrade that changes it fails here.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.exc import IntegrityError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.events import emit_credential_access, emit_event
from jentic_one.shared.executions import record_execution
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.schemas import OperationInfo

pytestmark = pytest.mark.integration

_PREFIX = "exec_core1822_"
_ACTOR = "agt_core_insert_1822"
_TRACE = "c" * 32


@pytest.fixture(autouse=True)
async def clean_rows(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _cleanup() -> None:
        async with admin_db.session() as session:
            await session.execute(delete(Event).where(Event.actor_id == _ACTOR))
            await session.execute(
                delete(ExecutionRecord).where(ExecutionRecord.id.like(f"{_PREFIX}%"))
            )
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


def _count_flushes(session: AsyncSession) -> list[int]:
    """Count ``before_flush`` dispatches on ``session``. The list holds one int."""
    counter = [0]

    def _on_flush(*_: Any) -> None:
        counter[0] += 1

    event.listen(session.sync_session, "before_flush", _on_flush)
    return counter


async def _record(session: AsyncSession, execution_id: str) -> str:
    return await record_execution(
        session,
        execution_id=execution_id,
        toolkit_id=None,
        trace_id=_TRACE,
        started_at=datetime.now(UTC),
        status=ExecutionStatus.COMPLETED,
        duration_ms=12,
        operation=OperationInfo(id="op_1822", path="/v1/things", method="GET"),
        api_vendor="example.com",
        http_status=200,
        actor_id=_ACTOR,
        actor_type="agent",
        credential_id="cred_1822",
    )


@pytest.mark.asyncio
async def test_hot_path_writes_skip_orm_flush_and_keep_orm_defaults(
    admin_db: DatabaseSession,
) -> None:
    """The audit event, the execution record and its lifecycle event all land
    without one ORM flush, carrying the ids and defaults the ORM would mint."""
    before = datetime.now(UTC) - timedelta(seconds=1)
    execution_id = f"{_PREFIX}ok"
    async with admin_db.session() as session:
        flushes = _count_flushes(session)
        audit_id = await emit_credential_access(
            session,
            actor_id=_ACTOR,
            actor_type="agent",
            credential_id="cred_1822",
            provider="example",
            wire_type="bearer",
            api_vendor="example.com",
            api_name="things",
            api_version="1",
            trace_id=_TRACE,
        )
        assert await _record(session, execution_id) == execution_id
        lifecycle_id = await emit_event(
            session,
            type=EventType.EXECUTION_COMPLETED,
            severity=EventSeverity.INFO,
            summary="execution completed",
            created_by=_ACTOR,
            actor_id=_ACTOR,
            actor_type="agent",
            execution_id=execution_id,
        )
        await session.commit()
        assert flushes == [0]

    async with admin_db.session() as session:
        events = {
            e.id: e for e in (await session.scalars(select(Event).where(Event.actor_id == _ACTOR)))
        }
        record = await session.get(ExecutionRecord, execution_id)

    assert set(events) == {audit_id, lifecycle_id}
    for event_id, row in events.items():
        assert event_id.startswith("evt_") and len(event_id) == 28
        assert row.created_at >= before
        assert row.updated_at == row.created_at
        assert row.requires_action is False
    assert events[audit_id].type == EventType.CREDENTIAL_ACCESSED
    assert events[audit_id].data["credential_id"] == "cred_1822"
    # ``data`` defaults to an empty object, never NULL, when the emitter passes none.
    assert events[lifecycle_id].data == {}
    assert events[lifecycle_id].execution_id == execution_id

    assert record is not None
    assert record.created_at >= before
    assert record.updated_at == record.created_at
    assert record.operation_path == "/v1/things"
    assert record.credential_id == "cred_1822"
    assert record.toolkit_id is None
    assert record.pinned_revisions is None


@pytest.mark.asyncio
async def test_pending_orm_rows_flush_before_the_core_insert(admin_db: DatabaseSession) -> None:
    """Rows the caller ``session.add``-ed earlier still reach the database first,
    as they would when the write was itself an ORM flush."""
    async with admin_db.session() as session:
        pending = ExecutionRecord(
            id=f"{_PREFIX}pending",
            trace_id=_TRACE,
            started_at=datetime.now(UTC),
            status=ExecutionStatus.FAILED,
            actor_id=_ACTOR,
            actor_type="agent",
        )
        session.add(pending)
        flushes = _count_flushes(session)
        await _record(session, f"{_PREFIX}after")
        assert flushes == [1]
        assert pending not in session.new
        await session.commit()


@pytest.mark.asyncio
async def test_failed_insert_poisons_the_session_like_a_failed_flush(
    admin_db: DatabaseSession,
) -> None:
    """A swallowed insert failure must not let the rest of the transaction commit
    silently. Same as a failed flush: the session refuses further work until
    rolled back, and nothing from the transaction persists."""
    async with admin_db.transaction() as session:
        await _record(session, f"{_PREFIX}first")
        with pytest.raises(IntegrityError):
            await _record(session, f"{_PREFIX}first")
        with pytest.raises(PendingRollbackError):
            await _record(session, f"{_PREFIX}second")
        with pytest.raises(PendingRollbackError):
            await session.commit()
        await session.rollback()

    async with admin_db.session() as session:
        assert await session.get(ExecutionRecord, f"{_PREFIX}first") is None
        assert await session.get(ExecutionRecord, f"{_PREFIX}second") is None


@pytest.mark.asyncio
async def test_failed_insert_inside_savepoint_rolls_back_only_the_savepoint(
    admin_db: DatabaseSession,
) -> None:
    """Inside ``begin_nested()`` a failed insert costs only the savepoint. The
    enclosing transaction keeps its earlier writes and goes on to commit."""
    async with admin_db.transaction() as session:
        await _record(session, f"{_PREFIX}outer")
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await _record(session, f"{_PREFIX}outer")
        await _record(session, f"{_PREFIX}after_savepoint")

    async with admin_db.session() as session:
        assert await session.get(ExecutionRecord, f"{_PREFIX}outer") is not None
        assert await session.get(ExecutionRecord, f"{_PREFIX}after_savepoint") is not None
