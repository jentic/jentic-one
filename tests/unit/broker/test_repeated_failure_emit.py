"""The sync/streaming broker path emits ``execution.repeated_failure``.

The async-worker path is covered in ``tests/unit/shared/test_repeated_failure.py``;
this suite closes the gap the plan (step 4.3) called for — asserting the event
fires through the broker service's ``run_execution`` (BrokerError path) and
``persist_streaming_execution`` (streaming path) on a FAILED outcome.

These run against a real in-memory SQLite admin DB (no DB mocking —
``tests/arch/test_no_db_mocking.py``) so they exercise the real ``_persist`` →
``_emit_execution_lifecycle`` ordering: the failing ``ExecutionRecord`` must be
flushed *before* the count query runs, or the threshold would be off by one.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy import Table
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.repos.event_repo import EventRepository
from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.exceptions import BrokerError
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution.service import (
    default_broker,
    persist_streaming_execution,
    run_execution,
)
from jentic_one.shared.config import SecurityConfig
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.models.events import EventSeverity, EventType

_ACTOR = "agt_broker"
_TOOLKIT = "tk_broker00000000000000000"
_OPERATION = "doThing"
_TRACE = "b" * 32


def _create_admin_tables(sync_conn: Connection) -> None:
    """Create the two admin tables this suite needs on SQLite.

    The ORM models carry Postgres-only ``server_default``s the SQLite dialect
    can't render; the tests supply explicit ids, so we drop the defaults for the
    DDL and restore them (leaving the shared model definitions untouched).
    """
    tables = (cast(Table, Event.__table__), cast(Table, ExecutionRecord.__table__))
    for table in tables:
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


@contextlib.asynccontextmanager
async def _rollback_on_error(session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """Mirror the router's ``ctx.admin_db.transaction()`` rollback semantics.

    The real sync router wraps ``run_execution`` in a transaction that rolls
    back on any exception (``shared/db/session.py``). We replicate just that
    behaviour here so the test proves the manual ``session.commit()`` inside
    ``run_execution`` persists the FAILED record + events *before* this outer
    block tries to roll them away on the re-raised ``BrokerError``.
    """
    try:
        yield session
    except BaseException:
        await session.rollback()
        raise
    else:
        await session.commit()


def _ctx_req() -> ExecuteRequestContext:
    return ExecuteRequestContext(
        upstream_url="https://api.example.com/v1/test",
        method="GET",
        trace_id=_TRACE,
        toolkit_id=_TOOLKIT,
        operation_id=_OPERATION,
        api_vendor="example",
        api_name="api",
        api_version="1.0.0",
        prefer=None,
        pinned_revisions=None,
    )


class _FailRunner(UpstreamRunner):
    async def run(self, request: RunnerRequest) -> RunnerResult:
        raise BrokerError(detail="upstream timeout")


async def _seed_prior_failures(session: AsyncSession, count: int) -> None:
    """Seed prior FAILED records for the same key, just below the threshold."""
    now = datetime.now(UTC)
    for i in range(count):
        session.add(
            ExecutionRecord(
                id=f"exec_prior_{i}",
                toolkit_id=_TOOLKIT,
                trace_id=_TRACE,
                started_at=now,
                status=ExecutionStatus.FAILED,
                operation_id=_OPERATION,
                actor_id=_ACTOR,
                actor_type="agent",
            )
        )
    await session.flush()


async def _repeated_events(session: AsyncSession) -> list[Event]:
    return await EventRepository.list_all(
        session, event_type=[EventType.EXECUTION_REPEATED_FAILURE]
    )


async def test_run_execution_emits_repeated_failure_on_broker_error(session: AsyncSession) -> None:
    """The sync path: a BrokerError persists the failing record, then crosses the threshold."""
    config = SecurityConfig(execution_repeated_failure_threshold=5)
    # Seed threshold-1 failures so the record persisted by this failing run is
    # the Nth — proving _persist flushes before the repeated-failure count runs.
    await _seed_prior_failures(session, 4)

    with pytest.raises(BrokerError):
        async with _rollback_on_error(session):
            await run_execution(
                _ctx_req(),
                body=None,
                headers=None,
                session=session,
                broker=default_broker(_FailRunner()),
                actor_id=_ACTOR,
                actor_type="agent",
                security_config=config,
            )

    events = await _repeated_events(session)
    assert len(events) == 1
    assert events[0].severity == EventSeverity.ERROR.value
    assert events[0].data["actor_id"] == _ACTOR
    assert events[0].data["toolkit_id"] == _TOOLKIT
    assert events[0].data["operation_id"] == _OPERATION


async def test_run_execution_no_repeated_failure_without_security_config(
    session: AsyncSession,
) -> None:
    """Without ``security_config`` (async-worker path) the sync path stays silent."""
    await _seed_prior_failures(session, 4)

    with pytest.raises(BrokerError):
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            session=session,
            broker=default_broker(_FailRunner()),
            actor_id=_ACTOR,
            actor_type="agent",
        )

    assert await _repeated_events(session) == []


async def test_persist_streaming_execution_emits_repeated_failure(session: AsyncSession) -> None:
    """The streaming path: a FAILED outcome records the Nth failure and emits once."""
    config = SecurityConfig(execution_repeated_failure_threshold=5)
    await _seed_prior_failures(session, 4)

    await persist_streaming_execution(
        session,
        execution_id="exec_stream_fail",
        started_at=datetime.now(UTC),
        status=ExecutionStatus.FAILED,
        http_status=502,
        duration_ms=10,
        error="Upstream returned 502",
        ctx_req=_ctx_req(),
        actor_id=_ACTOR,
        actor_type="agent",
        security_config=config,
    )

    events = await _repeated_events(session)
    assert len(events) == 1
    assert events[0].severity == EventSeverity.ERROR.value


async def test_streaming_completed_does_not_emit_repeated_failure(session: AsyncSession) -> None:
    """A COMPLETED streaming outcome never triggers repeated-failure detection."""
    config = SecurityConfig(execution_repeated_failure_threshold=5)
    await _seed_prior_failures(session, 10)

    await persist_streaming_execution(
        session,
        execution_id="exec_stream_ok",
        started_at=datetime.now(UTC),
        status=ExecutionStatus.COMPLETED,
        http_status=200,
        duration_ms=10,
        error=None,
        ctx_req=_ctx_req(),
        actor_id=_ACTOR,
        actor_type="agent",
        security_config=config,
    )

    assert await _repeated_events(session) == []
