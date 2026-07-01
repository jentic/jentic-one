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
from sqlalchemy.pool import ConnectionPoolEntry, StaticPool

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
        # ``:memory:`` must share a single connection: a second connection would
        # open a *separate, empty* database. StaticPool keeps exactly one.
        if (config.path or ":memory:") == ":memory:":
            return {
                "poolclass": StaticPool,
                "connect_args": {"check_same_thread": False},
            }
        # A file DB keeps the default async pool so *nested* sessions (a session
        # opened while another is still held on the same DB — a real pattern in
        # the service layer, e.g. audit-within-a-request) don't self-deadlock on
        # a single connection. Concurrent writers are instead made lock-safe with
        # ``BEGIN IMMEDIATE`` + ``busy_timeout`` (see configure_sqlite_*): a
        # second writer *waits* for the lock instead of failing instantly with
        # ``database is locked``, and the run_in_transaction retry covers the
        # rare wait that still exceeds ``busy_timeout``.
        return {
            "connect_args": {"check_same_thread": False},
        }


def configure_sqlite_deferred_control(engine: AsyncEngine) -> None:
    """Hand transaction control to us so writes can take the lock up front.

    aiosqlite (like pysqlite) opens transactions lazily in ``DEFERRED`` mode: the
    first statement decides the lock. A *write* transaction that reads before it
    writes — as the token, registration, and refresh paths all do (``SELECT``
    then ``UPDATE``/``INSERT``) — would take a *read* lock first, then try to
    upgrade to a *write* lock. Under WAL that upgrade cannot wait (waiting would
    deadlock two readers each wanting to write), so SQLite returns ``SQLITE_BUSY``
    immediately, ignoring ``busy_timeout`` → ``database is locked``.

    Setting ``isolation_level = None`` disables the driver's implicit ``BEGIN``,
    so no transaction is opened until we say so. The *write* path
    (:meth:`DatabaseSession.transaction`) then emits ``BEGIN IMMEDIATE`` to grab
    the write lock up front (a bounded wait governed by ``busy_timeout``), while
    read-only :meth:`DatabaseSession.session` blocks stay lock-free and never
    contend — which also means a read session can safely nest a write
    transaction on the same file without self-deadlocking.
    """

    sync_engine = engine.sync_engine

    @event.listens_for(sync_engine, "connect")
    def _disable_implicit_begin(
        dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry
    ) -> None:
        # Hand transaction control to us; aiosqlite mirrors the pysqlite API.
        dbapi_connection.isolation_level = None


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


def enable_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    """Backwards-compatible alias that configures pragmas with defaults."""
    configure_sqlite_pragmas(engine)
