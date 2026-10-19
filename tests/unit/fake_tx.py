"""Unit-test stand-ins for a database the code under test opens transactions on.

``run_execution`` and the async ``ExecutionHandler`` take the database and open
their own transaction after the upstream call. Pure unit tests that assert on a
session stand-in wrap it in :class:`SessionScope` so those writes land on it,
and assert ``opened`` to pin how many transactions the code opened.
:class:`FailingTransactions` fails every transaction, the way a lost database
connection does.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from jentic_one.shared.db.errors import DatabaseUnavailableError


def post_upstream_write_failures() -> list[Any]:
    """Failures a write after the upstream call can hit, as ``pytest.param``s.

    A mapped error, a pool checkout timeout, a connection-invalidated
    ``DBAPIError``, a bare driver error, and a programming error: once the
    upstream call ran, none of them may requeue the job or drop its result.
    """
    return [
        pytest.param(DatabaseUnavailableError("connection lost"), id="mapped"),
        pytest.param(
            PoolTimeoutError("QueuePool limit of size 5 overflow 10 reached"), id="pool-timeout"
        ),
        pytest.param(
            DBAPIError(
                "SELECT 1", {}, Exception("connection is closed"), connection_invalidated=True
            ),
            id="dbapi-invalidated",
        ),
        pytest.param(asyncpg.exceptions.ConnectionDoesNotExistError("closed"), id="raw-driver"),
        pytest.param(TypeError("bad argument"), id="programming"),
    ]


class SessionScope:
    """Yield ``session`` from every ``transaction()``; count how many were opened.

    With ``commit=True`` a transaction commits on a clean exit and rolls back on
    an exception, like ``DatabaseSession.transaction``; use it with a real
    session when the test checks what was committed.
    """

    def __init__(self, session: Any, *, commit: bool = False) -> None:
        self.session = session
        self.opened = 0
        self._commit = commit

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        self.opened += 1
        if not self._commit:
            yield self.session
            return
        try:
            yield self.session
        except BaseException:
            await self.session.rollback()
            raise
        await self.session.commit()


class FailingTransactions:
    """Raise ``error`` from every ``transaction()``; count how many were opened."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.opened = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        self.opened += 1
        raise self.error
        yield  # pragma: no cover - never reached; makes this an async generator
