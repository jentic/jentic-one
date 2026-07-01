"""Concurrency tests for the SQLite write path (BEGIN IMMEDIATE).

These use a real file-backed SQLite ``DatabaseSession`` (WAL is a no-op for
``:memory:``) to prove the two properties the fix must uphold:

- Writes go through ``BEGIN IMMEDIATE`` so concurrent writers *serialize* on the
  write lock instead of failing with "database is locked" (the read->write
  upgrade / ``SQLITE_BUSY_SNAPSHOT`` class of error).
- Reads via :meth:`DatabaseSession.session` stay in autocommit and do NOT take a
  write lock, so a held-open read session never blocks a writer — WAL's
  reader/writer concurrency is preserved. This is the regression guard against a
  blanket ``BEGIN IMMEDIATE`` that fires on every autobegin, including reads.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text

from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db.session import DatabaseSession


@pytest.fixture()
async def file_db(tmp_path: Path) -> DatabaseSession:
    db = DatabaseSession(DatabaseConfig(backend="sqlite", path=str(tmp_path / "concurrency.db")))
    await db.connect()
    async with db.transaction() as sess:
        await sess.execute(text("CREATE TABLE t (x INTEGER)"))
    return db


@pytest.mark.asyncio
async def test_write_transaction_emits_begin_immediate(file_db: DatabaseSession) -> None:
    """The write path opens the transaction with BEGIN IMMEDIATE, not deferred."""
    statements: list[str] = []

    from sqlalchemy import event

    sync_engine = file_db.engine.sync_engine

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _capture(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    async with file_db.transaction() as sess:
        await sess.execute(text("INSERT INTO t VALUES (1)"))

    assert any("BEGIN IMMEDIATE" in s for s in statements), statements


@pytest.mark.asyncio
async def test_read_session_does_not_block_writer(file_db: DatabaseSession) -> None:
    """A held-open read session must NOT hold a write lock (WAL concurrency)."""
    read_open = asyncio.Event()
    release = asyncio.Event()

    async def hold_read() -> None:
        async with file_db.session() as sess:
            await sess.execute(text("SELECT count(*) FROM t"))
            read_open.set()
            await release.wait()

    async def do_write() -> str:
        await read_open.wait()
        async with file_db.transaction() as sess:
            await sess.execute(text("INSERT INTO t VALUES (2)"))
        return "written"

    reader = asyncio.create_task(hold_read())
    try:
        # If reads took a write lock, this write would block until release and
        # time out. It must complete promptly instead.
        result = await asyncio.wait_for(do_write(), timeout=5.0)
        assert result == "written"
    finally:
        release.set()
        await reader


@pytest.mark.asyncio
async def test_concurrent_writers_serialize_without_lock_error(
    file_db: DatabaseSession,
) -> None:
    """Many concurrent read-then-write transactions all succeed (no lock error).

    Each transaction does a SELECT then an INSERT — the read->write upgrade shape
    that raises SQLITE_BUSY_SNAPSHOT under a deferred BEGIN. With BEGIN IMMEDIATE
    they serialize on the write lock and all commit.
    """

    async def read_then_write(n: int) -> int:
        async with file_db.transaction() as sess:
            await sess.execute(text("SELECT count(*) FROM t"))
            await sess.execute(text("INSERT INTO t VALUES (:x)"), {"x": n})
        return n

    results = await asyncio.gather(*(read_then_write(i) for i in range(10)))

    assert sorted(results) == list(range(10))
    async with file_db.session() as sess:
        count = (await sess.execute(text("SELECT count(*) FROM t"))).scalar_one()
    assert count == 10
