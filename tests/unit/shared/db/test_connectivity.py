"""Unit tests for classifying database connection-loss errors (#1082)."""

from __future__ import annotations

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from jentic_one.shared.db.connectivity import (
    connection_loss_cause,
    describe_connection_loss,
    is_connection_loss,
)
from jentic_one.shared.db.errors import DatabaseUnavailableError


class _AdapterError(Exception):
    """Stand-in for SQLAlchemy's asyncpg adapter error (``raise ... from asyncpg_exc``)."""


def _wrapped(driver_exc: BaseException, *, invalidated: bool = False) -> DBAPIError:
    """Build the SQLAlchemy shape seen in #1082: DBAPIError -> adapter error -> asyncpg."""
    adapter = _AdapterError(f"{type(driver_exc)}: {driver_exc}")
    adapter.__cause__ = driver_exc
    err = DBAPIError(
        "UPDATE jobs SET status=$1::VARCHAR",
        ("running",),
        adapter,
        connection_invalidated=invalidated,
    )
    err.__cause__ = adapter
    return err


def _with_context(exc: BaseException, context: BaseException) -> BaseException:
    exc.__context__ = context
    return exc


@pytest.mark.parametrize(
    "exc",
    [
        _wrapped(asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed")),
        _wrapped(_AdapterError("closed"), invalidated=True),
        _wrapped(asyncpg.exceptions.AdminShutdownError("terminating connection")),
        _wrapped(asyncpg.exceptions.CannotConnectNowError("the database system is starting up")),
        asyncpg.InterfaceError("cannot perform operation: connection is closed"),
        ConnectionResetError(54, "Connection reset by peer"),
        ConnectionRefusedError(61, "Connect call failed"),
        OSError("Multiple exceptions: [Errno 61] ..., [Errno 61] ..."),
        TimeoutError(),
        OperationalError("SELECT 1", {}, Exception("server closed the connection")),
        DatabaseUnavailableError("database is locked"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_is_connection_loss_matches_connection_errors(exc: BaseException) -> None:
    assert is_connection_loss(exc)


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("bad input"),
        RuntimeError("bug"),
        KeyError("job"),
        FileNotFoundError(2, "No such file"),
        PermissionError(13, "denied"),
        # asyncpg InterfaceError subclasses and other InterfaceErrors are bugs.
        asyncpg.exceptions.DataError("invalid input for query argument $1"),
        asyncpg.InterfaceError("cannot perform operation: another operation is in progress"),
        # A live connection rejecting a statement is not a connection loss.
        _wrapped(asyncpg.exceptions.UndefinedTableError('relation "jobs" does not exist')),
        IntegrityError("INSERT", {}, Exception("duplicate key")),
        # An unrelated error raised *while handling* a disconnect is still a bug.
        _with_context(RuntimeError("bug in handler"), ConnectionResetError()),
    ],
    ids=lambda e: f"{type(e).__name__}",
)
def test_is_connection_loss_ignores_unrelated_errors(exc: BaseException) -> None:
    assert not is_connection_loss(exc)


def test_connection_loss_cause_prefers_driver_error_and_describe_omits_sql() -> None:
    """The innermost driver error is reported, without SQL text or parameters."""
    driver = asyncpg.exceptions.ConnectionDoesNotExistError(
        "connection was closed in the middle of operation"
    )
    err = _wrapped(driver)
    cause = connection_loss_cause(err)
    assert cause is driver
    assert describe_connection_loss(cause) == "connection was closed in the middle of operation"

    invalidated = _wrapped(_AdapterError("adapter says closed"), invalidated=True)
    reason = describe_connection_loss(connection_loss_cause(invalidated))  # type: ignore[arg-type]
    assert "UPDATE jobs" not in reason
    assert "running" not in reason
