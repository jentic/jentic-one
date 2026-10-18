"""``http-index`` end to end: per-source egress widening and env auth, real registry DB.

Only the socket layer is stubbed (``httpx.AsyncHTTPTransport``), so every
request still goes through the source's pooled client, its DNS-pinning SSRF
guard and its auth resolver.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select, update
from structlog.testing import capture_logs

from jentic_one.admin.core.schema.jobs import Job
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_entries import CatalogEntryRow
from jentic_one.registry.core.schema.catalog_sources import CatalogSourceRow
from jentic_one.registry.core.schema.catalog_sync_runs import CatalogSyncRun
from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.registry.services.catalog.sources.sync import CatalogSourceSync
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from tests.integration.registry.services.catalog_sources_support import CtxFactory

pytestmark = pytest.mark.integration

_SECRET = "s3cr3t-catalog-token-" + uuid.uuid4().hex  # pragma: allowlist secret
_ENV = "JENTIC_TEST_CATALOG_INDEX_TOKEN"
_PRIVATE_INDEX = "http://10.20.0.5/apis.json"
_PRIVATE_SPEC = "http://10.20.0.5/specs/sync-test.json"
_SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Sync Test", "version": "1.0.0"},
    "paths": {"/things": {"get": {"operationId": "listThings", "responses": {}}}},
}
_ADMIN = Identity(sub="usr_admin", email="a@test.local", permissions=["org:admin"])


def _index() -> dict[str, Any]:
    return {
        "apis": [
            {
                "aid": "sync-test.io",
                "name": "Sync Test",
                "x-catalog-api-id": "sync-test.io",
                "properties": [{"type": "OpenAPI", "url": "specs/sync-test.json"}],
            }
        ]
    }


@pytest.fixture()
def upstream(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    """Answer at the socket layer; records every request that got past the guard."""
    seen: list[httpx.Request] = []

    async def _handle(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = _index() if str(request.url) == _PRIVATE_INDEX else _SPEC
        return httpx.Response(
            200, content=json.dumps(body).encode(), headers={"content-type": "application/json"}
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _handle)
    monkeypatch.setenv(_ENV, _SECRET)
    yield seen


def _source(slug: str, **fields: Any) -> dict[str, Any]:
    return {
        "slug": slug,
        "kind": "http-index",
        "visibility": "public",
        "trust": "internal",
        "settings": {"index_url": _PRIVATE_INDEX},
        "max_age_seconds": 0,
        **fields,
    }


def _sources() -> list[dict[str, Any]]:
    return [
        _source(
            "wide",
            egress={"allowed_private_subnets": ["10.20.0.0/16"]},
            auth={"type": "bearer_from_env", "env": _ENV},
        ),
        _source("plain", auth={"type": "bearer_from_env", "env": _ENV}),
    ]


async def _drop_imported(ctx: Context) -> None:
    """Remove the APIs this module imports, so other modules' vendor lookups stay unique."""
    async with ctx.registry_db.transaction() as session:
        ids = select(Api.id).where(Api.catalog_entry_id.like("wide/%"))
        await session.execute(update(Api).where(Api.id.in_(ids)).values(current_revision_id=None))
        await session.execute(delete(ApiRevision).where(ApiRevision.api_id.in_(ids)))
        await session.execute(delete(Api).where(Api.catalog_entry_id.like("wide/%")))


async def _db_text(ctx: Context) -> str:
    """Every catalog row and every job row, rendered, to search for a leaked secret."""
    parts: list[str] = []
    async with ctx.registry_db.session() as session:
        for model in (CatalogSourceRow, CatalogSyncRun, CatalogEntryRow):
            for row in (await session.execute(select(model))).scalars().all():
                parts.append(repr({c.key: getattr(row, c.key) for c in model.__mapper__.columns}))
    async with ctx.admin_db.session() as session:
        for job in (await session.execute(select(Job))).scalars().all():
            parts.append(repr({c.key: getattr(job, c.key) for c in Job.__mapper__.columns}))
    return "\n".join(parts)


async def test_egress_widening_applies_to_its_own_source_only(
    catalog_ctx: CtxFactory, upstream: list[httpx.Request]
) -> None:
    async with catalog_ctx(sources=_sources()) as ctx:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        wide = await sync.sync_source("wide")
        assert (wide.status, wide.upserted) == ("applied", 1)
        reached = len(upstream)
        assert reached >= 1

        plain = await sync.sync_source("plain")
        assert plain.status == "failed"
        assert len(upstream) == reached  # refused before any socket was opened

        # The widened source still works after the other one was refused.
        assert (await sync.sync_source("wide")).status == "applied"


async def test_secret_reaches_the_upstream_and_nothing_else(
    catalog_ctx: CtxFactory, upstream: list[httpx.Request]
) -> None:
    async with catalog_ctx(sources=_sources()) as ctx:
        try:
            await _check_secret_confined(ctx, upstream)
        finally:
            await _drop_imported(ctx)


async def _check_secret_confined(ctx: Context, upstream: list[httpx.Request]) -> None:
    with capture_logs() as logs:
        sync = CatalogSourceSync(ctx)
        await sync.register_sources()
        assert (await sync.sync_source("wide")).status == "applied"
        job_id = await CatalogService(ctx).import_entry("wide/sync-test.io", _ADMIN)
        async with ctx.admin_db.session() as session:
            job = await session.get(Job, job_id)
        assert job is not None and job.payload is not None
        await ImportHandler(ctx).execute(
            job_id=str(uuid.uuid4()),
            session=None,
            payload=dict(job.payload),
            created_by="usr_admin",
        )

    urls = {str(r.url): r.headers.get("authorization") for r in upstream}
    assert urls[_PRIVATE_INDEX] == f"Bearer {_SECRET}"
    assert urls[_PRIVATE_SPEC] == f"Bearer {_SECRET}"
    assert _SECRET not in repr(logs)
    assert _SECRET not in await _db_text(ctx)
    assert _SECRET not in json.dumps(job.payload)
