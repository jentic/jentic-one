"""Shared fixtures for unit registry tests that need real SQLite registry tables.

Provides in-memory SQLite registry sessions (no DB mocking —
``tests/arch/test_no_db_mocking.py``):

- ``apis_sqlite_session``: the ``apis`` and ``api_revisions`` tables.
- ``url_index_sqlite_session``: those plus ``operations`` and
  ``operation_url_indexes``, enough to run the URL-index lookups.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from functools import partial
from typing import Any, cast

import pytest
from sqlalchemy import Table
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql.functions import Function

import jentic_one.registry.core.schema  # noqa: F401  (register all registry tables)
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation


def _create_registry_tables(sync_conn: Connection, models: Sequence[Any]) -> None:
    """Create ``models``' tables on SQLite, dropping Postgres-only server defaults.

    ``api_revisions`` always comes with ``apis`` because ``apis`` carries an FK
    to it and SQLite refuses DML on a table whose referenced table is missing.
    The tests supply explicit values, so the defaults are dropped for the DDL
    and restored (leaving the shared models untouched). Only the Postgres-only
    *function* defaults (gen_random_uuid etc.) fail on SQLite; plain literal
    defaults (e.g. revision's "1") must stay.
    """
    tables = [cast(Table, model.__table__) for model in models]
    saved = {
        col: col.server_default
        for table in tables
        for col in table.columns
        if col.server_default is not None
        and isinstance(getattr(col.server_default, "arg", None), Function)
    }
    for col in saved:
        col.server_default = None
    try:
        for table in tables:
            sync_conn.execute(CreateTable(table, if_not_exists=True))
    finally:
        for col, default in saved.items():
            col.server_default = default


async def _sqlite_session(models: Sequence[Any]) -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(partial(_create_registry_tables, models=models))
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


@pytest.fixture()
async def apis_sqlite_session() -> AsyncGenerator[AsyncSession, None]:
    """A real in-memory SQLite registry session with the apis table created."""
    async for sess in _sqlite_session((Api, ApiRevision)):
        yield sess


@pytest.fixture()
async def url_index_sqlite_session() -> AsyncGenerator[AsyncSession, None]:
    """A real in-memory SQLite registry session with the URL-index lookup tables."""
    async for sess in _sqlite_session((Api, ApiRevision, Operation, OperationURLIndex)):
        yield sess
