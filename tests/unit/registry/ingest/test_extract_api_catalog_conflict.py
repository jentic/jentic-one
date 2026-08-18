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
``tests/arch/test_no_db_mocking.py``); the session fixture lives in the shared
``tests/unit/registry/conftest.py``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.ingest.exc import CatalogIdentityConflictError
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.extract_api import ResolveApiStage
from jentic_one.registry.repos.api_repo import ApiRepository

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
async def test_conflicting_catalog_id_refuses_import(apis_sqlite_session: AsyncSession) -> None:
    session = apis_sqlite_session
    await _seed(session, catalog_api_id="stripe.com/checkout")
    ctx = _ctx(session, catalog_api_id="api.stripe.com/checkout")
    # The typed subclass (an IngestStageError) lets the import job / dead-letter
    # path distinguish the guard's refusal; the message names both catalog ids
    # and the next step.
    with pytest.raises(CatalogIdentityConflictError, match="catalog identity conflict") as exc:
        await ResolveApiStage().run(ctx)
    assert "stripe.com/checkout" in str(exc.value)
    assert "api.stripe.com/checkout" in str(exc.value)
    assert "delete API" in str(exc.value)
    # The stored provenance must be untouched by the refused import.
    existing = await ApiRepository.get_by_identifier(
        session, _IDENTIFIER.vendor, _IDENTIFIER.name, _IDENTIFIER.version
    )
    assert existing is not None
    assert existing.catalog_api_id == "stripe.com/checkout"


@pytest.mark.asyncio
async def test_same_catalog_id_reimports(apis_sqlite_session: AsyncSession) -> None:
    session = apis_sqlite_session
    await _seed(session, catalog_api_id="stripe.com/checkout")
    ctx = _ctx(session, catalog_api_id="stripe.com/checkout")
    await ResolveApiStage().run(ctx)
    assert isinstance(ctx.require("api_id", uuid.UUID), uuid.UUID)


@pytest.mark.asyncio
async def test_null_stored_id_backfills(apis_sqlite_session: AsyncSession) -> None:
    """A manual import of the same identity picks up the catalog id on the next
    catalog import (#910 backfill) — not a conflict."""
    session = apis_sqlite_session
    await _seed(session, catalog_api_id=None)
    ctx = _ctx(session, catalog_api_id="stripe.com/checkout")
    await ResolveApiStage().run(ctx)
    existing = await ApiRepository.get_by_identifier(
        session, _IDENTIFIER.vendor, _IDENTIFIER.name, _IDENTIFIER.version
    )
    assert existing is not None
    assert existing.catalog_api_id == "stripe.com/checkout"


@pytest.mark.asyncio
async def test_manual_import_skips_guard(apis_sqlite_session: AsyncSession) -> None:
    """No incoming catalog id (manual/inline import) never conflicts, even when
    the identity was previously catalog-imported."""
    session = apis_sqlite_session
    await _seed(session, catalog_api_id="stripe.com/checkout")
    ctx = _ctx(session, catalog_api_id=None)
    await ResolveApiStage().run(ctx)
    existing = await ApiRepository.get_by_identifier(
        session, _IDENTIFIER.vendor, _IDENTIFIER.name, _IDENTIFIER.version
    )
    assert existing is not None
    assert existing.catalog_api_id == "stripe.com/checkout"


@pytest.mark.asyncio
async def test_fresh_identity_creates(apis_sqlite_session: AsyncSession) -> None:
    session = apis_sqlite_session
    ctx = _ctx(session, catalog_api_id="stripe.com/checkout")
    await ResolveApiStage().run(ctx)
    existing = await ApiRepository.get_by_identifier(
        session, _IDENTIFIER.vendor, _IDENTIFIER.name, _IDENTIFIER.version
    )
    assert existing is not None
    assert existing.catalog_api_id == "stripe.com/checkout"
