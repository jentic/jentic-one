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
import json
import sys
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

import jentic_one.mcp.tools as tools_mod
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.mcp.tools import CallEnv
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_entries import CatalogEntryRow
from jentic_one.registry.core.schema.catalog_snapshots import CatalogSnapshot
from jentic_one.registry.core.schema.catalog_sources import CatalogSourceRow
from jentic_one.registry.core.schema.catalog_sync_runs import CatalogSyncRun
from jentic_one.registry.core.schema.catalog_update_checks import CatalogUpdateCheck
from jentic_one.registry.services.catalog import service as service_module
from jentic_one.registry.services.catalog.entries import build_catalog_entry
from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.registry.services.catalog.snapshot import clear_snapshots
from jentic_one.registry.services.catalog.sources.sync import CatalogSourceSync
from jentic_one.registry.web.app import create_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig, CatalogConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
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
    _LISTINGS[_MANIFEST_URL] = ["stripe.com", "slack.com"]
    with patch("jentic_one.registry.services.catalog.fetch.httpx.AsyncClient", _factory):
        yield
    _LISTINGS.clear()


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


async def _outdate(ctx: Context, api_id: uuid.UUID, spec_url: str) -> None:
    """Record a notified upstream update the API's served revision has not adopted."""
    async with ctx.registry_db.transaction() as session:
        session.add(
            CatalogUpdateCheck(
                local_api_id=api_id, spec_url=spec_url, last_notified_digest="sha256:upstream"
            )
        )


async def _job_source(ctx: Context, job_id: str) -> dict[str, Any]:
    async with ctx.admin_db.session() as session:
        row = await session.get(Job, job_id)
        assert row is not None
        assert row.payload is not None
        return dict(row.payload["sources"][0])


async def _seed(ctx: Context, api_ids: list[str]) -> None:
    """Serve ``api_ids`` from the public manifest and sync it."""
    _LISTINGS[_MANIFEST_URL] = list(api_ids)
    await CatalogService(ctx).refresh()


# ── auth ─────────────────────────────────────────────────────────────────────


def test_list_catalog_requires_auth(unauthed_client: TestClient) -> None:
    assert unauthed_client.get("/catalog").status_code == 401


def test_refresh_rejects_non_admin_token(web_context: Context) -> None:
    """A valid identity without org:admin is rejected with 403 (not just presence)."""
    with _client(web_context, []) as tc:
        assert tc.post("/catalog:refresh").status_code == 403


