"""Web tests for the registry catalog (Discover) endpoints.

The whole stack runs against the real Postgres test DB; only the upstream
fetches are mocked. ``catalog.fetch.httpx.AsyncClient`` is patched with a
MockTransport that serves in-memory ``git-manifest`` indexes (one per source
URL) and a tiny fake spec, mirroring the ingest fetch unit-test pattern (no
real network).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import sys
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp import types as mcp_types
from mcp.shared.exceptions import MCPError
from sqlalchemy import delete, select, text, update

import jentic_one.mcp.envelopes as mcp_envelopes
import jentic_one.mcp.tools as tools_mod
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos import AccessTokenRepository
from jentic_one.auth.services.token_service import _hash_token
from jentic_one.auth.web.app import make_superset_verifier
from jentic_one.mcp.tools import CallEnv
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_entries import CatalogEntryRow
from jentic_one.registry.core.schema.catalog_snapshots import CatalogSnapshot
from jentic_one.registry.core.schema.catalog_sources import CatalogSourceRow
from jentic_one.registry.core.schema.catalog_sync_runs import CatalogSyncRun
from jentic_one.registry.core.schema.catalog_update_checks import CatalogUpdateCheck
from jentic_one.registry.repos.catalog_source_repo import CatalogSourceRepository
from jentic_one.registry.services.catalog import service as service_module
from jentic_one.registry.services.catalog.entries import build_catalog_entry
from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.registry.services.catalog.snapshot import clear_snapshots
from jentic_one.registry.services.catalog.sources.scanner import CatalogSourceSyncScanner
from jentic_one.registry.services.catalog.sources.sync import CatalogSourceSync, SyncOutcome
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.registry.web.app import create_app
from jentic_one.shared.auth.api_key_resolver import AGENT_API_KEY_PREFIX
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig, CatalogConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.utils import utcnow
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.api_identity import canonical_credential_scope, credential_covers
from jentic_one.shared.pagination import encode_catalog_cursor
from jentic_one.shared.web.deps import resolve_identity
from jentic_one.wiring import InProcessCatalogAutoImporter
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration

_RAW = "https://raw.githubusercontent.com"
_MANIFEST_BASE = f"{_RAW}/jentic/jentic-public-apis/main/apis"
_MANIFEST_URL = f"{_MANIFEST_BASE}/openapi/apis.json"
_CORP_BASE = f"{_RAW}/corp/catalog/main/apis"
_CORP_URL = f"{_CORP_BASE}/openapi/apis.json"
_VAULT_BASE = f"{_RAW}/vault/catalog/main/apis"
_VAULT_URL = f"{_VAULT_BASE}/openapi/apis.json"

#: The unpatched client class (the autouse fixture replaces ``httpx.AsyncClient``).
_REAL_ASYNC_CLIENT = httpx.AsyncClient

#: Manifest URL -> the ``catalog_api_id``s it lists (``{domain}`` or ``{domain}/{sub}``).
_LISTINGS: dict[str, list[str]] = {}

#: Spec URL -> a canned failure ``(status, headers)`` served instead of the spec.
_SPEC_FAILURES: dict[str, tuple[int, dict[str, str]]] = {}


def _include_url(base: str, api_id: str) -> str:
    domain, _, sub = api_id.partition("/")
    return f"{base}/openapi/{domain}/{sub or 'main'}/1.0/apis.json"


def _spec_url(base: str, api_id: str) -> str:
    return _include_url(base, api_id).replace("/apis.json", "/openapi.json")


def _manifest(url: str) -> dict[str, Any] | None:
    base = url.removesuffix("/openapi/apis.json")
    if url not in _LISTINGS:
        return None
    return {"include": [{"url": _include_url(base, a)} for a in _LISTINGS[url]]}


def _spec() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Stripe", "version": "2024-01-01", "description": "payments"},
        "paths": {
            "/charges": {
                "get": {"summary": "list charges", "operationId": "listCharges", "tags": ["c"]},
                "post": {"summary": "create charge", "tags": ["c"]},
            }
        },
    }


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in _SPEC_FAILURES:
            status, headers = _SPEC_FAILURES[url]
            return httpx.Response(status, headers=headers)
        body = sys.modules[__name__]._manifest(url)
        if body is None and url.endswith("/openapi.json"):
            body = _spec()
        if body is None:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(
            200, content=json.dumps(body).encode(), headers={"content-type": "application/json"}
        )

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _mock_github() -> Iterator[None]:
    """Route every catalog fetch through an in-memory MockTransport (no network)."""
    real_client_cls = httpx.AsyncClient

    def _factory(*_args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real_client_cls(transport=_mock_transport(), **kwargs)  # type: ignore[arg-type]

    _LISTINGS.clear()
    _SPEC_FAILURES.clear()
    _LISTINGS[_MANIFEST_URL] = ["stripe.com", "slack.com"]
    with patch("jentic_one.registry.services.catalog.fetch.httpx.AsyncClient", _factory):
        yield
    _LISTINGS.clear()
    _SPEC_FAILURES.clear()


async def _truncate(ctx: Context) -> None:
    async with ctx.registry_db.session() as session:
        await session.execute(text("UPDATE registry.apis SET current_revision_id = NULL"))
        await session.execute(delete(ApiRevision))
        await session.execute(delete(Api))
        await session.execute(delete(CatalogSnapshot))
        await session.execute(delete(CatalogUpdateCheck))
        await session.execute(delete(CatalogEntryRow))
        await session.execute(delete(CatalogSyncRun))
        await session.execute(delete(CatalogSourceRow))
        await session.commit()
    clear_snapshots()


@pytest.fixture(autouse=True)
async def _clean_catalog(web_context: Context) -> AsyncGenerator[None, None]:
    """Empty the catalog tables (and the local APIs) around each test."""
    await _truncate(web_context)
    yield
    await _truncate(web_context)


# ── helpers ──────────────────────────────────────────────────────────────────


def _public_source(**fields: Any) -> dict[str, Any]:
    return {
        "slug": "public",
        "kind": "git-manifest",
        "visibility": "public",
        "trust": "curated",
        "precedence": 10,
        "settings": {"url": _MANIFEST_URL},
        **fields,
    }


def _corp_source(**fields: Any) -> dict[str, Any]:
    return {
        "slug": "corp",
        "kind": "git-manifest",
        "visibility": "org",
        "trust": "internal",
        "precedence": 20,
        "settings": {"url": _CORP_URL},
        **fields,
    }


def _vault_source(**fields: Any) -> dict[str, Any]:
    return {
        "slug": "vault",
        "kind": "git-manifest",
        "visibility": "restricted",
        "trust": "internal",
        "precedence": 5,
        "readers": {"permissions": ["vault:read"], "actor_ids": ["agnt_vault"]},
        "settings": {"url": _VAULT_URL},
        **fields,
    }


def _config(base: AppConfig, sources: Sequence[dict[str, Any]], **catalog: Any) -> AppConfig:
    return base.model_copy(
        update={"catalog": CatalogConfig.model_validate({"sources": list(sources), **catalog})}
    )


@contextmanager
def _client(
    ctx: Context, permissions: Sequence[str], sub: str = "usr_reader"
) -> Iterator[TestClient]:
    """A TestClient whose caller holds exactly ``permissions`` (unexpanded)."""
    app = create_app(ctx)
    app.router.lifespan_context = noop_lifespan
    actor_type = ActorType.AGENT if sub.startswith("agnt_") else ActorType.USER
    identity = Identity(
        sub=sub, email=f"{sub}@test.local", permissions=list(permissions), actor_type=actor_type
    )

    async def _override(_: object = None) -> Identity:
        return identity

    app.dependency_overrides[resolve_identity] = _override
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as tc:
        yield tc


def _ids(body: dict[str, Any]) -> list[str]:
    return [e["entry_id"] for e in body["data"]]


async def _register(
    ctx: Context,
    entry_id: str | None,
    *,
    vendor: str,
    name: str,
    spec_url: str,
    catalog_api_id: str,
) -> uuid.UUID:
    """A local API imported from ``entry_id`` with one draft revision (as an import leaves it).

    ``entry_id=None`` is an API imported before entry ids were recorded.
    """
    async with ctx.registry_db.session() as session:
        api = Api(
            vendor=vendor,
            name=name,
            version="1.0",
            revision_count=1,
            catalog_api_id=catalog_api_id,
            catalog_entry_id=entry_id,
        )
        session.add(api)
        await session.flush()
        session.add(
            ApiRevision(
                api_id=api.id,
                state="draft",
                spec_digest=f"sha256:{entry_id or catalog_api_id}",
                source_type="url",
                source_url=spec_url,
            )
        )
        await session.commit()
        return api.id


async def _outdate(ctx: Context, api_id: uuid.UUID, entry_id: str) -> None:
    """Record a notified upstream update the API's served revision has not adopted."""
    async with ctx.registry_db.transaction() as session:
        session.add(
            CatalogUpdateCheck(
                local_api_id=api_id,
                catalog_entry_id=entry_id,
                last_notified_digest="sha256:upstream",
            )
        )


async def _job_source(ctx: Context, job_id: str) -> dict[str, Any]:
    async with ctx.admin_db.session() as session:
        row = await session.get(Job, job_id)
        assert row is not None
        assert row.payload is not None
        return dict(row.payload["sources"][0])


