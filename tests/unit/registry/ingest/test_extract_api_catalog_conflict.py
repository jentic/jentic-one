"""``ResolveApiStage`` catalog-identity collision guard (#1020).

After #1020 the catalog import derives the registry ``api_name`` from the
catalog id's *sub* segment, so two distinct catalog entries can collapse to the
same registry identity (``extract_vendor`` reduces hosts to eTLD+1: e.g.
``stripe.com/checkout`` and ``api.stripe.com/checkout`` both resolve to
``stripe-com/checkout``). These tests pin the guard's behaviour:

- a *different* stored ``catalog_api_id`` on the resolved identity refuses the
  import (no silent stacking of a foreign spec as a new revision),
- a matching id (ordinary re-import) and a NULL stored id (backfill of a
  manual import) proceed,
- a manual import (no incoming id) never triggers the guard.

Run against a real in-memory SQLite registry DB (no DB mocking —
``tests/arch/test_no_db_mocking.py``).
"""

from __future__ import annotations

import uuid
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
from jentic_one.registry.ingest.exc import IngestStageError
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.extract_api import ResolveApiStage
from jentic_one.registry.repos.api_repo import ApiRepository


def _create_registry_tables(sync_conn: Connection) -> None:
    """Create the tables these tests touch on SQLite, dropping Postgres-only
    server defaults (see ``test_api_repo_upsert`` for the rationale)."""
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


_IDENTIFIER = ApiIdentifier(vendor="stripe-com", name="checkout", version="1.0.0")


def _ctx(session: AsyncSession, *, catalog_api_id: str | None) -> PipelineContext:
    spec = IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=_IDENTIFIER,
        content={"openapi": "3.1.0"},
        catalog_api_id=catalog_api_id,
    )
    return PipelineContext(session=session, specification=spec, created_by="usr_test")


async def _seed(session: AsyncSession, *, catalog_api_id: str | None) -> None:
    await ApiRepository.upsert(
        session,
        vendor=_IDENTIFIER.vendor,
        name=_IDENTIFIER.name,
        version=_IDENTIFIER.version,
        created_by="usr_test",
        catalog_api_id=catalog_api_id,
    )


@pytest.mark.asyncio
async def test_conflicting_catalog_id_refuses_import(session: AsyncSession) -> None:
    await _seed(session, catalog_api_id="stripe.com/checkout")
    ctx = _ctx(session, catalog_api_id="api.stripe.com/checkout")
    with pytest.raises(IngestStageError, match="catalog identity conflict"):
        await ResolveApiStage().run(ctx)
    # The stored provenance must be untouched by the refused import.
    existing = await ApiRepository.get_by_identifier(
        session, _IDENTIFIER.vendor, _IDENTIFIER.name, _IDENTIFIER.version
    )
    assert existing is not None
    assert existing.catalog_api_id == "stripe.com/checkout"


@pytest.mark.asyncio
async def test_same_catalog_id_reimports(session: AsyncSession) -> None:
    await _seed(session, catalog_api_id="stripe.com/checkout")
    ctx = _ctx(session, catalog_api_id="stripe.com/checkout")
    await ResolveApiStage().run(ctx)
    assert isinstance(ctx.require("api_id", uuid.UUID), uuid.UUID)


@pytest.mark.asyncio
async def test_null_stored_id_backfills(session: AsyncSession) -> None:
    """A manual import of the same identity picks up the catalog id on the next
    catalog import (#910 backfill) — not a conflict."""
    await _seed(session, catalog_api_id=None)
    ctx = _ctx(session, catalog_api_id="stripe.com/checkout")
    await ResolveApiStage().run(ctx)
    existing = await ApiRepository.get_by_identifier(
        session, _IDENTIFIER.vendor, _IDENTIFIER.name, _IDENTIFIER.version
    )
    assert existing is not None
    assert existing.catalog_api_id == "stripe.com/checkout"


@pytest.mark.asyncio
async def test_manual_import_skips_guard(session: AsyncSession) -> None:
    """No incoming catalog id (manual/inline import) never conflicts, even when
    the identity was previously catalog-imported."""
    await _seed(session, catalog_api_id="stripe.com/checkout")
    ctx = _ctx(session, catalog_api_id=None)
    await ResolveApiStage().run(ctx)
    existing = await ApiRepository.get_by_identifier(
        session, _IDENTIFIER.vendor, _IDENTIFIER.name, _IDENTIFIER.version
    )
    assert existing is not None
    assert existing.catalog_api_id == "stripe.com/checkout"


@pytest.mark.asyncio
async def test_fresh_identity_creates(session: AsyncSession) -> None:
    ctx = _ctx(session, catalog_api_id="stripe.com/checkout")
    await ResolveApiStage().run(ctx)
    existing = await ApiRepository.get_by_identifier(
        session, _IDENTIFIER.vendor, _IDENTIFIER.name, _IDENTIFIER.version
    )
    assert existing is not None
    assert existing.catalog_api_id == "stripe.com/checkout"
