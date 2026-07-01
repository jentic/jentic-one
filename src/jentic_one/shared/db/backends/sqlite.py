"""SQLite database backend (aiosqlite).

SQLite is an embedded production target. Foreign-key enforcement is enabled
per-connection via a PRAGMA.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import URL
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import ConnectionPoolEntry

from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db.backends.base import DatabaseBackend


class SqliteBackend(DatabaseBackend):
    """Backend for SQLite using the aiosqlite driver."""

    @property
    def dialect_name(self) -> str:
        return "sqlite"

    def make_url(self, config: DatabaseConfig) -> URL:
        # config.path is validated to be non-None for the sqlite backend.
        database = config.path or ":memory:"
        return URL.create(drivername="sqlite+aiosqlite", database=database)

    def engine_kwargs(self, config: DatabaseConfig) -> dict[str, Any]:
        return {
            "connect_args": {"check_same_thread": False},
        }


def configure_sqlite_pragmas(
    engine: AsyncEngine,
    *,
    journal_mode: str = "WAL",
    busy_timeout_ms: int = 5000,
) -> None:
    """Register a hook setting per-connection SQLite PRAGMAs.

    Runs, in order, on every new connection:

    - ``PRAGMA foreign_keys=ON`` — enforce referential integrity (SQLite leaves
      this off by default).
    - ``PRAGMA journal_mode=<journal_mode>`` — ``WAL`` lets a writer and readers
      proceed concurrently. This is persistent per database file (a no-op after
      the first set) and a no-op for ``:memory:`` databases.
    - ``PRAGMA busy_timeout=<busy_timeout_ms>`` — when a write meets a held lock,
      wait up to this many milliseconds for it to clear instead of failing
      immediately with ``database is locked``. This is per-connection, so it is
      issued on every connect.
    """

    sync_engine = engine.sync_engine

    @event.listens_for(sync_engine, "connect")
    def _set_pragma(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA journal_mode={journal_mode}")
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        cursor.close()


def enable_sqlite_manual_begin(engine: AsyncEngine) -> None:
    """Hand transaction control to the application so writes can BEGIN IMMEDIATE.

    Setting ``isolation_level = None`` on each raw DBAPI connection disables
    pysqlite's legacy implicit ``BEGIN``. This leaves reads in autocommit — a
    plain ``SELECT`` via a read-only session takes only a shared read lock, so
    WAL's reader/writer concurrency is preserved — and lets the *write* path
    issue an explicit ``BEGIN IMMEDIATE`` (see ``DatabaseSession.transaction``).

    Why the write path needs ``BEGIN IMMEDIATE``: under a *deferred* begin a
    write transaction that reads first (e.g. ``UPDATE ... WHERE id = (SELECT
    ...)``) holds a read snapshot and then tries to *upgrade* to a writer. If
    another connection committed a write in the meantime, SQLite raises
    ``SQLITE_BUSY_SNAPSHOT`` ("database is locked") *immediately* — ``PRAGMA
    busy_timeout`` does not apply to snapshot-upgrade conflicts, only to
    acquiring an initial lock. ``BEGIN IMMEDIATE`` takes the write lock up
    front, so there is no read→write upgrade to fail on and ``busy_timeout``
    governs the wait.

    Crucially this is applied to writes only, not reads: a global
    ``BEGIN IMMEDIATE`` (via a ``begin`` event) would fire on every session's
    autobegin — including read-only ones — making every read take a write lock
    and defeating WAL.
    """

    sync_engine = engine.sync_engine

    @event.listens_for(sync_engine, "connect")
    def _disable_pysqlite_implicit_begin(
        dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry
    ) -> None:
        dbapi_connection.isolation_level = None


def enable_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    """Backwards-compatible alias that configures pragmas with defaults."""
    configure_sqlite_pragmas(engine)