def _run_scanner(tc: TestClient) -> list[SyncOutcome]:
    """Run one ``CatalogSourceSyncScanner`` tick on the app's loop, as its task would."""
    ctx: Context = tc.app.state.ctx  # type: ignore[attr-defined]
    return tc.portal.call(CatalogSourceSyncScanner(ctx).tick)  # type: ignore[union-attr]


def _sync_now(tc: TestClient) -> None:
    """Request a sync of every source the caller sees, then let the scanner run them."""
    for source in tc.get("/catalog/sources").json()["data"]:
        assert tc.post(f"/catalog/sources/{source['slug']}:sync").status_code == 202
    _run_scanner(tc)


async def _sync_all(ctx: Context) -> None:
    """Register and sync every configured source once, in configured order."""
    sync = CatalogSourceSync(ctx)
    await sync.register_sources()
    for slug in sync.sources:
        await sync.sync_source(slug)


async def _seed(ctx: Context, api_ids: list[str]) -> None:
    """Serve ``api_ids`` from the public manifest and sync it."""
    _LISTINGS[_MANIFEST_URL] = list(api_ids)
    await _sync_all(ctx)


# ── auth ─────────────────────────────────────────────────────────────────────


def test_list_catalog_requires_auth(unauthed_client: TestClient) -> None:
    assert unauthed_client.get("/catalog").status_code == 401


def test_sync_requires_catalog_sync(web_context: Context) -> None:
    """A valid identity without catalog:sync is rejected with 403 (not just presence)."""
    with _client(web_context, ["capabilities:read"]) as tc:
        assert tc.post("/catalog/sources/public:sync").status_code == 403


