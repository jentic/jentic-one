"""Structural types for code that is handed a database rather than a session."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class TransactionalDatabase(Protocol):
    """A database the holder opens its own short write transactions on.

    ``DatabaseSession`` satisfies it. Code that takes one (rather than an open
    session) decides when a transaction starts, so it can keep slow work such
    as an upstream call outside any transaction.
    """

    def transaction(self) -> AbstractAsyncContextManager[AsyncSession]: ...
