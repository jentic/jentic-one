"""Unit tests for classifying database connection-loss errors (#1082)."""

from __future__ import annotations

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from jentic_one.shared.db.connectivity import (
    connection_loss_cause,
    describe_connection_loss,
    is_connection_loss,
    redact_sql,
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


def _wrapped_operational(driver_exc: BaseException) -> OperationalError:
    """An OperationalError (not invalidated) whose driver cause is ``driver_exc``."""
    adapter = _AdapterError(str(driver_exc))
    adapter.__cause__ = driver_exc
    err = OperationalError("SELECT 1", {}, adapter)
    err.__cause__ = adapter
    return err


def _unavailable_from(exc: BaseException) -> DatabaseUnavailableError:
    """Mirror ``DatabaseSession.transaction()``: wrap ``str(exc)`` and chain ``from exc``."""
    err = DatabaseUnavailableError(str(exc))
    err.__cause__ = exc
    return err


def _with_sqlstate(exc: _AdapterError, sqlstate: str) -> _AdapterError:
    exc.sqlstate = sqlstate  # type: ignore[attr-defined]
    return exc


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
        # An OperationalError counts only when SQLAlchemy invalidated the
        # connection, or a cause in the chain is a connection error.
        OperationalError(
            "SELECT 1", {}, Exception("server closed the connection"), connection_invalidated=True
        ),
        _wrapped_operational(asyncpg.exceptions.ConnectionDoesNotExistError("closed")),
        # The domain wrapper from DatabaseSession.transaction() is classified
        # by what it was raised from.
        _unavailable_from(_wrapped(_AdapterError("closed"), invalidated=True)),
        # A driver error that only carries a connection-class SQLSTATE (08xxx).
        _wrapped(_with_sqlstate(_AdapterError("connection failure"), "08006")),
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
        # OperationalError / DatabaseUnavailableError on a live connection:
        # SQLite schema, syntax and lock errors ...
        OperationalError("SELECT * FROM missing", {}, Exception("no such table: missing")),
        OperationalError("SELEC 1", {}, Exception('near "SELEC": syntax error')),
        OperationalError("UPDATE jobs", {}, Exception("database is locked")),
        _unavailable_from(
            OperationalError("SELECT * FROM jobs", {}, Exception("no such table: jobs"))
        ),
        DatabaseUnavailableError("no such table: jobs"),
        # ... and Postgres lock/statement timeouts, serialization failures and
        # deadlocks, whichever SQLAlchemy wrapper carries them.
        _wrapped(asyncpg.exceptions.LockNotAvailableError("could not obtain lock")),
        _wrapped(asyncpg.exceptions.QueryCanceledError("canceling statement due to timeout")),
        _wrapped(asyncpg.exceptions.SerializationError("could not serialize access")),
        _wrapped(asyncpg.exceptions.DeadlockDetectedError("deadlock detected")),
        _wrapped_operational(asyncpg.exceptions.QueryCanceledError("statement timeout")),
        _wrapped(_with_sqlstate(_AdapterError("lock timeout"), "55P03")),
        _wrapped(_with_sqlstate(_AdapterError("statement timeout"), "57014")),
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


def test_describe_and_redact_strip_sql_from_wrapped_strings() -> None:
    """A wrapper built from ``str(sqlalchemy_error)`` never echoes the SQL tail."""
    op = OperationalError("UPDATE jobs SET error=$1", ("secret-value",), Exception("gone"))
    wrapped = DatabaseUnavailableError(str(op))
    assert "[SQL:" in str(wrapped)
    for text in (describe_connection_loss(wrapped), redact_sql(str(wrapped)), redact_sql(str(op))):
        assert "UPDATE jobs" not in text
        assert "secret-value" not in text
        assert "gone" in text
    assert redact_sql("plain message") == "plain message"
