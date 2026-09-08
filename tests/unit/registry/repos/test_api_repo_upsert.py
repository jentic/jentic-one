"""``ApiRepository.upsert`` semantics for the persisted catalog identity (#910).

The catalog slug (`domain[/sub-api]`) is display provenance, not resolution
identity — these tests pin the update rules that keep it trustworthy:

- a catalog import records it on create,
- a catalog re-import refreshes (or backfills) it,
- a manual re-import of the same triple (``None``) never clears one already
  recorded (e.g. overlay materialization re-ingests inline without the slug).

Run against a real in-memory SQLite registry DB (no DB mocking —
``tests/arch/test_no_db_mocking.py``).
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
from jentic_one.registry.repos.api_repo import ApiRepository


def _create_registry_tables(sync_conn: Connection) -> None:
    """Create the tables these tests touch on SQLite, dropping Postgres-only
    server defaults.

    ``api_revisions`` too (not just ``apis``) because ``apis`` carries an FK to
    it and SQLite refuses DML on a table whose referenced table is missing. The
    tests supply explicit values, so the defaults are dropped for the DDL and
    restored (leaving the shared models untouched).
    """
    tables = (cast(Table, Api.__table__), cast(Table, ApiRevision.__table__))
    # Only the Postgres-only *function* defaults (gen_random_uuid etc.) fail on
    # SQLite; plain literal defaults (e.g. revision's "1") must stay.
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
async def session() -> AsyncGenerator[AsyncSession, None]:
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


_TRIPLE = {"vendor": "nytimes.com", "name": "nytimes-com-article-search", "version": "1.0.0"}


@pytest.mark.asyncio
async def test_catalog_import_records_slug_on_create(session: AsyncSession) -> None:
    api = await ApiRepository.upsert(
        session,
        **_TRIPLE,
        created_by="usr_test",
        catalog_api_id="nytimes.com/article_search",
    )
    assert api.catalog_api_id == "nytimes.com/article_search"


@pytest.mark.asyncio
async def test_manual_import_leaves_slug_null(session: AsyncSession) -> None:
    api = await ApiRepository.upsert(session, **_TRIPLE, created_by="usr_test")
    assert api.catalog_api_id is None


@pytest.mark.asyncio
async def test_catalog_reimport_backfills_existing_row(session: AsyncSession) -> None:
    """A pre-#910 row (or manual first import) picks up the slug on its next
    catalog import of the same triple."""
    first = await ApiRepository.upsert(session, **_TRIPLE, created_by="usr_test")
    assert first.catalog_api_id is None

    again = await ApiRepository.upsert(
        session,
        **_TRIPLE,
        created_by="usr_test",
        catalog_api_id="nytimes.com/article_search",
    )
    assert again.id == first.id
    assert again.catalog_api_id == "nytimes.com/article_search"


@pytest.mark.asyncio
async def test_slugless_reimport_never_clears_recorded_slug(session: AsyncSession) -> None:
    """Overlay materialization and manual re-imports pass ``None`` — that must
    not erase the identity a catalog import already recorded."""
    await ApiRepository.upsert(
        session,
        **_TRIPLE,
        created_by="usr_test",
        catalog_api_id="nytimes.com/article_search",
    )
    again = await ApiRepository.upsert(session, **_TRIPLE, created_by="usr_test")
    assert again.catalog_api_id == "nytimes.com/article_search"
