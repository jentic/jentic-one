"""Integration test: the repeated-failure detector's SAVEPOINT on a real database.

The detector is best-effort: a failure inside it must never lose the
``ExecutionRecord`` the caller already flushed into the same transaction. On
PostgreSQL a failed *statement* aborts the whole transaction, so a bare
``try/except`` would swallow the error yet leave the transaction poisoned — the
caller's commit then fails and the record is gone. The unit suite can only
simulate that with a Python exception on SQLite; this runs a real DB-level
error inside the detector against both backends and asserts the record commits.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

import jentic_one.shared.events.repeated_failure as repeated_failure_mod
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.shared.config import SecurityConfig
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.events.repeated_failure import maybe_emit_repeated_failure
from jentic_one.shared.executions import record_execution
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.schemas import OperationInfo

pytestmark = pytest.mark.integration

_PREFIX = "integ-rf-savepoint-"
_ACTOR = "agt_rf_savepoint"
_TOOLKIT = "tk_rf_savepoint"
_OPERATION = OperationInfo(id="op_rf_savepoint", path="/v1/things", method="GET")


@pytest.fixture(autouse=True)
async def clean_records(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _cleanup() -> None:
        async with admin_db.session() as session:
            await session.execute(
                delete(ExecutionRecord).where(ExecutionRecord.id.like(f"{_PREFIX}%"))
            )
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


@pytest.mark.asyncio
async def test_db_error_inside_the_detector_keeps_the_flushed_records(
    admin_db: DatabaseSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def emit_hits_a_db_error(session: AsyncSession, **_: Any) -> None:
        # A statement the database itself rejects — on PostgreSQL this aborts
        # the enclosing transaction unless it runs inside a SAVEPOINT.
        await session.execute(text("SELECT * FROM jentic_rf_no_such_table"))

    monkeypatch.setattr(repeated_failure_mod, "emit_event", emit_hits_a_db_error)
    config = SecurityConfig(execution_repeated_failure_threshold=3)

    async with admin_db.transaction() as session:
        for i in range(3):
            await record_execution(
                session,
                execution_id=f"{_PREFIX}{i}",
                toolkit_id=_TOOLKIT,
                trace_id="a" * 32,
                started_at=datetime.now(UTC),
                status=ExecutionStatus.FAILED,
                operation=_OPERATION,
                actor_id=_ACTOR,
                actor_type="agent",
            )
        # Threshold reached → the detector reaches the (failing) emit.
        await maybe_emit_repeated_failure(
            session,
            actor_id=_ACTOR,
            actor_type="agent",
            toolkit_id=_TOOLKIT,
            operation=_OPERATION,
            trace_id="a" * 32,
            config=config,
        )
        # The transaction is still usable after the swallowed failure.
        await session.execute(select(1))

    async with admin_db.session() as session:
        count = await session.execute(
            select(func.count())
            .select_from(ExecutionRecord)
            .where(ExecutionRecord.id.like(f"{_PREFIX}%"))
        )
        assert count.scalar_one() == 3
