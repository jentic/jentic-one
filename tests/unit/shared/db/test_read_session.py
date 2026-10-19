"""``DatabaseSession.read_session`` on a real file-backed SQLite database (#1823).

A read session runs its statements without ``BEGIN``/``ROLLBACK`` and refuses
writes, since in autocommit a write would be durable without any commit. On
SQLite the pooled connections are already in driver autocommit, so the read
session must leave the isolation level alone: resetting it on checkin would
re-enable pysqlite's implicit ``BEGIN`` for every later user of that connection.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Integer, column, delete, insert, select, table, text, update
from sqlalchemy.exc import InvalidRequestError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from tests.harness.db_round_trips import trace_round_trips

from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db.session import DatabaseSession


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "t"

    x: Mapped[int] = mapped_column(Integer, primary_key=True)


@pytest.fixture()
async def file_db(tmp_path: Path) -> AsyncGenerator[DatabaseSession, None]:
    # Blocks run one after another, so each reuses the single pooled connection.
    db = DatabaseSession(DatabaseConfig(backend="sqlite", path=str(tmp_path / "read.db")))
    await db.connect()
    async with db.transaction() as sess:
        await sess.execute(text("CREATE TABLE t (x INTEGER PRIMARY KEY)"))
        await sess.execute(text("INSERT INTO t VALUES (1), (2)"))
    yield db
    await db.close()


async def _rows(db: DatabaseSession) -> list[int]:
    async with db.session() as sess:
        return list((await sess.execute(select(_Row.x).order_by(_Row.x))).scalars())


@pytest.mark.asyncio
async def test_read_session_runs_select_without_begin_or_rollback(
    file_db: DatabaseSession,
) -> None:
    """Characterises the SQLite path: one statement, no transaction control.

    SQLite connections are already in driver autocommit, so ``session()`` passes
    this too; the Postgres integration tests are the ones that discriminate.
    """
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
    """The read session leaves SQLite's pooled connection in driver autocommit.

    Applying ``AUTOCOMMIT`` on SQLite would reset the connection on checkin to
    pysqlite's legacy mode (``isolation_level == ""``), whose implicit
    ``BEGIN`` opens a deferred transaction before the next DML outside
    ``transaction()``. Both assertions on the reused driver connection fail in
    that case.
    """
    async with file_db.read_session() as sess:
        await sess.execute(text("SELECT 1"))
        read_driver = (await (await sess.connection()).get_raw_connection()).driver_connection

    async with file_db.session() as sess:
        conn = await sess.connection()
        driver = (await conn.get_raw_connection()).driver_connection
        assert driver is read_driver
        assert driver is not None
        assert driver.isolation_level is None
        # DML outside transaction(): no implicit BEGIN wraps it.
        await conn.exec_driver_sql("INSERT INTO t VALUES (3)")
        assert driver.in_transaction is False

    # Sanity check: the write path still opens with its explicit BEGIN IMMEDIATE.
    with trace_round_trips(file_db) as trace:
        async with file_db.transaction() as sess:
            await sess.execute(text("INSERT INTO t VALUES (4)"))
    assert trace.statements[0] == "BEGIN IMMEDIATE", trace.statements


async def _orm_add_then_query(sess: AsyncSession) -> None:
    sess.add(_Row(x=10))
    await sess.execute(select(_Row))  # autoflush


async def _orm_add_then_flush(sess: AsyncSession) -> None:
    sess.add(_Row(x=10))
    await sess.flush()


async def _orm_dirty_then_query(sess: AsyncSession) -> None:
    row = (await sess.execute(select(_Row).where(_Row.x == 1))).scalar_one()
    row.x = 10
    await sess.execute(select(_Row))  # autoflush


async def _orm_delete_then_flush(sess: AsyncSession) -> None:
    row = (await sess.execute(select(_Row).where(_Row.x == 1))).scalar_one()
    await sess.delete(row)
    await sess.flush()


def _execute(statement: Any) -> Callable[[AsyncSession], Any]:
    async def _run(sess: AsyncSession) -> None:
        await sess.execute(statement)

    return _run


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "write",
    [
        _orm_add_then_query,
        _orm_add_then_flush,
        _orm_dirty_then_query,
        _orm_delete_then_flush,
        _execute(insert(_Row).values(x=10)),
        _execute(update(_Row).values(x=10).where(_Row.x == 1)),
        _execute(delete(_Row)),
        _execute(insert(table("t", column("x"))).values(x=10)),
        _execute(text("INSERT INTO t VALUES (10)")),
        _execute(text("  update t SET x = 10 WHERE x = 1")),
        _execute(text("DELETE FROM t")),
        _execute(text("/* c */ DELETE FROM t")),
        _execute(text("-- c\nINSERT INTO t VALUES (10)")),
        _execute(text("/* a\nb */ -- c\n  update t SET x = 10 WHERE x = 1")),
        _execute(select(insert(_Row).values(x=10).returning(_Row.x).cte("ins").c.x)),
        _execute(select(_Row.x).add_cte(delete(_Row).returning(_Row.x).cte("d"))),
    ],
    ids=[
        "orm-add-autoflush",
        "orm-add-flush",
        "orm-dirty-autoflush",
        "orm-delete-flush",
        "orm-insert",
        "orm-update",
        "orm-delete",
        "core-insert",
        "text-insert",
        "text-update",
        "text-delete",
        "text-comment-delete",
        "text-comment-insert",
        "text-comments-update",
        "core-cte-insert",
        "core-add-cte-delete",
    ],
)
async def test_read_session_refuses_writes(
    file_db: DatabaseSession, write: Callable[[AsyncSession], Any]
) -> None:
    """A write through ``read_session()`` raises and leaves no row changed."""
    with pytest.raises(InvalidRequestError, match="read_session"):
        async with file_db.read_session() as sess:
            await write(sess)

    assert await _rows(file_db) == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "read",
    [
        select(_Row.x).order_by(_Row.x).with_for_update(),
        select(select(_Row.x).cte("c").c.x).order_by("x"),
        text("WITH c AS (SELECT x FROM t) SELECT x FROM c ORDER BY x"),
        text("SELECT x FROM t WHERE 'delete' != 'update' ORDER BY x"),
        text("\nSELECT x FROM t ORDER BY x"),
        text("/* DELETE */ -- UPDATE\nSELECT x FROM t ORDER BY x"),
    ],
    ids=[
        "orm-for-update",
        "core-cte-select",
        "text-cte-select",
        "text-quoted-keywords",
        "text-leading-newline",
        "text-keywords-in-comments",
    ],
)
async def test_read_session_allows_reads_that_mention_write_keywords(
    file_db: DatabaseSession, read: Any
) -> None:
    """The guard keys on the statement verb, not on keywords inside a read.

    SQLite drops ``FOR UPDATE`` when compiling ``with_for_update()``, so that
    case pins that the guard does not refuse a locking ``select``.
    """
    async with file_db.read_session() as sess:
        rows = (await sess.execute(read)).scalars()
        assert list(rows) == [1, 2]


@pytest.mark.asyncio
async def test_read_session_passes_textual_for_update_to_the_database(
    file_db: DatabaseSession,
) -> None:
    """Textual ``SELECT … FOR UPDATE`` gets past the guard.

    SQLite has no ``FOR UPDATE``, so the statement fails in the database with
    ``OperationalError``. A guard refusal would raise ``InvalidRequestError``.
    """
    async with file_db.read_session() as sess:
        with pytest.raises(OperationalError):
            await sess.execute(text("SELECT x FROM t ORDER BY x FOR UPDATE"))


@pytest.mark.asyncio
async def test_read_only_guard_is_scoped_to_the_read_session(file_db: DatabaseSession) -> None:
    """Sessions opened after a read session can still write."""
    async with file_db.read_session() as sess:
        await sess.execute(select(_Row))

    async with file_db.transaction() as sess:
        sess.add(_Row(x=3))
        await sess.execute(update(_Row).values(x=4).where(_Row.x == 3))

    assert await _rows(file_db) == [1, 2, 4]


class _MappedError(Exception):
    pass


class _MappingDatabaseSession(DatabaseSession):
    """Maps body errors in ``session()``, as connection-loss handling does."""

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        async with super().session() as sess:
            try:
                yield sess
            except LookupError as exc:
                raise _MappedError from exc


@pytest.mark.asyncio
async def test_read_session_inherits_session_error_mapping(tmp_path: Path) -> None:
    """``read_session()`` runs its body inside ``session()``, so it maps errors the same way."""
    db = _MappingDatabaseSession(DatabaseConfig(backend="sqlite", path=str(tmp_path / "map.db")))
    await db.connect()
    try:
        with pytest.raises(_MappedError):
            async with db.read_session() as sess:
                await sess.execute(text("SELECT 1"))
                raise LookupError("lost")
    finally:
        await db.close()
