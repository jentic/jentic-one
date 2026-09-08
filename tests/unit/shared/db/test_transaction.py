"""Unit tests for DatabaseSession.run_in_transaction body-level retry.

These use a real in-memory SQLite ``DatabaseSession`` (no DB mocking, per
project rules) and induce transient failures by raising from the callable
passed to ``run_in_transaction`` — exactly how a transient ``OperationalError``
would surface from inside the transaction body in production.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, MissingGreenlet, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db.errors import (
    DatabaseConsistencyError,
    DatabaseIntegrityError,
    DatabaseUnavailableError,
)
from jentic_one.shared.db.session import DatabaseSession


@pytest.fixture()
async def sqlite_session() -> DatabaseSession:
    db = DatabaseSession(DatabaseConfig(backend="sqlite", path=":memory:"))
    await db.connect()
    return db


def _operational_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("database is locked"))


@pytest.mark.asyncio
async def test_run_in_transaction_retries_then_succeeds(sqlite_session: DatabaseSession) -> None:
    """A transient OperationalError on the first attempt is retried and succeeds."""
    attempts = 0

    async def fn(session: AsyncSession) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _operational_error()
        await session.execute(text("SELECT 1"))
        return "ok"

    result = await sqlite_session.run_in_transaction(fn, backoff_s=0)

    assert result == "ok"
    assert attempts == 2


@pytest.mark.asyncio
async def test_run_in_transaction_exhausts_retries(sqlite_session: DatabaseSession) -> None:
    """A callable that always raises a transient error exhausts retries -> 503-mapped error."""
    attempts = 0

    async def fn(_session: AsyncSession) -> None:
        nonlocal attempts
        attempts += 1
        raise _operational_error()

    with pytest.raises(DatabaseUnavailableError):
        await sqlite_session.run_in_transaction(fn, retries=2, backoff_s=0)

    # initial attempt + 2 retries
    assert attempts == 3


@pytest.mark.asyncio
async def test_run_in_transaction_does_not_retry_integrity_error(
    sqlite_session: DatabaseSession,
) -> None:
    """IntegrityError is not retried and surfaces as DatabaseIntegrityError."""
    attempts = 0

    async def fn(_session: AsyncSession) -> None:
        nonlocal attempts
        attempts += 1
        raise IntegrityError("INSERT", {}, Exception("unique constraint"))

    with pytest.raises(DatabaseIntegrityError):
        await sqlite_session.run_in_transaction(fn, backoff_s=0)

    assert attempts == 1


@pytest.mark.asyncio
async def test_transaction_maps_missing_greenlet_to_consistency_error(
    sqlite_session: DatabaseSession,
) -> None:
    """MissingGreenlet (an InvalidRequestError) surfaces as DatabaseConsistencyError.

    Regression guard for #642: an accidental async lazy load inside a
    transaction must map to a known domain error rather than escaping raw.
    """
    with pytest.raises(DatabaseConsistencyError):
        async with sqlite_session.transaction() as session:
            await session.execute(text("SELECT 1"))
            raise MissingGreenlet("greenlet_spawn has not been called")