def test_refresh_invalid_token_401(unauthed_client: TestClient) -> None:
    resp = unauthed_client.post(
        "/catalog:refresh", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401


def test_refresh_without_auth_401(unauthed_client: TestClient) -> None:
    assert unauthed_client.post("/catalog:refresh").status_code == 401


# ── refresh + list ───────────────────────────────────────────────────────────


def test_refresh_then_list(admin_client: TestClient) -> None:
    r = admin_client.post("/catalog:refresh")
    assert r.status_code == 200
    assert r.json() == {"count": 2, "status": "refreshed"}

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


async def test_refresh_syncs_into_catalog_entries(
    admin_client: TestClient, web_context: Context
) -> None:
    assert admin_client.post("/catalog:refresh").status_code == 200
    async with web_context.registry_db.session() as session:
        rows = (
            await session.execute(
                select(CatalogEntryRow.entry_id, CatalogEntryRow.catalog_api_id).where(
                    CatalogEntryRow.removed_at.is_(None)
                )
            )
        ).all()
    assert sorted(rows) == [("public/slack.com", "slack.com"), ("public/stripe.com", "stripe.com")]
    forced = admin_client.post("/catalog:refresh", params={"force": "true"})
    assert forced.status_code == 200
    assert forced.json() == {"count": 2, "status": "refreshed"}


async def test_reads_ignore_the_legacy_snapshot_table(
    admin_client: TestClient, web_context: Context
) -> None:
    """``catalog_snapshots`` no longer backs reads: a stale row there changes nothing."""
    assert admin_client.post("/catalog:refresh").status_code == 200
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


async def test_refresh_force_overrides_a_suspect_empty_listing(
    admin_client: TestClient, web_context: Context
) -> None:
    assert admin_client.post("/catalog:refresh").status_code == 200

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
    _LISTINGS[_MANIFEST_URL] = []
    assert admin_client.post("/catalog:refresh").status_code == 200
    assert await _live() == ["public/slack.com", "public/stripe.com"]
    assert await _status() == "suspect_empty"

    forced = admin_client.post("/catalog:refresh", params={"force": "true"})
    assert forced.status_code == 200
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
    admin_client.post("/catalog:refresh")
    body = admin_client.get("/catalog", params={"q": "stripe"}).json()
    assert _ids(body) == ["public/stripe.com"]


def test_search_never_matches_the_source_slug(admin_client: TestClient) -> None:
    """The scorer tokenises ``catalog_api_id``, never ``entry_id``."""
    admin_client.post("/catalog:refresh")
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
    admin_client.post("/catalog:refresh")
    r = admin_client.get("/catalog", params={"cursor": "not-a-valid-cursor!!!"})
    assert r.status_code == 400


def test_malformed_but_base64_cursor_returns_400(admin_client: TestClient) -> None:
    bad = base64.b64encode(json.dumps({"s": 0.1}).encode()).decode()
    assert admin_client.get("/catalog", params={"cursor": bad}).status_code == 400


def test_limit_out_of_bounds_rejected(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
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
    admin_client.post("/catalog:refresh")
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
    admin_client.post("/catalog:refresh")
    r = admin_client.get("/catalog/public/stripe.com")
    assert r.status_code == 200
    body = r.json()
    assert body["entry_id"] == "public/stripe.com"
    assert body["catalog_api_id"] == "stripe.com"
    assert body["_links"]["import"].endswith("/catalog/public/stripe.com:import")


def test_get_unknown_entry_404(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    r = admin_client.get("/catalog/public/does-not-exist.com")
    assert r.status_code == 404
    assert r.json()["type"].endswith("catalog_entry_not_found")
    assert "did_you_mean" not in r.json()


def test_legacy_api_id_404_names_the_visible_winner(admin_client: TestClient) -> None:
    """An id in the old ``api_id`` form misses with a hint at its entry_id."""
    admin_client.post("/catalog:refresh")
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
    admin_client.post("/catalog:refresh")
    assert admin_client.get(path).status_code == 404


# ── preview ──────────────────────────────────────────────────────────────────


def test_preview_operations(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    r = admin_client.get("/catalog/public/stripe.com/operations")
    assert r.status_code == 200
    body = r.json()
    assert sorted(op["method"] for op in body["data"]) == ["GET", "POST"]
    assert body["info"]["title"] == "Stripe"


def test_preview_operations_q_filters_across_spec(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    body = admin_client.get("/catalog/public/stripe.com/operations", params={"q": "create"}).json()
    assert body["total"] == 1
    assert body["data"][0]["method"] == "POST"


def test_preview_operations_q_is_case_insensitive(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    body = admin_client.get(
        "/catalog/public/stripe.com/operations", params={"q": "LISTCHARGES"}
    ).json()
    assert body["total"] == 1
    assert body["data"][0]["operation_id"] == "listCharges"


def test_preview_operations_offset_limit_paginates(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
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
    admin_client.post("/catalog:refresh")
    path = "/catalog/public/stripe.com/operations"
    assert admin_client.get(path, params={"q": "x" * 501}).status_code == 422
    at_max = admin_client.get(path, params={"q": "x" * 500})
    assert at_max.status_code == 200
    assert at_max.json()["total"] == 0


# ── import ───────────────────────────────────────────────────────────────────


async def test_import_entry_enqueues_job(admin_client: TestClient, web_context: Context) -> None:
    admin_client.post("/catalog:refresh")
    r = admin_client.post("/catalog/public/stripe.com:import")
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    source = await _job_source(web_context, body["job_id"])
    assert source["catalog_entry_id"] == "public/stripe.com"
    assert source["catalog_api_id"] == "stripe.com"
    assert source["url"] == _spec_url(_MANIFEST_BASE, "stripe.com")


def test_import_unknown_entry_404(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
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
        _coincap(),
        "https://example.test/coincap/openapi.json",
        identity,
        vendor=None,
        name="coincap.io",
    ) == {
        "type": "url",
        "url": "https://example.test/coincap/openapi.json",
        "origin": "catalog",
        "submitted_by": "usr_importer",
        "vendor": "coincap.io",
        "api_name": "coincap.io",
        "catalog_api_id": "coincap.io",
        "catalog_entry_id": "public/coincap.io",
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
        await CatalogService(ctx).refresh()
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
        ("GET", "/catalog/stripe.com"),
    ]


async def _responses(ctx: Context) -> list[tuple[int, bytes]]:
    out: list[tuple[int, bytes]] = []
    with _client(ctx, ["capabilities:read", "catalog:import", "events:write"]) as tc:
        for method, path in _paths_for_byte_identity():
            r = tc.request(method, path)
            out.append((r.status_code, r.content))
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
    statuses = dict(zip(paths, [s for s, _ in with_org], strict=True))
    assert statuses["/catalog/corp/stripe.com"] == 404
    assert statuses["/catalog/corp/stripe.com:import"] == 404


def _pin_sync_times(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    """Freeze the clock the listing ages against, so two deployments compare byte-for-byte."""
    monkeypatch.setattr(service_module, "utcnow", lambda: now)


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
            await CatalogService(ctx).refresh()
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
            await _outdate(
                ctx,
                api_id,
                _spec_url(_MANIFEST_BASE if source == "public" else _CORP_BASE, "stripe.com"),
            )
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
            await _outdate(ctx, api_id, spec_url)
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
    admin_client.post("/catalog:refresh")
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
