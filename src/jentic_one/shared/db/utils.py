"""Shared database utility functions."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Session


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


async def insert_append_only_row(
    session: AsyncSession, model: type[DeclarativeBase], values: dict[str, Any]
) -> None:
    """Insert one row of ``model`` without registering an ORM instance.

    For append-only rows on hot paths that nobody reads back through the
    session: no identity map, no attribute instrumentation, no flush planning,
    no flush events. Column ``default=``s still apply to keys absent from
    ``values``. Keys whose value is ``None`` are left out of the INSERT too,
    so a column with a default gets that default rather than NULL; to store
    an explicit NULL in such a column, use the ORM path instead.

    Failure semantics match a flush: the insert runs in the same subtransaction
    scope ``Session.flush`` opens, so a failure rolls back the enclosing
    savepoint (or the whole transaction) and parks the session until
    ``rollback()``. A bare ``session.execute`` would leave the session active
    instead, and on PostgreSQL that turns a swallowed failure into a failed
    ``RELEASE SAVEPOINT`` or a ``COMMIT`` that silently rolls back.

    Pending ORM state is flushed first, so rows the caller added earlier still
    land before this one.
    """
    await session.run_sync(_flush_then_bulk_insert, model, values)


def _flush_then_bulk_insert(
    sync_session: Session, model: type[DeclarativeBase], values: dict[str, Any]
) -> None:
    sync_session.flush()
    sync_session.bulk_insert_mappings(model, [values])
