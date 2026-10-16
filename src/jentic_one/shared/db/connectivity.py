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
from sqlalchemy.exc import DBAPIError, DisconnectionError, OperationalError

from jentic_one.shared.db.errors import DatabaseUnavailableError

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


def _is_connection_loss_leaf(exc: BaseException) -> bool:
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    if isinstance(exc, (DisconnectionError, DatabaseUnavailableError)):
        return True
    if isinstance(exc, OperationalError):
        return True
    if isinstance(exc, _ASYNCPG_CONNECTION_ERRORS):
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


def describe_connection_loss(cause: BaseException, *, max_len: int = 200) -> str:
    """Short, log-safe reason for a connection-loss ``cause``.

    SQLAlchemy wrappers render the SQL statement and bound parameters in
    ``str()``; use the underlying DBAPI error's message instead so a warning
    never carries query text or parameter values.
    """
    source: BaseException = cause
    if isinstance(cause, DBAPIError) and isinstance(cause.orig, BaseException):
        source = cause.orig
    text = str(source).strip() or type(source).__name__
    return text[:max_len]
