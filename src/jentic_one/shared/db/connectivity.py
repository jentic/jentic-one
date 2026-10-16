"""Classify exceptions that mean "the database connection is gone".

Long-running loops (the job worker) treat a lost or unreachable database as an
expected, recoverable condition — log once, back off, reconnect — rather than
as a bug. The match is deliberately narrow so genuine programming and data
errors still surface with a traceback.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator

import asyncpg
from sqlalchemy.exc import DBAPIError, DisconnectionError

# Server-side conditions that drop or refuse sessions: SQLSTATE class 08
# (connection exception, e.g. ``ConnectionDoesNotExistError``) plus the
# shutdown / starting-up states seen while Postgres restarts.
_ASYNCPG_CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    asyncpg.PostgresConnectionError,
    asyncpg.exceptions.AdminShutdownError,
    asyncpg.exceptions.CrashShutdownError,
    asyncpg.exceptions.CannotConnectNowError,
)

# Network-level failures reaching the server. Only the bare ``OSError`` type is
# matched (asyncio raises it for unreachable hosts and multi-address connect
# failures); filesystem subclasses such as ``FileNotFoundError`` are not.
_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    socket.gaierror,
)

# SQLSTATEs that mean the session is gone, for driver errors that are not one
# of the asyncpg classes above (e.g. SQLAlchemy's asyncpg adapter error, which
# copies ``sqlstate`` from the asyncpg exception): class 08 plus the
# admin/crash shutdown and "starting up" states. Lock timeouts (55P03),
# statement timeouts (57014), serialization failures (40001) and deadlocks
# (40P01) arrive on a live connection and are deliberately absent.
_CONNECTION_SQLSTATE_CLASS = "08"
_CONNECTION_SQLSTATES = frozenset({"57P01", "57P02", "57P03"})

# SQLAlchemy appends the statement and bound parameters to ``str(exc)`` after
# the driver message; everything from this marker on is dropped before an
# error string is logged or persisted.
_SQL_MARKER = "[SQL:"

_MAX_CHAIN_DEPTH = 8


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """Yield ``exc`` and its explicit causes (``__cause__`` and DBAPI ``.orig``).

    ``__context__`` is not followed: an unrelated error raised while handling a
    connection error must not be mistaken for one.
    """
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending and len(seen) < _MAX_CHAIN_DEPTH:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        orig = getattr(current, "orig", None)
        if isinstance(orig, BaseException):
            pending.append(orig)
        if current.__cause__ is not None:
            pending.append(current.__cause__)


def _has_connection_sqlstate(exc: BaseException) -> bool:
    sqlstate = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    if not isinstance(sqlstate, str):
        return False
    return sqlstate.startswith(_CONNECTION_SQLSTATE_CLASS) or sqlstate in _CONNECTION_SQLSTATES


def _is_connection_loss_leaf(exc: BaseException) -> bool:
    """True when ``exc`` itself (not its causes) marks a lost connection.

    SQLAlchemy wrappers (``OperationalError`` and every other ``DBAPIError``)
    and the domain ``DatabaseUnavailableError`` are *not* matched by type:
    they also carry live-connection failures (SQLite ``no such table`` /
    ``database is locked``, lock and statement timeouts). They only count when
    SQLAlchemy flagged the connection invalidated, or when a cause in the
    chain (``.orig`` / ``__cause__``) is itself a connection error.
    """
    if isinstance(exc, DBAPIError):
        return exc.connection_invalidated
    if isinstance(exc, DisconnectionError):
        return True
    if isinstance(exc, _ASYNCPG_CONNECTION_ERRORS):
        return True
    if _has_connection_sqlstate(exc):
        return True
    # asyncpg's bare InterfaceError covers "connection is closed"; its
    # subclasses (DataError, ClientConfigurationError, …) are real bugs.
    if type(exc) is asyncpg.InterfaceError and "connection is closed" in str(exc):
        return True
    return isinstance(exc, _NETWORK_ERRORS) or type(exc) is OSError


def connection_loss_cause(exc: BaseException) -> BaseException | None:
    """Return the exception in ``exc``'s cause chain that marks a lost connection.

    Returns ``None`` when ``exc`` is not a connection-loss error. The innermost
    match is preferred (the driver/network error rather than its wrapper) so
    callers can log a short, specific reason without the SQL statement.
    """
    match: BaseException | None = None
    for link in _chain(exc):
        if _is_connection_loss_leaf(link):
            match = link
    return match


def is_connection_loss(exc: BaseException) -> bool:
    """True when ``exc`` means the database connection was lost or unreachable."""
    return connection_loss_cause(exc) is not None


def redact_sql(text: str) -> str:
    """Drop the ``[SQL: …]`` / ``[parameters: …]`` tail SQLAlchemy appends to errors.

    Applies to SQLAlchemy exceptions and to domain errors built from their
    ``str()`` (``DatabaseUnavailableError(str(exc))``), so query text and
    bound values never reach logs or persisted job/event fields.
    """
    head, _, _ = text.partition(_SQL_MARKER)
    return head.strip()


def describe_connection_loss(cause: BaseException, *, max_len: int = 200) -> str:
    """Short, log-safe reason for a connection-loss ``cause``.

    Prefers the underlying DBAPI error's message over the SQLAlchemy wrapper
    and strips any SQL tail, so the result never carries query text or
    parameter values.
    """
    source: BaseException = cause
    if isinstance(cause, DBAPIError) and isinstance(cause.orig, BaseException):
        source = cause.orig
    text = redact_sql(str(source)) or type(source).__name__
    return text[:max_len]