def test_sync_invalid_token_401(unauthed_client: TestClient) -> None:
    resp = unauthed_client.post(
        "/catalog/sources/public:sync", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401


def test_sync_without_auth_401(unauthed_client: TestClient) -> None:
    assert unauthed_client.post("/catalog/sources/public:sync").status_code == 401


def test_refresh_route_is_gone(admin_client: TestClient) -> None:
    """``POST /catalog/sources/{slug}:sync`` replaced ``POST /catalog:refresh``."""
    assert admin_client.post("/catalog:refresh").status_code in (404, 405)


# ── sync + list ──────────────────────────────────────────────────────────────


def test_sync_then_list(admin_client: TestClient) -> None:
    r = admin_client.post("/catalog/sources/public:sync")
    assert r.status_code == 202
    body = r.json()
    assert {k: body[k] for k in ("slug", "status", "forced")} == {
        "slug": "public",
        "status": "queued",
        "forced": False,
    }
    assert body["due_at"] is not None
    assert admin_client.post("/catalog/sources/public:sync").json()["status"] == "coalesced"
    _run_scanner(admin_client)

    listing = admin_client.get("/catalog")
    assert listing.status_code == 200
    body = listing.json()
    assert _ids(body) == ["public/slack.com", "public/stripe.com"]
    stripe = body["data"][1]
    assert stripe["source_slug"] == "public"
    assert stripe["catalog_api_id"] == "stripe.com"
    assert stripe["has_spec"] is True
    assert stripe["alternates"] == 0
    assert stripe["_links"]["self"].endswith("/catalog/public/stripe.com")
    assert stripe["_links"]["alternates"] == []
    for removed in ("api_id", "path", "spec_url", "github_url", "content_hint"):
        assert removed not in stripe
    assert body["catalog_total"] == 2
    assert body["oldest_source_sync_age_seconds"] is not None
    assert "manifest_age_seconds" not in body


async def test_sync_writes_catalog_entries(admin_client: TestClient, web_context: Context) -> None:
    _sync_now(admin_client)
    async with web_context.registry_db.session() as session:
        rows = (
            await session.execute(
                select(CatalogEntryRow.entry_id, CatalogEntryRow.catalog_api_id).where(
                    CatalogEntryRow.removed_at.is_(None)
                )
            )
        ).all()
    assert sorted(rows) == [("public/slack.com", "slack.com"), ("public/stripe.com", "stripe.com")]


async def test_reads_ignore_the_legacy_snapshot_table(
    admin_client: TestClient, web_context: Context
) -> None:
    """``catalog_snapshots`` no longer backs reads: a stale row there changes nothing."""
    _sync_now(admin_client)
    before = admin_client.get("/catalog").json()
    async with web_context.registry_db.session() as session:
        session.add(
            CatalogSnapshot(
                source_url=_MANIFEST_URL,
                entry_count=1,
                entries=[{"api_id": "ghost.example", "vendor": "ghost.example"}],
            )
        )
        await session.commit()
    after = admin_client.get("/catalog").json()
    assert _ids(after) == _ids(before)


async def test_forced_sync_overrides_a_suspect_empty_listing(
    admin_client: TestClient, web_context: Context
) -> None:
    _sync_now(admin_client)

    async def _live() -> list[str]:
        async with web_context.registry_db.session() as session:
            rows = await session.execute(
                select(CatalogEntryRow.entry_id).where(CatalogEntryRow.removed_at.is_(None))
            )
            return sorted(r[0] for r in rows.all())

    async def _status() -> str:
        async with web_context.registry_db.session() as session:
            row = (
                await session.execute(
                    select(CatalogSourceRow).where(CatalogSourceRow.slug == "public")
                )
            ).scalar_one()
            return str(row.last_status)

    assert await _live() == ["public/slack.com", "public/stripe.com"]

    # Upstream now lists nothing: the guard holds the removal of every row.
    # (Age the first run past min_sync_interval_seconds so the re-sync is due now.)
    async with web_context.registry_db.transaction() as session:
        await session.execute(
            update(CatalogSyncRun).values(started_at=utcnow() - timedelta(minutes=5))
        )
    _LISTINGS[_MANIFEST_URL] = []
    _sync_now(admin_client)
    assert await _live() == ["public/slack.com", "public/stripe.com"]
    assert await _status() == "suspect_empty"

    forced = admin_client.post("/catalog/sources/public:sync", params={"force": "true"})
    assert forced.status_code == 202
    assert forced.json()["forced"] is True
    _run_scanner(admin_client)
    assert await _live() == []
    assert await _status() == "ok"
    async with web_context.registry_db.session() as session:
        runs = (await session.execute(select(CatalogSyncRun.forced, CatalogSyncRun.status))).all()
    assert (True, "applied") in [tuple(r) for r in runs]


async def test_lazy_read_flags_stale_sources_and_starts_no_sync_task(
    web_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _LISTINGS[_CORP_URL] = ["corp-a.example"]
    cfg = _config(web_config, [_public_source(), _corp_source(visibility="public")])
    synced: list[str] = []
    real_sync = CatalogSourceSync.sync_source

    async def _spy(self: CatalogSourceSync, slug: str, **kwargs: Any) -> Any:
        synced.append(slug)
        return await real_sync(self, slug, **kwargs)

    monkeypatch.setattr(CatalogSourceSync, "sync_source", _spy)
    async with Context(cfg) as ctx:
        await _truncate(ctx)
        before = set(asyncio.all_tasks())
        view = await CatalogService(ctx).list_all(permissions=frozenset())
        assert view.catalog_total == 3
        # Never-synced git-manifest sources sync inline on the first read.
        assert sorted(synced) == ["corp", "public"]

        # Once synced, a stale source is only flagged for the scanner.
        synced.clear()
        async with ctx.registry_db.transaction() as session:
            await session.execute(
                text(
                    "UPDATE registry.catalog_sources SET last_sync_at = now() - interval '30 days'"
                    " WHERE slug = 'corp'"
                )
            )
        await CatalogService(ctx).list_all(permissions=frozenset())
        leftover = [
            t
            for t in asyncio.all_tasks() - before
            if not t.done() and "update_notify" not in repr(t.get_coro())
        ]
        assert leftover == []
        assert synced == []
        async with ctx.registry_db.session() as session:
            rows = {r.slug: r for r in (await session.execute(select(CatalogSourceRow))).scalars()}
        assert rows["corp"].sync_wanted_at is not None
        assert rows["corp"].sync_lease_owner is None
        await _truncate(ctx)


async def test_first_sync_over_the_fetch_timeout_fails_and_stops_blocking_reads(
    web_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A never-synced source whose listing outlasts ``fetch_timeout_s`` ends a failed
    run (so the scanner backs off) and releases its lease; later reads skip it."""
    _LISTINGS[_CORP_URL] = ["corp-a.example"]
    cfg = _config(web_config, [_public_source(), _corp_source(visibility="public")])
    cfg = cfg.model_copy(update={"ingest": cfg.ingest.model_copy(update={"fetch_timeout_s": 0.3})})

    async def _slow(request: httpx.Request) -> httpx.Response:
        if str(request.url) == _CORP_URL:
            await asyncio.sleep(5)
        response = _mock_transport().handler(request)
        assert isinstance(response, httpx.Response)
        return response

    def _factory(*_args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(_slow), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("jentic_one.registry.services.catalog.fetch.httpx.AsyncClient", _factory)
    synced: list[str] = []
    real_sync = CatalogSourceSync.sync_source

    async def _spy(self: CatalogSourceSync, slug: str, **kwargs: Any) -> Any:
        synced.append(slug)
        return await real_sync(self, slug, **kwargs)

    monkeypatch.setattr(CatalogSourceSync, "sync_source", _spy)
    async with Context(cfg) as ctx:
        await _truncate(ctx)
        started = asyncio.get_running_loop().time()
        view = await CatalogService(ctx).list_all(permissions=frozenset())
        assert asyncio.get_running_loop().time() - started < 3
        assert {e.source_slug for e in view.data} == {"public"}
        async with ctx.registry_db.session() as session:
            corp = (
                await session.execute(
                    select(CatalogSourceRow).where(CatalogSourceRow.slug == "corp")
                )
            ).scalar_one_or_none()
            runs = (
                (
                    await session.execute(
                        select(CatalogSyncRun).where(CatalogSyncRun.source_slug == "corp")
                    )
                )
                .scalars()
                .all()
            )
        assert corp is not None
        assert corp.sync_lease_owner is None
        assert corp.consecutive_failures == 1
        assert corp.last_status == "failed"
        assert [(r.status, r.error_code) for r in runs] == [("failed", "listing_timeout")]

        synced.clear()
        started = asyncio.get_running_loop().time()
        await CatalogService(ctx).list_all(permissions=frozenset())
        assert asyncio.get_running_loop().time() - started < 1
        assert synced == []
        await _truncate(ctx)


def test_list_search_filter(admin_client: TestClient) -> None:
    _sync_now(admin_client)
    body = admin_client.get("/catalog", params={"q": "stripe"}).json()
    assert _ids(body) == ["public/stripe.com"]


def test_search_never_matches_the_source_slug(admin_client: TestClient) -> None:
    """The scorer tokenises ``catalog_api_id``, never ``entry_id``."""
    _sync_now(admin_client)
    assert admin_client.get("/catalog", params={"q": "public"}).json()["data"] == []


# ── pagination ───────────────────────────────────────────────────────────────


def _walk_catalog(client: TestClient, **params: str | int | bool) -> list[str]:
    """Page through /catalog via next_cursor and return the flat entry_id list."""
    out: list[str] = []
    cursor: str | None = None
    for _ in range(1000):
        query: dict[str, str | int | bool] = {**params}
        if cursor is not None:
            query["cursor"] = cursor
        body = client.get("/catalog", params=query).json()
        out.extend(_ids(body))
        if not body["has_more"]:
            return out
        cursor = body["next_cursor"]
        assert cursor is not None
    raise AssertionError("pagination did not terminate")


async def test_browse_pagination_walks_all_entries_once(
    admin_client: TestClient, web_context: Context
) -> None:
    # Listed in a non-sorted order so a "return everything, ignore cursor" bug
    # can't pass by coincidence: the walk must re-impose entry_id order.
    ids = [f"api-{i:03d}.com" for i in range(25)]
    await _seed(web_context, ids[12:] + ids[:12])

    first = admin_client.get("/catalog", params={"limit": 10}).json()
    assert len(first["data"]) == 10
    assert first["has_more"] is True
    assert first["next_cursor"] is not None
    assert first["catalog_total"] == 25

    walked = _walk_catalog(admin_client, limit=10)
    assert walked == [f"public/{a}" for a in sorted(ids)]


async def test_final_page_has_no_more_and_null_cursor(
    admin_client: TestClient, web_context: Context
) -> None:
    await _seed(web_context, [f"api-{i:03d}.com" for i in range(7)])
    cursor: str | None = None
    for _ in range(10):
        params: dict[str, str | int] = {"limit": 3}
        if cursor:
            params["cursor"] = cursor
        body = admin_client.get("/catalog", params=params).json()
        if not body["has_more"]:
            break
        cursor = body["next_cursor"]
    assert body["has_more"] is False
    assert body["next_cursor"] is None


async def test_search_pagination_walks_ranked_results_in_order(
    admin_client: TestClient, web_context: Context
) -> None:
    # foo-bar covers both tokens and outranks the single-token halves.
    await _seed(web_context, ["foo-bar.com", "foo-x.com", "bar-y.com", "unrelated.com"])
    walked = _walk_catalog(admin_client, q="foo bar", limit=1)
    assert walked == ["public/foo-bar.com", "public/bar-y.com", "public/foo-x.com"]


async def test_filtered_walk_exact_set_and_count_stability(
    admin_client: TestClient, web_context: Context
) -> None:
    """An unregistered_only walk returns exactly the unregistered set; counters stay stable."""
    ids = [f"api-{i:02d}.com" for i in range(10)]
    await _seed(web_context, ids)
    registered = {ids[2], ids[5], ids[8]}
    for i, a in enumerate(sorted(registered)):
        await _register(
            web_context,
            f"public/{a}",
            vendor=a,
            name=f"n{i}",
            spec_url=_spec_url(_MANIFEST_BASE, a),
            catalog_api_id=a,
        )

    out: list[str] = []
    counts: list[tuple[int, int]] = []
    cursor: str | None = None
    for _ in range(1000):
        query: dict[str, str | int | bool] = {"unregistered_only": True, "limit": 2}
        if cursor is not None:
            query["cursor"] = cursor
        body = admin_client.get("/catalog", params=query).json()
        out.extend(_ids(body))
        counts.append((body["catalog_total"], body["registered_count"]))
        if not body["has_more"]:
            break
        cursor = body["next_cursor"]

    assert sorted(out) == sorted(f"public/{a}" for a in set(ids) - registered)
    assert len(out) == len(set(out)) == 7
    assert all(c == (10, 3) for c in counts)


async def test_cursor_past_end_returns_empty_terminal_page(
    admin_client: TestClient, web_context: Context
) -> None:
    await _seed(web_context, ["a.com", "b.com"])
    body = admin_client.get(
        "/catalog", params={"cursor": encode_catalog_cursor("public/zzz.com")}
    ).json()
    assert body["data"] == []
    assert body["has_more"] is False
    assert body["next_cursor"] is None


def test_invalid_cursor_returns_400(admin_client: TestClient) -> None:
    _sync_now(admin_client)
    r = admin_client.get("/catalog", params={"cursor": "not-a-valid-cursor!!!"})
    assert r.status_code == 400


def test_malformed_but_base64_cursor_returns_400(admin_client: TestClient) -> None:
    bad = base64.b64encode(json.dumps({"s": 0.1}).encode()).decode()
    assert admin_client.get("/catalog", params={"cursor": bad}).status_code == 400


def test_limit_out_of_bounds_rejected(admin_client: TestClient) -> None:
    _sync_now(admin_client)
    assert admin_client.get("/catalog", params={"limit": 0}).status_code == 422
    assert admin_client.get("/catalog", params={"limit": 201}).status_code == 422


def test_mutually_exclusive_filters_rejected(admin_client: TestClient) -> None:
    resp = admin_client.get("/catalog", params={"registered_only": True, "unregistered_only": True})
    assert resp.status_code == 422
    assert "mutually_exclusive" in resp.json()["type"]


def test_outdated_only_with_unregistered_only_rejected(admin_client: TestClient) -> None:
    resp = admin_client.get("/catalog", params={"outdated_only": True, "unregistered_only": True})
    assert resp.status_code == 422
    assert "mutually_exclusive" in resp.json()["type"]


# ── registered ───────────────────────────────────────────────────────────────


async def test_registered_follows_catalog_entry_id(
    admin_client: TestClient, web_context: Context
) -> None:
    """``registered`` reads the API's ``catalog_entry_id``, not its revision's URL."""
    _sync_now(admin_client)
    assert admin_client.get("/catalog/public/stripe.com").json()["registered"] is False

    # A revision of the entry's spec URL without a recorded entry id is not registered.
    async with web_context.registry_db.session() as session:
        api = Api(vendor="stripe-com", name="stripe-com", version="1.0", revision_count=1)
        session.add(api)
        await session.flush()
        session.add(
            ApiRevision(
                api_id=api.id,
                state="draft",
                spec_digest="sha256:url-only",
                source_type="url",
                source_url=_spec_url(_MANIFEST_BASE, "stripe.com"),
            )
        )
        await session.commit()
    assert admin_client.get("/catalog/public/stripe.com").json()["registered"] is False

    await _register(
        web_context,
        "public/stripe.com",
        vendor="stripe-com",
        name="stripe",
        spec_url="https://elsewhere.example/spec.json",
        catalog_api_id="stripe.com",
    )
    assert admin_client.get("/catalog/public/stripe.com").json()["registered"] is True
    listing = admin_client.get("/catalog").json()
    assert listing["registered_count"] == 1
    assert (
        admin_client.get("/catalog", params={"registered_only": True}).json()["data"][0]["entry_id"]
        == "public/stripe.com"
    )


# ── get + 404 ────────────────────────────────────────────────────────────────


def test_get_entry(admin_client: TestClient) -> None:
    _sync_now(admin_client)
    r = admin_client.get("/catalog/public/stripe.com")
    assert r.status_code == 200
    body = r.json()
    assert body["entry_id"] == "public/stripe.com"
    assert body["catalog_api_id"] == "stripe.com"
    assert body["_links"]["import"].endswith("/catalog/public/stripe.com:import")


def test_get_unknown_entry_404(admin_client: TestClient) -> None:
    _sync_now(admin_client)
    r = admin_client.get("/catalog/public/does-not-exist.com")
    assert r.status_code == 404
    assert r.json()["type"].endswith("catalog_entry_not_found")
    assert "did_you_mean" not in r.json()


def test_legacy_api_id_404_names_the_visible_winner(admin_client: TestClient) -> None:
    """An id in the old ``api_id`` form misses with a hint at its entry_id."""
    _sync_now(admin_client)
    r = admin_client.get("/catalog/stripe.com")
    assert r.status_code == 404
    assert r.json()["did_you_mean"] == "public/stripe.com"
    imp = admin_client.post("/catalog/stripe.com:import")
    assert imp.status_code == 404
    assert imp.json()["did_you_mean"] == "public/stripe.com"


@pytest.mark.parametrize(
    "path",
    [
        "/catalog/public/%2E%2E/public/stripe.com",
        "/catalog/public//stripe.com",
        "/catalog/public/%2e/stripe.com",
    ],
)
def test_traversal_shaped_ids_are_missing(admin_client: TestClient, path: str) -> None:
    _sync_now(admin_client)
    assert admin_client.get(path).status_code == 404


# ── preview ──────────────────────────────────────────────────────────────────


async def test_card_only_entry_answers_409_on_preview_and_import(
    web_config: AppConfig,
) -> None:
    """An entry its source lists without a spec is a permanent state, not a 502."""
    async with _deployment(web_config, [_public_source()]) as ctx:
        async with ctx.registry_db.transaction() as session:
            await session.execute(
                update(CatalogEntryRow)
                .where(CatalogEntryRow.entry_id == "public/slack.com")
                .values(spec_ref=None)
            )
        clear_snapshots()
        with _client(ctx, ["org:admin"]) as tc:
            assert tc.get("/catalog/public/slack.com").json()["has_spec"] is False
            for r in (
                tc.get("/catalog/public/slack.com/operations"),
                tc.post("/catalog/public/slack.com:import"),
            ):
                assert r.status_code == 409
                assert r.json()["type"].endswith("catalog_entry_has_no_spec")


def test_preview_operations(admin_client: TestClient) -> None:
    _sync_now(admin_client)
    r = admin_client.get("/catalog/public/stripe.com/operations")
    assert r.status_code == 200
    body = r.json()
    assert sorted(op["method"] for op in body["data"]) == ["GET", "POST"]
    assert body["info"]["title"] == "Stripe"


def test_preview_spec_gone_is_410(admin_client: TestClient) -> None:
    """The entry is listed but its source no longer serves the spec: 410, not 404."""
    _sync_now(admin_client)
    _SPEC_FAILURES[_spec_url(_MANIFEST_BASE, "stripe.com")] = (404, {})
    r = admin_client.get("/catalog/public/stripe.com/operations")
    assert r.status_code == 410
    assert r.json()["type"].endswith("catalog_spec_gone")


@pytest.mark.parametrize(
    ("status", "headers", "retry_after"),
    [(503, {"retry-after": "42"}, "42"), (429, {"retry-after": "7"}, "7"), (502, {}, "5")],
)
def test_preview_source_unavailable_is_503_with_retry_after(
    admin_client: TestClient, status: int, headers: dict[str, str], retry_after: str
) -> None:
    """A throttled or failing source is transient: 503 with the wait in ``Retry-After``."""
    _sync_now(admin_client)
    _SPEC_FAILURES[_spec_url(_MANIFEST_BASE, "stripe.com")] = (status, headers)
    r = admin_client.get("/catalog/public/stripe.com/operations")
    assert r.status_code == 503
    assert r.json()["type"].endswith("catalog_source_unavailable")
    assert r.headers["retry-after"] == retry_after


def test_preview_operations_q_filters_across_spec(admin_client: TestClient) -> None:
    _sync_now(admin_client)
    body = admin_client.get("/catalog/public/stripe.com/operations", params={"q": "create"}).json()
    assert body["total"] == 1
    assert body["data"][0]["method"] == "POST"


def test_preview_operations_q_is_case_insensitive(admin_client: TestClient) -> None:
    _sync_now(admin_client)
    body = admin_client.get(
        "/catalog/public/stripe.com/operations", params={"q": "LISTCHARGES"}
    ).json()
    assert body["total"] == 1
    assert body["data"][0]["operation_id"] == "listCharges"


def test_preview_operations_offset_limit_paginates(admin_client: TestClient) -> None:
    _sync_now(admin_client)
    path = "/catalog/public/stripe.com/operations"
    fbody = admin_client.get(path, params={"limit": 1, "offset": 0}).json()
    assert fbody["total"] == 2
    assert len(fbody["data"]) == 1
    assert fbody["truncated"] is True
    sbody = admin_client.get(path, params={"limit": 1, "offset": 1}).json()
    assert len(sbody["data"]) == 1
    assert sbody["truncated"] is False
    assert sbody["data"][0]["method"] != fbody["data"][0]["method"]


def test_preview_operations_q_bounds(admin_client: TestClient) -> None:
    _sync_now(admin_client)
    path = "/catalog/public/stripe.com/operations"
    assert admin_client.get(path, params={"q": "x" * 501}).status_code == 422
    at_max = admin_client.get(path, params={"q": "x" * 500})
    assert at_max.status_code == 200
    assert at_max.json()["total"] == 0


# ── import ───────────────────────────────────────────────────────────────────


async def test_import_entry_enqueues_job(admin_client: TestClient, web_context: Context) -> None:
    _sync_now(admin_client)
    r = admin_client.post("/catalog/public/stripe.com:import")
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    source = await _job_source(web_context, body["job_id"])
    assert source["catalog_entry_id"] == "public/stripe.com"
    assert source["catalog_api_id"] == "stripe.com"
    # The job resolves the spec through the entry's source: no URL in the payload.
    assert source["type"] == "catalog"
    assert "url" not in source


def test_import_unknown_entry_404(admin_client: TestClient) -> None:
    _sync_now(admin_client)
    assert admin_client.post("/catalog/public/nope.com:import").status_code == 404


def _coincap() -> Any:
    return build_catalog_entry(
        source_slug="public",
        external_id="coincap.io",
        catalog_api_id="coincap.io",
        hint="coincap.io",
        title=None,
        version=None,
    )


def test_to_import_source_threads_catalog_vendor_and_api_name(web_context: Context) -> None:
    """Catalog imports carry the entry's identity vendor and api_name, so specs that omit
    ``info.x-vendor``/``contact.name`` still resolve an api identifier."""
    identity = Identity(sub="usr_importer", email="u@test.local", permissions=[])
    assert CatalogService(web_context)._to_import_source(
        _coincap(), identity, vendor=None, name="coincap.io", switch_source=False
    ) == {
        "type": "catalog",
        "catalog_entry_id": "public/coincap.io",
        "catalog_api_id": "coincap.io",
        "origin": "catalog",
        "submitted_by": "usr_importer",
        "vendor": "coincap.io",
        "api_name": "coincap.io",
        "supersede_active": False,
        "host_change_approved": False,
        "switch_source": False,
    }


async def test_import_derives_api_name_from_sub_segment(
    admin_client: TestClient, web_context: Context
) -> None:
    """A ``domain/sub`` id seeds only the sub segment as api_name, never a vendor-doubled one."""
    await _seed(web_context, ["posthog.com/posthog-api"])
    r = admin_client.post("/catalog/public/posthog.com/posthog-api:import")
    assert r.status_code == 202
    source = await _job_source(web_context, r.json()["job_id"])
    assert source["api_name"] == "posthog-api"
    assert source["vendor"] == "posthog.com"
    assert source["catalog_api_id"] == "posthog.com/posthog-api"


async def test_reimport_targets_stored_identity_of_the_entry(
    admin_client: TestClient, web_context: Context
) -> None:
    """A re-import lands on the ``(vendor, name)`` of the API imported from the entry."""
    await _seed(web_context, ["finage.co.uk"])
    first = admin_client.post("/catalog/public/finage.co.uk:import").json()["job_id"]
    assert (await _job_source(web_context, first))["vendor"] == "finage.co.uk"

    await _register(
        web_context,
        "public/finage.co.uk",
        vendor="co-uk",
        name="finage-co-uk",
        spec_url=_spec_url(_MANIFEST_BASE, "finage.co.uk"),
        catalog_api_id="finage.co.uk",
    )
    again = admin_client.post("/catalog/public/finage.co.uk:import").json()["job_id"]
    source = await _job_source(web_context, again)
    assert (source["vendor"], source["api_name"]) == ("co-uk", "finage-co-uk")


# ── multiple sources: visibility, collapse, byte identity, source switch ─────

_STRIPE_MAPPING = {"source": "corp", "external_id": "stripe.com", "catalog_api_id": "stripe.com"}


@asynccontextmanager
async def _deployment(
    web_config: AppConfig, sources: Sequence[dict[str, Any]], **catalog: Any
) -> AsyncIterator[Context]:
    """A context over ``sources``, synced once."""
    async with Context(_config(web_config, sources, **catalog)) as ctx:
        await _sync_all(ctx)
        yield ctx


def _three_sources() -> list[dict[str, Any]]:
    _LISTINGS[_CORP_URL] = ["corp-billing.example"]
    _LISTINGS[_VAULT_URL] = ["vault-keys.example"]
    return [
        _public_source(),
        _corp_source(),
        _vault_source(readers={"permissions": ["credentials:write"], "actor_ids": ["agnt_vault"]}),
    ]


@pytest.mark.parametrize(
    ("sub", "permissions", "expected"),
    [
        ("usr_plain", [], {"public"}),
        ("agnt_plain", ["capabilities:read"], {"public"}),
        ("usr_org", ["catalog:org:read"], {"public", "corp"}),
        ("agnt_org", ["catalog:org:read"], {"public", "corp"}),
        # restricted needs the permission AND an ACL match
        ("usr_noacl", ["catalog:restricted:read"], {"public"}),
        ("usr_acl", ["catalog:restricted:read", "credentials:write"], {"public", "vault"}),
        ("agnt_vault", ["catalog:restricted:read"], {"public", "vault"}),
        # an ACL match without the permission admits nothing
        ("agnt_vault", ["credentials:write"], {"public"}),
        ("usr_admin", ["org:admin"], {"public", "corp", "vault"}),
    ],
)
async def test_visibility_matrix(
    web_config: AppConfig, sub: str, permissions: list[str], expected: set[str]
) -> None:
    async with _deployment(web_config, _three_sources()) as ctx:
        with _client(ctx, ["capabilities:read", *permissions], sub=sub) as tc:
            body = tc.get("/catalog").json()
            assert {e["source_slug"] for e in body["data"]} == expected
            assert body["catalog_total"] == len(body["data"])
            for hidden in {"corp/corp-billing.example", "vault/vault-keys.example"}:
                if hidden.split("/")[0] not in expected:
                    assert tc.get(f"/catalog/{hidden}").status_code == 404


async def test_unexpanded_admin_grant_sees_org_sources(web_config: AppConfig) -> None:
    """Visibility reads the effective permission set: a bare ``org:admin`` grant (as an
    API key carries it) answers byte-identically to the expanded set."""
    async with _deployment(web_config, _three_sources()) as ctx:
        expanded = ["org:admin", "catalog:org:read", "catalog:restricted:read", "catalog:sync"]
        with _client(ctx, ["org:admin"], sub="usr_admin") as bare:
            a = bare.get("/catalog").content
        with _client(ctx, expanded, sub="usr_admin") as full:
            b = full.get("/catalog").content
        assert a == b
        assert b"corp/corp-billing.example" in a


def _paths_for_byte_identity() -> list[tuple[str, str]]:
    return [
        ("GET", "/catalog"),
        ("GET", "/catalog?limit=1"),
        ("GET", "/catalog?q=stripe"),
        ("GET", "/catalog?include_alternates=true"),
        ("GET", "/catalog/public/stripe.com"),
        ("GET", "/catalog/corp/stripe.com"),
        ("GET", "/catalog/corp/never.example"),
        ("GET", "/catalog/corp/stripe.com/operations"),
        ("GET", "/catalog/corp/never.example/operations"),
        ("POST", "/catalog/corp/stripe.com:import"),
        ("POST", "/catalog/corp/never.example:import"),
        ("POST", "/catalog/corp/stripe.com:snooze"),
        ("POST", "/catalog/corp/never.example:snooze"),
        ("POST", "/catalog/corp/stripe.com:unsnooze"),
        ("POST", "/catalog/corp/never.example:unsnooze"),
        ("GET", "/catalog/stripe.com"),
    ]


#: MCP tool calls whose answers must not reveal a hidden source.
_MCP_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("search_catalog", {}),
    ("search_catalog", {"query": "stripe"}),
    ("search_catalog", {"limit": 1}),
    ("import_api", {"api_id": "corp/stripe.com"}),
    ("import_api", {"api_id": "corp/never.example"}),
    ("import_api", {"api_id": "corp/corp-only.example"}),
]

_BYTE_IDENTITY_PERMISSIONS = ["capabilities:read", "catalog:import", "events:write", "jobs:read"]


async def _mcp_answer(ctx: Context, tool: str, arguments: dict[str, Any]) -> tuple[int, bytes]:
    env = _mcp_env(ctx, _BYTE_IDENTITY_PERMISSIONS)
    try:
        result = await tools_mod.dispatch_tool_call(env, tool, arguments)
    except MCPError as exc:
        return exc.error.code, exc.error.message.encode()
    return 0, result.model_dump_json().encode()


async def _responses(ctx: Context) -> list[tuple[int, bytes]]:
    out: list[tuple[int, bytes]] = []
    with _client(ctx, _BYTE_IDENTITY_PERMISSIONS) as tc:
        for method, path in _paths_for_byte_identity():
            r = tc.request(method, path)
            out.append((r.status_code, r.content))
    for tool, arguments in _MCP_CALLS:
        out.append(await _mcp_answer(ctx, tool, arguments))
    return out


async def test_hidden_source_is_byte_identical_to_its_absence(
    web_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller without ``catalog:org:read`` cannot tell an ``org`` source exists."""
    frozen = datetime(2026, 1, 1, tzinfo=UTC)
    _LISTINGS[_CORP_URL] = ["stripe.com", "corp-only.example"]
    async with _deployment(web_config, [_public_source()]) as ctx:
        _pin_sync_times(monkeypatch, frozen)
        without_org = await _responses(ctx)
    await _truncate_all(web_config)
    async with _deployment(
        web_config,
        [_public_source(), _corp_source()],
        catalog_api_id_mappings=[_STRIPE_MAPPING],
    ) as ctx:
        _pin_sync_times(monkeypatch, frozen)
        with_org = await _responses(ctx)
    assert [s for s, _ in with_org] == [s for s, _ in without_org]
    assert with_org == without_org
    paths = [p for _, p in _paths_for_byte_identity()]
    statuses = dict(zip(paths, [s for s, _ in with_org[: len(paths)]], strict=True))
    assert statuses["/catalog/corp/stripe.com"] == 404
    assert statuses["/catalog/corp/stripe.com:import"] == 404
    assert statuses["/catalog/corp/stripe.com:unsnooze"] == 404
    mcp = with_org[len(paths) :]
    assert [code for code, _ in mcp[3:]] == [mcp_types.INVALID_PARAMS] * 3
    assert b"corp" not in b"".join(body for _, body in mcp[:3])


@pytest.mark.parametrize("failure", [(404, {}), (503, {"retry-after": "9"})])
async def test_hidden_entry_with_failing_spec_is_byte_identical_to_its_absence(
    web_config: AppConfig, monkeypatch: pytest.MonkeyPatch, failure: tuple[int, dict[str, str]]
) -> None:
    """A hidden entry's source failing its spec fetch still answers the plain 404.

    Visibility is checked before any fetch, so neither ``410 catalog_spec_gone``
    nor ``503`` can reveal that a hidden source lists the entry.
    """
    frozen = datetime(2026, 1, 1, tzinfo=UTC)
    paths = ["/catalog/corp/stripe.com/operations", "/catalog/corp/corp-only.example/operations"]

    async def _answers(ctx: Context) -> list[tuple[int, bytes, str | None]]:
        with _client(ctx, _BYTE_IDENTITY_PERMISSIONS) as tc:
            out = []
            for path in paths:
                r = tc.get(path)
                out.append((r.status_code, r.content, r.headers.get("retry-after")))
            return out

    _LISTINGS[_CORP_URL] = ["stripe.com", "corp-only.example"]
    async with _deployment(web_config, [_public_source()]) as ctx:
        _pin_sync_times(monkeypatch, frozen)
        without_org = await _answers(ctx)
    await _truncate_all(web_config)
    for api_id in ("stripe.com", "corp-only.example"):
        _SPEC_FAILURES[_spec_url(_CORP_BASE, api_id)] = failure
    async with _deployment(
        web_config,
        [_public_source(), _corp_source()],
        catalog_api_id_mappings=[_STRIPE_MAPPING],
    ) as ctx:
        _pin_sync_times(monkeypatch, frozen)
        with_org = await _answers(ctx)
        # The failure is real: a caller who can see the source gets it.
        with _client(ctx, ["org:admin"]) as admin:
            seen = admin.get("/catalog/corp/corp-only.example/operations").status_code
    assert seen == (410 if failure[0] == 404 else 503)
    assert with_org == without_org
    assert [status for status, _, _ in with_org] == [404, 404]
    assert all(retry_after is None for _, _, retry_after in with_org)


def _pin_sync_times(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    """Freeze the clocks the listing ages and the MCP stamp read, so two deployments
    compare byte-for-byte."""
    monkeypatch.setattr(service_module, "utcnow", lambda: now)
    stamp = mcp_envelopes.instance_stamp

    def _frozen_stamp(ctx: Context) -> dict[str, Any]:
        return {**stamp(ctx), "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}

    monkeypatch.setattr(mcp_envelopes, "instance_stamp", _frozen_stamp)


async def _truncate_all(web_config: AppConfig) -> None:
    async with Context(web_config) as ctx:
        await _truncate(ctx)


async def test_hidden_cursor_walk_survives_a_hidden_source_sync(web_config: AppConfig) -> None:
    """A cursor issued before a hidden source syncs keeps paging (no ``400``)."""
    _LISTINGS[_MANIFEST_URL] = [f"api-{i:02d}.com" for i in range(6)]
    _LISTINGS[_CORP_URL] = ["corp-a.example"]
    async with _deployment(web_config, [_public_source(), _corp_source()]) as ctx:
        with _client(ctx, ["capabilities:read"]) as tc:
            first = tc.get("/catalog", params={"limit": 2}).json()
            _LISTINGS[_CORP_URL] = ["corp-a.example", "corp-b.example"]
            await _sync_all(ctx)
            nxt = tc.get("/catalog", params={"limit": 2, "cursor": first["next_cursor"]})
            assert nxt.status_code == 200
            assert _ids(nxt.json()) == ["public/api-02.com", "public/api-03.com"]


async def test_collapse_winner_follows_a_finished_import(web_config: AppConfig) -> None:
    """Registered beats precedence on the very next list, with no sync in between."""
    _LISTINGS[_CORP_URL] = ["stripe.com"]
    async with _deployment(
        web_config,
        [_public_source(), _corp_source()],
        catalog_api_id_mappings=[_STRIPE_MAPPING],
    ) as ctx:
        with _client(ctx, ["capabilities:read", "catalog:org:read"]) as tc:
            body = tc.get("/catalog", params={"q": "stripe"}).json()
            [winner] = body["data"]
            assert winner["entry_id"] == "corp/stripe.com"
            assert winner["alternates"] == 1
            assert winner["_links"]["alternates"][0].endswith("/catalog/public/stripe.com")
            # The collapsed-out alternate is still addressable.
            assert tc.get("/catalog/public/stripe.com").status_code == 200
            unc = tc.get("/catalog", params={"q": "stripe", "include_alternates": True}).json()
            assert _ids(unc) == ["corp/stripe.com", "public/stripe.com"]
            assert all(e["alternates"] == 0 for e in unc["data"])

            await _register(
                ctx,
                "public/stripe.com",
                vendor="stripe-com",
                name="stripe-com",
                spec_url=_spec_url(_MANIFEST_BASE, "stripe.com"),
                catalog_api_id="stripe.com",
            )
            after = tc.get("/catalog", params={"q": "stripe"}).json()
            [winner] = after["data"]
            assert winner["entry_id"] == "public/stripe.com"
            assert winner["registered"] is True
            assert winner["alternates"] == 1
            assert after["registered_count"] == 1
            reg = tc.get("/catalog", params={"registered_only": True}).json()
            assert _ids(reg) == ["public/stripe.com"]

        # A caller without the org source sees no alternate at all.
        with _client(ctx, ["capabilities:read"]) as public_only:
            [only] = public_only.get("/catalog", params={"q": "stripe"}).json()["data"]
            assert only["entry_id"] == "public/stripe.com"
            assert only["alternates"] == 0
            assert only["_links"]["alternates"] == []


async def test_registered_alternates_are_counted_and_listed(web_config: AppConfig) -> None:
    """Two registered entries for one ``catalog_api_id`` both count and both list."""
    _LISTINGS[_CORP_URL] = ["stripe.com"]
    async with _deployment(
        web_config,
        [_public_source(), _corp_source()],
        catalog_api_id_mappings=[_STRIPE_MAPPING],
    ) as ctx:
        both = ("public/stripe.com", "corp/stripe.com")
        for entry_id in both:
            source = entry_id.split("/")[0]
            api_id = await _register(
                ctx,
                entry_id,
                vendor="stripe-com",
                name=f"stripe-{source}",
                spec_url=_spec_url(
                    _MANIFEST_BASE if source == "public" else _CORP_BASE, "stripe.com"
                ),
                catalog_api_id="stripe.com",
            )
            await _outdate(ctx, api_id, entry_id)
        with _client(ctx, ["capabilities:read", "catalog:org:read"]) as tc:
            body = tc.get("/catalog").json()
            assert [e["entry_id"] for e in body["data"] if "stripe" in e["entry_id"]] == [
                "corp/stripe.com"
            ]
            assert (body["registered_count"], body["outdated_count"]) == (2, 2)
            for flag in ("registered_only", "outdated_only"):
                listed = tc.get("/catalog", params={flag: True}).json()
                assert sorted(_ids(listed)) == sorted(both)
                searched = tc.get("/catalog", params={flag: True, "q": "stripe"}).json()
                assert sorted(_ids(searched)) == sorted(both)
        # A public-only reader sees just the public one.
        with _client(ctx, ["capabilities:read"]) as tc:
            body = tc.get("/catalog", params={"registered_only": True}).json()
            assert _ids(body) == ["public/stripe.com"]
            assert body["registered_count"] == 1


async def _serve(ctx: Context, api_id: uuid.UUID) -> None:
    """Promote the API's only revision so it serves (``current_revision_id`` set)."""
    async with ctx.registry_db.transaction() as session:
        revision = (
            await session.execute(select(ApiRevision).where(ApiRevision.api_id == api_id))
        ).scalar_one()
        revision.state = "live"
        api = await session.get(Api, api_id)
        assert api is not None
        api.current_revision_id = revision.id


async def test_no_entry_adopts_an_api_that_records_none(
    web_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An API that records no entry is never resolved by spec URL: neither the
    legacy entry nor another source's entry listing the same URL can snooze it."""
    listing = _manifest

    def _corp_lists_the_public_spec(url: str) -> dict[str, Any] | None:
        if url == _CORP_URL:
            return {"include": [{"url": _include_url(_MANIFEST_BASE, "stripe.com")}]}
        return listing(url)

    monkeypatch.setattr(sys.modules[__name__], "_manifest", _corp_lists_the_public_spec)
    _LISTINGS[_CORP_URL] = []
    async with _deployment(web_config, [_public_source(), _corp_source()]) as ctx:
        spec_url = _spec_url(_MANIFEST_BASE, "stripe.com")
        api_id = await _register(
            ctx,
            None,
            vendor="stripe-com",
            name="stripe",
            spec_url=spec_url,
            catalog_api_id="stripe.com",
        )
        await _serve(ctx, api_id)
        await _outdate(ctx, api_id, "public/stripe.com")
        with _client(ctx, ["org:admin", "events:write"]) as tc:
            assert tc.post("/catalog/corp/stripe.com:snooze").status_code == 404
            assert tc.post("/catalog/public/stripe.com:snooze").status_code == 404


async def test_update_and_mute_act_on_the_apis_own_entry(web_config: AppConfig) -> None:
    """Two registered entries share ``stripe.com``: each API's response names its own
    entry, and re-import / snooze through that entry act on that API only."""
    _LISTINGS[_CORP_URL] = ["stripe.com"]
    async with _deployment(
        web_config,
        [_public_source(), _corp_source()],
        catalog_api_id_mappings=[_STRIPE_MAPPING],
    ) as ctx:
        ids: dict[str, uuid.UUID] = {}
        for source, base in (("public", _MANIFEST_BASE), ("corp", _CORP_BASE)):
            spec_url = _spec_url(base, "stripe.com")
            ids[source] = await _register(
                ctx,
                f"{source}/stripe.com",
                vendor="stripe-com",
                name=f"stripe-{source}",
                spec_url=spec_url,
                catalog_api_id="stripe.com",
            )
            await _serve(ctx, ids[source])
            await _outdate(ctx, ids[source], spec_url)
        permissions = ["org:admin", "events:write"]
        with _client(ctx, permissions) as tc:
            apis = {a["api"]["name"]: a for a in tc.get("/apis").json()["data"]}
            assert apis["stripe-public"]["catalog_entry_id"] == "public/stripe.com"
            assert apis["stripe-corp"]["catalog_entry_id"] == "corp/stripe.com"
            detail = tc.get("/apis/stripe-com/stripe-public/1.0").json()
            assert detail["catalog_entry_id"] == "public/stripe.com"

            # The collapse winner is corp; acting on public's entry still hits public.
            r = tc.post("/catalog/public/stripe.com:import")
            assert r.status_code == 202
            job = await _job_source(ctx, r.json()["job_id"])
            assert (job["vendor"], job["api_name"]) == ("stripe-com", "stripe-public")
            assert tc.post("/catalog/public/stripe.com:snooze").status_code == 204
        async with ctx.registry_db.session() as session:
            checks = {
                c.local_api_id: c
                for c in (await session.execute(select(CatalogUpdateCheck))).scalars()
            }
        assert checks[ids["public"]].snoozed_digest is not None
        assert checks[ids["corp"]].snoozed_digest is None

        # A caller who can't read corp gets no entry id for the corp API.
        with _client(ctx, ["apis:read"]) as tc:
            apis = {a["api"]["name"]: a for a in tc.get("/apis").json()["data"]}
        assert apis["stripe-public"]["catalog_entry_id"] == "public/stripe.com"
        assert apis["stripe-corp"]["catalog_entry_id"] is None


async def test_api_without_a_recorded_entry_names_its_legacy_entry(
    web_config: AppConfig,
) -> None:
    async with _deployment(web_config, [_public_source()]) as ctx:
        await _unrecorded_stripe(ctx)
        with _client(ctx, ["apis:read"]) as tc:
            [api] = tc.get("/apis").json()["data"]
        assert api["catalog_entry_id"] == "public/stripe.com"


async def test_outdated_only_lists_a_newly_registered_winner(web_config: AppConfig) -> None:
    """An import that makes the lower-precedence entry the winner lists it as outdated."""
    _LISTINGS[_CORP_URL] = ["stripe.com"]
    async with _deployment(
        web_config,
        [_public_source(), _corp_source()],
        catalog_api_id_mappings=[_STRIPE_MAPPING],
    ) as ctx:
        with _client(ctx, ["capabilities:read", "catalog:org:read"]) as tc:
            assert tc.get("/catalog", params={"outdated_only": True}).json()["data"] == []
            spec_url = _spec_url(_MANIFEST_BASE, "stripe.com")
            api_id = await _register(
                ctx,
                "public/stripe.com",
                vendor="stripe-com",
                name="stripe-com",
                spec_url=spec_url,
                catalog_api_id="stripe.com",
            )
            await _outdate(ctx, api_id, "public/stripe.com")
            body = tc.get("/catalog", params={"outdated_only": True}).json()
            assert _ids(body) == ["public/stripe.com"]
            assert body["outdated_count"] == 1
            [entry] = body["data"]
            assert (entry["registered"], entry["update_available"]) == (True, True)


async def test_unmapped_org_entry_is_namespaced_and_never_collapses(
    web_config: AppConfig,
) -> None:
    _LISTINGS[_CORP_URL] = ["stripe.com"]
    async with _deployment(web_config, [_public_source(), _corp_source()]) as ctx:
        with _client(ctx, ["capabilities:read", "catalog:org:read"]) as tc:
            body = tc.get("/catalog", params={"include_alternates": False}).json()
            corp = next(e for e in body["data"] if e["source_slug"] == "corp")
            assert corp["catalog_api_id"] == "corp~stripe.com"
            assert corp["vendor"] == "corp~stripe.com"
            assert corp["display_vendor"] == "stripe.com"
            assert corp["alternates"] == 0


async def _conflict(ctx: Context) -> None:
    """``corp/stripe.com`` (mapped to ``stripe.com``) already backs a registered API."""
    await _register(
        ctx,
        "corp/stripe.com",
        vendor="stripe-com",
        name="stripe-com",
        spec_url=_spec_url(_CORP_BASE, "stripe.com"),
        catalog_api_id="stripe.com",
    )


@pytest.mark.parametrize(
    ("permissions", "switch", "status", "lands_on_existing"),
    [
        (["org:admin"], False, 202, False),
        (["org:admin"], True, 202, True),
        (["catalog:import", "catalog:org:read"], False, 202, False),
        (["catalog:import", "catalog:org:read"], True, 403, None),
    ],
)
async def test_source_switch_is_an_explicit_credentials_write_opt_in(
    web_config: AppConfig,
    permissions: list[str],
    switch: bool,
    status: int,
    lands_on_existing: bool | None,
) -> None:
    _LISTINGS[_CORP_URL] = ["stripe.com"]
    async with _deployment(
        web_config,
        [_public_source(), _corp_source()],
        catalog_api_id_mappings=[_STRIPE_MAPPING],
    ) as ctx:
        await _conflict(ctx)
        with _client(ctx, permissions) as tc:
            r = tc.post(
                "/catalog/public/stripe.com:import", params={"switch_source": str(switch).lower()}
            )
        assert r.status_code == status
        if lands_on_existing is None:
            assert r.json()["type"].endswith("source_switch_forbidden")
            return
        source = await _job_source(ctx, r.json()["job_id"])
        assert source["catalog_entry_id"] == "public/stripe.com"
        if lands_on_existing:
            assert (source["vendor"], source["api_name"]) == ("stripe-com", "stripe-com")
        else:
            # The distinct-name path: the existing API is left alone.
            assert source["api_name"] == "public~stripe.com"


async def test_mcp_import_never_switches_source(web_config: AppConfig) -> None:
    """``import_api`` has no switch opt-in: even an ``org:admin`` agent importing an
    entry whose id another entry's API holds gets the distinct-name path."""
    _LISTINGS[_CORP_URL] = ["stripe.com"]
    async with _deployment(
        web_config,
        [_public_source(), _corp_source()],
        catalog_api_id_mappings=[_STRIPE_MAPPING],
    ) as ctx:
        await _conflict(ctx)
        job_id = await tools_mod._file_import(
            _mcp_env(ctx, ["org:admin", "credentials:write"]), "public/stripe.com"
        )
        source = await _job_source(ctx, job_id)
        assert source["catalog_entry_id"] == "public/stripe.com"
        assert source["api_name"] == "public~stripe.com"
        async with ctx.registry_db.session() as session:
            api = (await session.execute(select(Api))).scalar_one()
        assert (api.name, api.catalog_entry_id) == ("stripe-com", "corp/stripe.com")


async def _unrecorded_stripe(ctx: Context) -> None:
    """A ``stripe.com`` API imported before entry ids were recorded (legacy source)."""
    await _register(
        ctx,
        None,
        vendor="stripe-com",
        name="stripe-com",
        spec_url=_spec_url(_MANIFEST_BASE, "stripe.com"),
        catalog_api_id="stripe.com",
    )


def _mcp_env(ctx: Context, permissions: Sequence[str]) -> CallEnv:
    return CallEnv(
        ctx=ctx,
        identity=Identity(
            sub="agnt_mcp",
            email="agnt_mcp@test.local",
            permissions=list(permissions),
            actor_type=ActorType.AGENT,
        ),
        credential="jak_test",
        base_url="http://testserver",
        session_id=None,
    )


@pytest.mark.parametrize("surface", ["rest", "mcp"])
async def test_unrecorded_holder_is_not_adopted_by_another_source(
    web_config: AppConfig, surface: str
) -> None:
    """An API with no recorded entry came from the legacy source: a mapped org entry
    sharing its ``catalog_api_id`` takes a distinct name instead of switching it."""
    _LISTINGS[_CORP_URL] = ["stripe.com"]
    async with _deployment(
        web_config,
        [_public_source(), _corp_source()],
        catalog_api_id_mappings=[_STRIPE_MAPPING],
    ) as ctx:
        await _unrecorded_stripe(ctx)
        permissions = ["catalog:import", "catalog:org:read"]
        if surface == "rest":
            with _client(ctx, permissions) as tc:
                r = tc.post("/catalog/corp/stripe.com:import")
            assert r.status_code == 202
            job_id = r.json()["job_id"]
        else:
            job_id = await tools_mod._file_import(_mcp_env(ctx, permissions), "corp/stripe.com")
        source = await _job_source(ctx, job_id)
        assert source["catalog_entry_id"] == "corp/stripe.com"
        assert source["api_name"] == "corp~stripe.com"
        async with ctx.registry_db.session() as session:
            api = (await session.execute(select(Api))).scalar_one()
        assert (api.vendor, api.name, api.catalog_entry_id) == ("stripe-com", "stripe-com", None)


async def test_unrecorded_holder_is_adopted_by_the_legacy_source(web_config: AppConfig) -> None:
    _LISTINGS[_CORP_URL] = ["stripe.com"]
    async with _deployment(
        web_config,
        [_public_source(), _corp_source()],
        catalog_api_id_mappings=[_STRIPE_MAPPING],
    ) as ctx:
        await _unrecorded_stripe(ctx)
        with _client(ctx, ["catalog:import"]) as tc:
            r = tc.post("/catalog/public/stripe.com:import")
        assert r.status_code == 202
        source = await _job_source(ctx, r.json()["job_id"])
        assert (source["vendor"], source["api_name"]) == ("stripe-com", "stripe-com")


async def test_unrecorded_holder_switches_only_with_credentials_write(
    web_config: AppConfig,
) -> None:
    _LISTINGS[_CORP_URL] = ["stripe.com"]
    async with _deployment(
        web_config,
        [_public_source(), _corp_source()],
        catalog_api_id_mappings=[_STRIPE_MAPPING],
    ) as ctx:
        await _unrecorded_stripe(ctx)
        url = "/catalog/corp/stripe.com:import"
        with _client(ctx, ["catalog:import", "catalog:org:read"]) as tc:
            denied = tc.post(url, params={"switch_source": "true"})
        assert denied.status_code == 403
        with _client(ctx, ["org:admin"]) as tc:
            switched = tc.post(url, params={"switch_source": "true"})
        assert switched.status_code == 202
        source = await _job_source(ctx, switched.json()["job_id"])
        assert (source["vendor"], source["api_name"]) == ("stripe-com", "stripe-com")


async def test_switch_source_without_a_conflict_is_a_no_op(admin_client: TestClient) -> None:
    _sync_now(admin_client)
    plain = admin_client.post("/catalog/public/stripe.com:import")
    switched = admin_client.post(
        "/catalog/public/stripe.com:import", params={"switch_source": "true"}
    )
    assert plain.status_code == switched.status_code == 202


# ── resolve + auto-import (vendor connect hand-off) ──────────────────────────


async def test_resolve_picks_the_winner_the_caller_can_see(web_config: AppConfig) -> None:
    _LISTINGS[_CORP_URL] = ["stripe.com"]
    async with _deployment(
        web_config,
        [_public_source(), _corp_source()],
        catalog_api_id_mappings=[_STRIPE_MAPPING],
    ) as ctx:
        svc = CatalogService(ctx)
        public = await svc.resolve("stripe.com", permissions=frozenset())
        assert public.entry_id == "public/stripe.com"
        org = await svc.resolve("stripe.com", permissions=frozenset({"catalog:org:read"}))
        assert org.entry_id == "corp/stripe.com"


async def test_auto_import_enqueues_the_public_entry(web_context: Context) -> None:
    await _seed(web_context, ["stripe.com"])
    job_id = await InProcessCatalogAutoImporter(web_context).ensure_imported(
        catalog_api_id="stripe.com", initiator_actor_id="usr_1"
    )
    assert job_id is not None
    assert (await _job_source(web_context, job_id))["catalog_entry_id"] == "public/stripe.com"


async def test_auto_import_picks_the_org_entry_for_an_org_reader(web_config: AppConfig) -> None:
    _LISTINGS[_CORP_URL] = ["stripe.com"]
    async with _deployment(
        web_config,
        [_public_source(), _corp_source()],
        catalog_api_id_mappings=[_STRIPE_MAPPING],
    ) as ctx:
        importer = InProcessCatalogAutoImporter(ctx)
        job = await importer.ensure_imported(
            catalog_api_id="stripe.com",
            initiator_actor_id="usr_1",
            initiator_permissions=frozenset({"catalog:org:read"}),
        )
        assert job is not None
        assert (await _job_source(ctx, job))["catalog_entry_id"] == "corp/stripe.com"


async def test_auto_import_noop_when_already_registered(web_context: Context) -> None:
    await _seed(web_context, ["stripe.com"])
    await _register(
        web_context,
        "public/stripe.com",
        vendor="stripe-com",
        name="stripe-com",
        spec_url=_spec_url(_MANIFEST_BASE, "stripe.com"),
        catalog_api_id="stripe.com",
    )
    job_id = await InProcessCatalogAutoImporter(web_context).ensure_imported(
        catalog_api_id="stripe.com", initiator_actor_id="usr_1"
    )
    assert job_id is None


async def test_public_winner_reads_the_snapshot_without_syncing_or_flagging(
    web_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _LISTINGS[_CORP_URL] = ["stripe.com"]
    async with _deployment(
        web_config,
        [_public_source(), _corp_source()],
        catalog_api_id_mappings=[_STRIPE_MAPPING],
    ) as ctx:
        async with ctx.registry_db.transaction() as session:
            await session.execute(
                text(
                    "UPDATE registry.catalog_sources SET last_sync_at = last_sync_at"
                    " - interval '30 days'"
                )
            )
        calls: list[str] = []

        async def _no_sync(self: CatalogSourceSync, slug: str, **_: Any) -> Any:
            calls.append(f"sync:{slug}")
            raise AssertionError("public_winner must not sync")

        async def _no_flag(_session: Any, slugs: Any) -> int:
            calls.append(f"flag:{slugs}")
            return 0

        monkeypatch.setattr(CatalogSourceSync, "sync_source", _no_sync)
        monkeypatch.setattr(CatalogSourceRepository, "mark_sync_wanted", staticmethod(_no_flag))
        svc = CatalogService(ctx)
        assert await svc.public_winner("stripe.com") == "public/stripe.com"
        assert await svc.public_winner("never.example") is None
        assert calls == []
        # A regular read of the same catalog does flag the stale sources.
        await svc.list_all(permissions=frozenset())
        assert calls and calls[0].startswith("flag:")


# ── visibility through the real credential verifiers ─────────────────────────

_PARITY_AGENT = "agnt_vault"
_PARITY_OWNER = "usr_catalog_parity_owner"


async def _clear_parity_agent(ctx: Context) -> None:
    async with ctx.admin_db.transaction() as session:
        for table, column in (
            ("access_tokens", "actor_id"),
            ("agent_credentials", "agent_id"),
            ("actor_permission_grants", "actor_id"),
            ("agents", "id"),
        ):
            await session.execute(
                text(f"DELETE FROM {table} WHERE {column} = :id"),
                {"id": _PARITY_AGENT},
            )
        await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": _PARITY_OWNER})


async def _seed_parity_agent(ctx: Context, grants: Sequence[str]) -> tuple[str, str]:
    """An active agent holding ``grants``, with a ``jak_`` key and an ``at_`` token."""
    api_key = f"{AGENT_API_KEY_PREFIX}{secrets.token_hex(16)}"
    access_token = f"at_{secrets.token_hex(16)}"
    await _clear_parity_agent(ctx)
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name) "
                "VALUES (:id, 'catalog-parity@test.local', 'Cat', 'P')"
            ),
            {"id": _PARITY_OWNER},
        )
        await session.execute(
            text(
                "INSERT INTO agents (id, name, owner_id, registered_by, status, created_by) "
                "VALUES (:id, 'parity-bot', :owner, 'system:test', 'active', 'system:test')"
            ),
            {"id": _PARITY_AGENT, "owner": _PARITY_OWNER},
        )
        await session.execute(
            text(
                "INSERT INTO agent_credentials (id, agent_id, api_key_hash, created_by) "
                "VALUES (:id, :agent_id, :hash, 'system:test')"
            ),
            {
                "id": f"agc_{_PARITY_AGENT}",
                "agent_id": _PARITY_AGENT,
                "hash": hashlib.sha256(api_key.encode()).hexdigest(),
            },
        )
        for i, permission in enumerate(grants):
            await session.execute(
                text(
                    "INSERT INTO actor_permission_grants "
                    "(id, actor_id, actor_type, permission, created_by) "
                    "VALUES (:id, :actor_id, 'agent', :permission, 'system:test')"
                ),
                {"id": f"asg_parity_{i}", "actor_id": _PARITY_AGENT, "permission": permission},
            )
        await AccessTokenRepository.create(
            session,
            token_hash=_hash_token(access_token),
            actor_id=_PARITY_AGENT,
            actor_type="agent",
            scopes=list(grants),
            token_family_id="tfam_catalog_parity",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            created_by="system:test",
        )
    return api_key, access_token


_SYNC_AGE = re.compile(rb'"(oldest_source_sync_age_seconds)":\d+')


@pytest.mark.parametrize(
    ("grants", "expected"),
    [
        (["org:admin"], {"public", "corp", "vault"}),
        (["capabilities:read", "catalog:org:read"], {"public", "corp"}),
        (["capabilities:read", "catalog:restricted:read"], {"public", "vault"}),
        (["capabilities:read"], {"public"}),
    ],
)
async def test_api_key_and_access_token_see_the_same_catalog(
    web_config: AppConfig, grants: list[str], expected: set[str]
) -> None:
    """The visibility set comes from the verifier's effective permissions, so an agent's
    ``jak_`` key and its ``at_`` token answer every catalog read byte-for-byte alike."""
    async with _deployment(web_config, _three_sources()) as ctx:
        api_key, access_token = await _seed_parity_agent(ctx, grants)
        try:
            app = create_app(ctx)
            app.router.lifespan_context = noop_lifespan
            app.state.verify_token = make_superset_verifier(ctx)
            answers: dict[str, list[tuple[int, bytes]]] = {}
            with TestClient(app) as tc:
                for name, credential in (("key", api_key), ("token", access_token)):
                    headers = {"Authorization": f"Bearer {credential}"}
                    answers[name] = [
                        # The sync age is wall-clock seconds, so it can tick between
                        # the two credentials' reads; every other byte must match.
                        (r.status_code, _SYNC_AGE.sub(b'"\\1":0', r.content))
                        for r in (
                            tc.get(path, headers=headers)
                            for path in (
                                "/catalog",
                                "/catalog?include_alternates=true",
                                "/catalog/corp/corp-billing.example",
                                "/catalog/vault/vault-keys.example",
                            )
                        )
                    ]
            assert answers["key"] == answers["token"]
            status, body = answers["key"][0]
            assert status == 200
            assert {e["source_slug"] for e in json.loads(body)["data"]} == expected
        finally:
            await _clear_parity_agent(ctx)


# ── identity vendor: credential coverage (§11.1 #7) ──────────────────────────

_STRIPE_WIDE = canonical_credential_scope(vendor="stripe.com", name=None, version=None)


async def _import_and_ingest(ctx: Context, entry_id: str) -> Api:
    """Import ``entry_id`` over REST, run the queued job, return the API it landed on."""
    with _client(ctx, ["org:admin"]) as tc:
        r = tc.post(f"/catalog/{entry_id}:import")
    assert r.status_code == 202, r.text
    source = await _job_source(ctx, r.json()["job_id"])
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient", httpx.AsyncClient):
        await ImportHandler(ctx).execute(
            job_id=generate_ksuid("job"),
            db=None,
            payload={"sources": [source]},
            created_by="t",
        )
    async with ctx.registry_db.session() as session:
        return (
            await session.execute(select(Api).where(Api.catalog_entry_id == entry_id))
        ).scalar_one()


async def test_vendor_wide_credential_never_covers_a_namespaced_entry(
    web_config: AppConfig,
) -> None:
    """An internal source's ``stripe.com``, ``stripe.com/payments`` and ``api.stripe.com``
    resolve to namespaced ids, and none of their imported APIs is covered by a
    vendor-wide Stripe credential."""
    _LISTINGS[_MANIFEST_URL] = ["stripe.com"]
    _LISTINGS[_CORP_URL] = ["stripe.com", "stripe.com/payments", "api.stripe.com"]
    async with _deployment(web_config, [_public_source(), _corp_source()]) as ctx:
        with _client(ctx, ["capabilities:read", "catalog:org:read"]) as tc:
            corp = {
                e["entry_id"]: e
                for e in tc.get("/catalog", params={"include_alternates": True}).json()["data"]
                if e["source_slug"] == "corp"
            }
        assert {e["catalog_api_id"] for e in corp.values()} == {
            "corp~stripe.com",
            "corp~stripe.com/payments",
            "corp~api.stripe.com",
        }
        for entry_id in sorted(corp):
            api = await _import_and_ingest(ctx, entry_id)
            assert api.vendor.startswith("corp-"), (entry_id, api.vendor)
            assert not credential_covers(
                _STRIPE_WIDE, vendor=api.vendor, name=api.name, version=api.version
            ), (entry_id, api.vendor, api.name)


async def test_a_mapped_entry_is_covered_by_the_vendor_wide_credential(
    web_config: AppConfig,
) -> None:
    _LISTINGS[_MANIFEST_URL] = ["stripe.com"]
    _LISTINGS[_CORP_URL] = ["stripe.com", "stripe.com/payments"]
    async with _deployment(
        web_config,
        [_public_source(), _corp_source()],
        catalog_api_id_mappings=[_STRIPE_MAPPING],
    ) as ctx:
        mapped = await _import_and_ingest(ctx, "corp/stripe.com")
        assert credential_covers(
            _STRIPE_WIDE, vendor=mapped.vendor, name=mapped.name, version=mapped.version
        )
        unmapped = await _import_and_ingest(ctx, "corp/stripe.com/payments")
        assert not credential_covers(
            _STRIPE_WIDE, vendor=unmapped.vendor, name=unmapped.name, version=unmapped.version
        )
