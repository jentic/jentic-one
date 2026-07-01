"""Tests for pluggable database backend selection and engine wiring."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db.backends import (
    PostgresBackend,
    SqliteBackend,
    configure_sqlite_pragmas,
    get_backend,
)
from jentic_one.shared.db.session import DatabaseSession


def test_get_backend_returns_postgres_by_default() -> None:
    config = DatabaseConfig(name="reg")
    backend = get_backend(config)
    assert isinstance(backend, PostgresBackend)
    assert backend.dialect_name == "postgres"


def test_get_backend_returns_sqlite() -> None:
    config = DatabaseConfig(backend="sqlite", path=":memory:")
    backend = get_backend(config)
    assert isinstance(backend, SqliteBackend)
    assert backend.dialect_name == "sqlite"


def test_postgres_config_requires_name() -> None:
    with pytest.raises(ValueError, match="requires a database 'name'"):
        DatabaseConfig(backend="postgres", name="")


def test_sqlite_config_requires_path() -> None:
    with pytest.raises(ValueError, match="requires a 'path'"):
        DatabaseConfig(backend="sqlite")


def test_postgres_make_url_and_engine_kwargs() -> None:
    config = DatabaseConfig(name="reg", schema_name="registry")
    backend = PostgresBackend()
    url = backend.make_url(config)
    assert url.drivername == "postgresql+asyncpg"
    assert url.database == "reg"
    kwargs = backend.engine_kwargs(config)
    assert kwargs["connect_args"]["server_settings"]["search_path"] == "registry,public"


def test_sqlite_make_url_uses_path() -> None:
    config = DatabaseConfig(backend="sqlite", path="/tmp/x.db")
    backend = SqliteBackend()
    url = backend.make_url(config)
    assert url.drivername == "sqlite+aiosqlite"
    assert url.database == "/tmp/x.db"


@pytest.mark.asyncio
async def test_sqlite_engine_round_trip() -> None:
    config = DatabaseConfig(backend="sqlite", path=":memory:")
    backend = SqliteBackend()
    engine = create_async_engine(backend.make_url(config), **backend.engine_kwargs(config))
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_connect_hook_sets_wal_and_busy_timeout(tmp_path: Path) -> None:
    """The connect hook applies journal_mode=WAL and the configured busy_timeout.

    Uses a file-backed DB (not ``:memory:``) because WAL is a no-op for in-memory
    databases.
    """
    db_file = tmp_path / "pragma_test.db"
    config = DatabaseConfig(backend="sqlite", path=str(db_file), busy_timeout_ms=7321)
    backend = SqliteBackend()
    engine = create_async_engine(backend.make_url(config), **backend.engine_kwargs(config))
    configure_sqlite_pragmas(
        engine,
        journal_mode=config.journal_mode,
        busy_timeout_ms=config.busy_timeout_ms,
    )
    try:
        async with engine.connect() as conn:
            journal_mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
            busy_timeout = (await conn.execute(text("PRAGMA busy_timeout"))).scalar_one()
            foreign_keys = (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()
        assert journal_mode == "wal"
        assert busy_timeout == 7321
        assert foreign_keys == 1
    finally:
        await engine.dispose()


def test_sqlite_engine_kwargs_file_uses_default_pool() -> None:
    """A file-backed SQLite engine keeps the default pool (no StaticPool).

    A single connection would self-deadlock the service layer's nested-session
    pattern (a session opened while another is held on the same DB, e.g.
    audit-within-a-request). Concurrent writers are instead made lock-safe with
    ``BEGIN IMMEDIATE`` + ``busy_timeout`` so they wait rather than failing
    instantly with ``database is locked``.
    """
    config = DatabaseConfig(backend="sqlite", path="/tmp/x.db")
    kwargs = SqliteBackend().engine_kwargs(config)
    assert "poolclass" not in kwargs


def test_sqlite_engine_kwargs_memory_uses_static_pool() -> None:
    """An in-memory SQLite engine must share one connection (StaticPool).

    A second connection to ``:memory:`` opens a separate, empty database, so the
    engine has to reuse a single connection.
    """
    from sqlalchemy.pool import StaticPool

    config = DatabaseConfig(backend="sqlite", path=":memory:")
    kwargs = SqliteBackend().engine_kwargs(config)
    assert kwargs["poolclass"] is StaticPool


@pytest.mark.asyncio
async def test_sqlite_write_transaction_takes_write_lock_up_front(tmp_path: Path) -> None:
    """A write ``transaction()`` that reads before it writes holds the write lock.

    The write path opens with ``BEGIN IMMEDIATE``, so a concurrent writer on a
    second connection must *wait* on ``busy_timeout`` (and fail busy when it
    expires) rather than the first transaction failing to upgrade a read lock —
    i.e. the transaction is genuinely a writer from its first statement.
    """
    import asyncio

    from sqlalchemy.exc import DatabaseError

    from jentic_one.shared.db.errors import DatabaseUnavailableError

    db = DatabaseSession(
        DatabaseConfig(backend="sqlite", path=str(tmp_path / "immediate.db"), busy_timeout_ms=200)
    )
    await db.connect()
    try:
        async with db.transaction() as sess:
            await sess.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)"))
            await sess.execute(text("INSERT INTO t (id, v) VALUES (1, 0)"))

        # Hold a write transaction open (read first, so under DEFERRED this would
        # only be a read lock — BEGIN IMMEDIATE makes it a write lock up front).
        holder_ready = asyncio.Event()
        release_holder = asyncio.Event()

        async def holder() -> None:
            async with db.transaction() as sess:
                await sess.execute(text("SELECT v FROM t WHERE id = 1"))
                holder_ready.set()
                await release_holder.wait()

        async def contender() -> None:
            await holder_ready.wait()
            async with db.transaction() as sess:
                await sess.execute(text("UPDATE t SET v = v + 1 WHERE id = 1"))

        holder_task = asyncio.create_task(holder())
        # The contender must fail busy because the holder owns the write lock.
        with pytest.raises((DatabaseUnavailableError, DatabaseError)):
            await asyncio.wait_for(contender(), timeout=5)
        release_holder.set()
        await holder_task
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sqlite_session_serializes_concurrent_read_then_write(tmp_path: Path) -> None:
    """Concurrent read-then-write transactions on one DatabaseSession don't lock.

    This is the ``jentic register`` shape: the token/mint path reads a row then
    writes it, while a background loop writes the same DB. Before the fix these
    raced to ``database is locked``; BEGIN IMMEDIATE + busy_timeout serialize
    them into ordered, lossless updates.
    """
    import asyncio

    db = DatabaseSession(DatabaseConfig(backend="sqlite", path=str(tmp_path / "reg.db")))
    await db.connect()
    try:
        async with db.transaction() as sess:
            await sess.execute(text("CREATE TABLE counter (id INTEGER PRIMARY KEY, n INTEGER)"))
            await sess.execute(text("INSERT INTO counter (id, n) VALUES (1, 0)"))

        async def bump() -> None:
            await db.run_in_transaction(_bump_once)

        async def _bump_once(sess: AsyncSession) -> None:
            row = (await sess.execute(text("SELECT n FROM counter WHERE id = 1"))).scalar_one()
            await sess.execute(text("UPDATE counter SET n = :n WHERE id = 1"), {"n": row + 1})

        await asyncio.gather(*(bump() for _ in range(10)))

        async with db.session() as sess:
            total = (await sess.execute(text("SELECT n FROM counter WHERE id = 1"))).scalar_one()
        assert total == 10
    finally:
        await db.close()
