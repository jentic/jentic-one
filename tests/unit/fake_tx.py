"""A unit-test stand-in for a database whose ``transaction()`` yields one fixed session.

``run_execution`` and the async ``ExecutionHandler`` take the database and open
their own transaction after the upstream call. Pure unit tests that assert on a
session stand-in wrap it in :class:`SessionScope` so those writes land on it;
``opened`` counts the transactions the code under test opened.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


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
