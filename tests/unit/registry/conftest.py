"""Shared fixtures for unit registry tests that need a real SQLite ``apis`` table.

Promoted from ``repos/test_api_repo_upsert.py`` / ``ingest/test_extract_api_catalog_conflict.py``
(fixture-reuse rule: fixtures useful to more than one file live in the closest
shared conftest). Real in-memory SQLite, no DB mocking
(``tests/arch/test_no_db_mocking.py``).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import cast

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


def _create_registry_tables(sync_conn: Connection) -> None:
    """Create the tables these tests touch on SQLite, dropping Postgres-only
    server defaults.

    ``api_revisions`` too (not just ``apis``) because ``apis`` carries an FK to
    it and SQLite refuses DML on a table whose referenced table is missing. The
    tests supply explicit values, so the defaults are dropped for the DDL and
    restored (leaving the shared models untouched). Only the Postgres-only
    *function* defaults (gen_random_uuid etc.) fail on SQLite; plain literal
    defaults (e.g. revision's "1") must stay.
    """
    tables = (cast(Table, Api.__table__), cast(Table, ApiRevision.__table__))
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


@pytest.fixture()
async def apis_sqlite_session() -> AsyncGenerator[AsyncSession, None]:
    """A real in-memory SQLite registry session with the apis table created."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(_create_registry_tables)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()
