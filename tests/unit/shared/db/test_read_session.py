"""``DatabaseSession.read_session`` on a real file-backed SQLite database (#1823).

A read session runs its statements without ``BEGIN``/``ROLLBACK``. On SQLite the
pooled connections are already in driver autocommit, so the read session must
leave the isolation level alone: resetting it on checkin would re-enable
pysqlite's implicit ``BEGIN`` and break the ``BEGIN IMMEDIATE`` write path for
every later user of that connection.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from sqlalchemy import text
from tests.harness.db_round_trips import trace_round_trips

from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db.session import DatabaseSession


@pytest.fixture()
async def file_db(tmp_path: Path) -> AsyncGenerator[DatabaseSession, None]:
    # Blocks run one after another, so each reuses the single pooled connection.
    db = DatabaseSession(DatabaseConfig(backend="sqlite", path=str(tmp_path / "read.db")))
    await db.connect()
    async with db.transaction() as sess:
        await sess.execute(text("CREATE TABLE t (x INTEGER)"))
        await sess.execute(text("INSERT INTO t VALUES (1), (2)"))
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_read_session_runs_select_without_begin_or_rollback(
    file_db: DatabaseSession,
) -> None:
    with trace_round_trips(file_db) as trace:
        async with file_db.read_session() as sess:
            rows = (await sess.execute(text("SELECT x FROM t ORDER BY x"))).scalars().all()

    assert rows == [1, 2]
    assert len(trace.statements) == 1, trace.statements
    trace.assert_no_begin_or_rollback()


@pytest.mark.asyncio
async def test_read_session_keeps_pooled_connection_in_driver_autocommit(
    file_db: DatabaseSession,
) -> None:
    """A later write on the same connection still opens with BEGIN IMMEDIATE."""
    async with file_db.read_session() as sess:
        await sess.execute(text("SELECT 1"))
        read_driver = (await (await sess.connection()).get_raw_connection()).driver_connection

    async with file_db.session() as sess:
        conn = await sess.connection()
        driver = (await conn.get_raw_connection()).driver_connection
        assert driver is read_driver
        assert driver is not None
        assert driver.isolation_level is None

    with trace_round_trips(file_db) as trace:
        async with file_db.transaction() as sess:
            await sess.execute(text("INSERT INTO t VALUES (3)"))

    assert trace.statements[0] == "BEGIN IMMEDIATE", trace.statements
