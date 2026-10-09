"""Shared database utility functions."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


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
