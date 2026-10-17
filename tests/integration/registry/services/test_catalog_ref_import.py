"""End-to-end catalog import through the entry's source, against the real registry DB.

``CatalogService.import_entry`` enqueues a ``type: "catalog"`` job source carrying
only the entry id; the import job resolves the spec through the source that lists
the entry (``CatalogRefResolver`` → ``CatalogSource.fetch_spec``). Uses the
test-only ``static`` kind so no HTTP is involved and nothing below the source is
stubbed.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from jentic_one.admin.core.schema.jobs import Job
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.ingest.exc import IngestJobError
from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.registry.services.catalog.sources.sync import CatalogSourceSync
from jentic_one.registry.services.errors import CatalogSpecGoneError
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.testing.catalog_source import (
    StaticEntry,
    register_static_source_kind,
    static_source_config,
)
from tests.integration.registry.services.catalog_sources_support import CtxFactory

pytestmark = pytest.mark.integration

_SPEC = json.dumps(
    {
        "openapi": "3.1.0",
        "info": {"title": "Sync Test", "version": "1.0.0"},
        "paths": {"/things": {"get": {"operationId": "listThings", "responses": {}}}},
    }
)
_ADMIN = Identity(sub="usr_admin", email="a@test.local", permissions=["org:admin"])


def _sources(*, spec: str | None = _SPEC) -> list[dict[str, Any]]:
    register_static_source_kind()
    cfg = static_source_config(
        "corp",
        [
            StaticEntry(
                external_id="sync-test.io",
                catalog_api_id_hint="sync-test.io",
                spec=spec,
            )
        ],
        trust="curated",
    )
    return [cfg.model_dump()]


async def _sync(ctx: Context) -> None:
    sync = CatalogSourceSync(ctx)
    await sync.register_sources()
    await sync.sync_source("corp")


async def _job_payload(ctx: Context, job_id: str) -> dict[str, Any]:
    async with ctx.admin_db.session() as session:
        row = await session.get(Job, job_id)
    assert row is not None and row.payload is not None
    return dict(row.payload)


async def test_import_job_resolves_the_spec_through_the_source(catalog_ctx: CtxFactory) -> None:
    register_static_source_kind()
    async with catalog_ctx(sources=_sources()) as ctx:
        await _sync(ctx)
        job_id = await CatalogService(ctx).import_entry("corp/sync-test.io", _ADMIN)
        payload = await _job_payload(ctx, job_id)
        [source] = payload["sources"]
        assert source["type"] == "catalog"
        assert source["catalog_entry_id"] == "corp/sync-test.io"
        # Only the entry id: no spec location, no source settings or credentials.
        assert not {"url", "content", "spec_ref", "settings", "auth"} & set(source)

        result = await ImportHandler(ctx).execute(
            job_id=str(uuid.uuid4()), session=None, payload=payload, created_by="usr_admin"
        )
        [revision] = result.body["revisions"]
        async with ctx.registry_db.session() as session:
            api = (
                await session.execute(select(Api).where(Api.vendor == revision["api"]["vendor"]))
            ).scalar_one()
            rev = (
                await session.execute(select(ApiRevision).where(ApiRevision.api_id == api.id))
            ).scalar_one()
        assert api.catalog_entry_id == "corp/sync-test.io"
        assert rev.source_type == "catalog"
        assert rev.origin == "catalog"
        assert rev.source_url is None  # the static source fetches by id, not URL


async def test_import_job_fails_readably_when_the_entry_is_gone(
    catalog_ctx: CtxFactory,
) -> None:
    """The entry was listed at enqueue time but not when the job runs."""
    register_static_source_kind()
    async with catalog_ctx(sources=_sources()) as ctx:
        payload = {
            "sources": [
                {
                    "type": "catalog",
                    "catalog_entry_id": "corp/never-listed.io",
                    "catalog_api_id": "never-listed.io",
                    "origin": "catalog",
                }
            ]
        }
        with pytest.raises(IngestJobError, match="no longer listed"):
            await ImportHandler(ctx).execute(
                job_id=str(uuid.uuid4()), session=None, payload=payload, created_by="usr_admin"
            )


async def test_card_only_entry_is_spec_gone(catalog_ctx: CtxFactory) -> None:
    """An entry listing no spec cannot be previewed or imported: 410, not a job."""
    register_static_source_kind()
    async with catalog_ctx(sources=_sources(spec=None)) as ctx:
        await _sync(ctx)
        svc = CatalogService(ctx)
        with pytest.raises(CatalogSpecGoneError):
            await svc.preview("corp/sync-test.io", permissions=frozenset({"org:admin"}))
        with pytest.raises(CatalogSpecGoneError):
            await svc.import_entry("corp/sync-test.io", _ADMIN)
