"""Shared database utility functions."""

from datetime import UTC, datetime

from sqlalchemy import func, select, util
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Insert


def utcnow() -> datetime:
    """UTC-aware now() for app-side timestamp defaults (microsecond resolution)."""
    return datetime.now(UTC)


async def db_now(session: AsyncSession) -> datetime:
    """The database's current time, UTC-aware.

    For deadlines that several processes compare (an approval's expiry, say):
    reading the clock from the one database keeps a broker, an admin app and
    a worker on different hosts from disagreeing through clock skew.
    PostgreSQL answers the transaction's start time; SQLite answers
    ``CURRENT_TIMESTAMP`` (UTC, whole seconds), which arrives naive.
    """
    value = (await session.execute(select(func.now()))).scalar_one()
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def execute_core_insert(session: AsyncSession, stmt: Insert) -> None:
    """Run a Core ``INSERT`` with the transaction semantics of an ORM flush.

    For append-only rows on hot paths: a Core statement skips the unit of work
    (identity map, attribute instrumentation, flush planning) that
    ``session.add`` + ``session.flush`` pays for a row nobody reads back.

    A bare ``session.execute`` does not match a flush on failure, though. A
    failed flush rolls back its enclosing scope and parks the session until
    ``rollback()``. On PostgreSQL that scope is the savepoint inside
    ``begin_nested()``, or else the whole transaction, after which every
    further statement and the ``commit()`` raise ``PendingRollbackError``. A
    failed bare execute leaves the session active instead: inside a savepoint
    the ``RELEASE`` hits an aborted transaction; outside one, a caller that
    swallows the error (a best-effort emit) gets a ``COMMIT`` that PostgreSQL
    silently turns into a rollback, and SQLite commits the rest of the
    transaction. So the statement runs in the same subtransaction scope
    ``Session.flush`` opens, and that scope rolls back with the flush's
    exception capture when the statement fails.

    Pending ORM state is flushed first, so rows the caller added earlier in the
    transaction still land before this one. The flush is a no-op, and fires no
    flush events, on a clean session.
    """
    await session.run_sync(_execute_in_flush_scope, stmt)


def _execute_in_flush_scope(sync_session: Session, stmt: Insert) -> None:
    sync_session.flush()
    # ``_autobegin_t()._begin()`` and ``rollback(_capture_exception=True)`` are
    # the calls ``Session._flush`` makes. They are private SQLAlchemy API, pinned
    # by tests/integration/shared/test_core_insert_hot_path.py.
    transaction = sync_session._autobegin_t()._begin()
    try:
        sync_session.execute(stmt)
        transaction.commit()
    except BaseException:
        with util.safe_reraise():
            transaction.rollback(_capture_exception=True)
