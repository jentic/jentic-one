"""Web tests for the registry catalog (Discover) endpoints.

The whole stack runs against the real Postgres test DB; only the upstream GitHub
fetch is mocked. We patch ``catalog.fetch.httpx.AsyncClient`` with a MockTransport
so ``:refresh`` ingests a tiny fake manifest and previews resolve a tiny fake spec,
mirroring the ingest fetch unit-test pattern (no real network).
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncGenerator, Iterator
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_snapshots import CatalogSnapshot
from jentic_one.registry.services.catalog.service import CatalogEntryView, CatalogService
from jentic_one.registry.web.app import create_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.pagination import encode_catalog_cursor
from jentic_one.shared.web.deps import resolve_identity
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration

_MANIFEST_BASE = "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/apis"
_MANIFEST_URL = f"{_MANIFEST_BASE}/openapi/apis.json"


def _manifest() -> dict[str, Any]:
    return {
        "include": [
            {"url": f"{_MANIFEST_BASE}/openapi/stripe.com/main/2024-01-01/apis.json"},
            {"url": f"{_MANIFEST_BASE}/openapi/slack.com/main/1.0/apis.json"},
        ]
    }


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
        if url == _MANIFEST_URL:
            body = _manifest()
        elif url.endswith("/openapi.json"):
            body = _spec()
        else:
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

    with patch("jentic_one.registry.services.catalog.fetch.httpx.AsyncClient", _factory):
        yield


@pytest.fixture(autouse=True)
async def _clean_catalog(web_context: Context) -> AsyncGenerator[None, None]:
    """Empty the catalog cache around each test for deterministic counts."""

    async def _truncate() -> None:
        async with web_context.registry_db.session() as session:
            await session.execute(delete(CatalogSnapshot))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


# ── auth ─────────────────────────────────────────────────────────────────────


def test_list_catalog_requires_auth(unauthed_client: TestClient) -> None:
    assert unauthed_client.get("/catalog").status_code == 401


def test_refresh_rejects_non_admin_token(web_context: Context) -> None:
    """A valid identity without org:admin is rejected with 403 (not just presence)."""
    app = create_app(web_context)
    app.router.lifespan_context = noop_lifespan

    async def _override(_: object = None) -> Identity:
        return Identity(sub="non-admin", email="u@test.local", permissions=[])

    app.dependency_overrides[resolve_identity] = _override
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as tc:
        assert tc.post("/catalog:refresh").status_code == 403


def test_refresh_invalid_token_401(unauthed_client: TestClient) -> None:
    # A garbage bearer token can't be verified by the live verifier -> 401.
    resp = unauthed_client.post(
        "/catalog:refresh", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401


def test_refresh_without_auth_401(unauthed_client: TestClient) -> None:
    assert unauthed_client.post("/catalog:refresh").status_code == 401


# ── refresh + list ─────────────────────────────────────────────────────────--


def test_refresh_then_list(admin_client: TestClient) -> None:
    r = admin_client.post("/catalog:refresh")
    assert r.status_code == 200
    assert r.json()["count"] == 2

    listing = admin_client.get("/catalog")
    assert listing.status_code == 200
    body = listing.json()
    ids = sorted(e["api_id"] for e in body["data"])
    assert ids == ["slack.com", "stripe.com"]
    assert body["catalog_total"] == 2
    assert body["manifest_age_seconds"] is not None


def test_list_search_filter(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    body = admin_client.get("/catalog", params={"q": "stripe"}).json()
    assert [e["api_id"] for e in body["data"]] == ["stripe.com"]


# ── pagination ─────────────────────────────────────────────────────────────--


async def _seed_snapshot(web_context: Context, api_ids: list[str]) -> None:
    entries = [
        {
            "api_id": a,
            "vendor": a,
            "path": f"apis/openapi/{a}",
            "spec_url": f"{_MANIFEST_BASE}/openapi/{a}/openapi.json",
            "github_url": f"https://github.com/jentic/jentic-public-apis/tree/main/{a}",
        }
        for a in api_ids
    ]
    async with web_context.registry_db.session() as session:
        session.add(
            CatalogSnapshot(source_url=_MANIFEST_URL, entry_count=len(entries), entries=entries)
        )
        await session.commit()


def _walk_catalog(client: TestClient, **params: object) -> list[str]:
    """Page through /catalog via next_cursor and return the flat api_id list."""
    out: list[str] = []
    cursor: str | None = None
    for _ in range(1000):
        query = {**params}
        if cursor is not None:
            query["cursor"] = cursor
        body = client.get("/catalog", params=query).json()
        out.extend(e["api_id"] for e in body["data"])
        if not body["has_more"]:
            return out
        cursor = body["next_cursor"]
        assert cursor is not None
    raise AssertionError("pagination did not terminate")


async def test_browse_pagination_walks_all_entries_once(
    admin_client: TestClient, web_context: Context
) -> None:
    # Seed in a non-sorted order so a "return everything, ignore cursor" bug
    # can't pass by coincidence — the walk must re-impose api_id order.
    ids = [f"api-{i:03d}.com" for i in range(25)]
    shuffled = ids[12:] + ids[:12]
    await _seed_snapshot(web_context, shuffled)

    first = admin_client.get("/catalog", params={"limit": 10}).json()
    assert len(first["data"]) == 10
    assert first["has_more"] is True
    assert first["next_cursor"] is not None
    # status counts reflect the whole manifest, not the page
    assert first["catalog_total"] == 25

    walked = _walk_catalog(admin_client, limit=10)
    assert walked == sorted(ids)
    assert len(walked) == 25


def _last_page(client: TestClient, **params: object) -> dict[str, Any]:
    cursor: str | None = None
    for _ in range(1000):
        query = {**params}
        if cursor is not None:
            query["cursor"] = cursor
        body: dict[str, Any] = client.get("/catalog", params=query).json()
        if not body["has_more"]:
            return body
        cursor = body["next_cursor"]
    raise AssertionError("pagination did not terminate")


async def test_final_page_has_no_more_and_null_cursor(
    admin_client: TestClient, web_context: Context
) -> None:
    await _seed_snapshot(web_context, [f"api-{i:03d}.com" for i in range(7)])
    last = _last_page(admin_client, limit=3)
    assert last["has_more"] is False
    assert last["next_cursor"] is None


async def test_search_pagination_walks_ranked_results_in_order(
    admin_client: TestClient, web_context: Context
) -> None:
    # 2-token query gives distinct scores: foo-bar (both tokens, 6) outranks the
    # single-token halves (3 each).
    ids = ["foo-bar.com", "foo-x.com", "bar-y.com", "unrelated.com"]
    await _seed_snapshot(web_context, ids)

    walked = _walk_catalog(admin_client, q="foo bar", limit=1)
    # order must be preserved across pages: full match first, then the 3s by api_id
    assert walked == ["foo-bar.com", "bar-y.com", "foo-x.com"]
    assert "unrelated.com" not in walked


async def test_filtered_walk_exact_set_and_count_stability(
    admin_client: TestClient, web_context: Context
) -> None:
    """unregistered_only walk returns the exact unregistered set with stable counts.

    Seeds a snapshot where some entries are locally registered (a matching draft
    revision exists), then pages the unregistered-only view and asserts: the
    walked set is exactly the unregistered entries (no skips/dups across pages),
    and catalog_total/registered_count are identical on every page (whole-manifest
    counts, not page-local).
    """
    ids = [f"api-{i:02d}.com" for i in range(10)]
    await _seed_snapshot(web_context, ids)
    registered = {ids[2], ids[5], ids[8]}
    spec_urls = {a: f"{_MANIFEST_BASE}/openapi/{a}/openapi.json" for a in registered}
    try:
        async with web_context.registry_db.session() as session:
            for i, a in enumerate(sorted(registered)):
                api = Api(vendor=a, name=f"n{i}", version="v1", revision_count=1)
                session.add(api)
                await session.flush()
                session.add(
                    ApiRevision(
                        api_id=api.id,
                        state="draft",
                        spec_digest=f"sha256:{i}",
                        source_type="url",
                        source_url=spec_urls[a],
                    )
                )
            await session.commit()

        # walk unregistered-only across small pages, collecting counts per page
        out: list[str] = []
        counts: list[tuple[int, int]] = []
        cursor: str | None = None
        for _ in range(1000):
            query: dict[str, object] = {"unregistered_only": True, "limit": 2}
            if cursor is not None:
                query["cursor"] = cursor
            body = admin_client.get("/catalog", params=query).json()
            out.extend(e["api_id"] for e in body["data"])
            counts.append((body["catalog_total"], body["registered_count"]))
            if not body["has_more"]:
                break
            cursor = body["next_cursor"]

        assert sorted(out) == sorted(set(ids) - registered)
        assert len(out) == len(set(out)) == 7  # no dups, exact count
        # whole-manifest counts identical on every page
        assert all(c == (10, 3) for c in counts)
    finally:
        async with web_context.registry_db.session() as session:
            await session.execute(text("UPDATE registry.apis SET current_revision_id = NULL"))
            await session.execute(text("DELETE FROM registry.api_revisions"))
            await session.execute(text("DELETE FROM registry.apis"))
            await session.commit()


async def test_cursor_past_end_returns_empty_terminal_page(
    admin_client: TestClient, web_context: Context
) -> None:
    await _seed_snapshot(web_context, ["a.com", "b.com"])
    # synthesise a cursor positioned past the last entry
    cursor = encode_catalog_cursor("zzz.com")
    body = admin_client.get("/catalog", params={"cursor": cursor}).json()
    assert body["data"] == []
    assert body["has_more"] is False
    assert body["next_cursor"] is None


def test_invalid_cursor_returns_400(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    r = admin_client.get("/catalog", params={"cursor": "not-a-valid-cursor!!!"})
    assert r.status_code == 400


def test_malformed_but_base64_cursor_returns_400(admin_client: TestClient) -> None:
    # Valid base64, wrong shape (missing id) — must be 400, not 500.
    bad = base64.b64encode(json.dumps({"s": 0.1}).encode()).decode()
    r = admin_client.get("/catalog", params={"cursor": bad})
    assert r.status_code == 400


def test_limit_out_of_bounds_rejected(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    assert admin_client.get("/catalog", params={"limit": 0}).status_code == 422
    assert admin_client.get("/catalog", params={"limit": 201}).status_code == 422


def test_mutually_exclusive_filters_rejected(admin_client: TestClient) -> None:
    resp = admin_client.get("/catalog", params={"registered_only": True, "unregistered_only": True})
    assert resp.status_code == 422
    assert "mutually_exclusive" in resp.json()["type"]


def test_list_unregistered_only(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    body = admin_client.get("/catalog", params={"unregistered_only": True}).json()
    # nothing locally registered for these vendors in a clean DB
    assert all(e["registered"] is False for e in body["data"])


async def test_entry_registered_after_local_import(
    admin_client: TestClient, web_context: Context
) -> None:
    """A catalog entry flips unregistered→registered once its spec is imported.

    Coverage is keyed on ``spec_url == ApiRevision.source_url``. Importing creates
    a *draft* revision (never promoted), so this also guards the regression where
    keying on the current revision left every fresh import showing registered=false.
    """
    admin_client.post("/catalog:refresh")

    # Before: stripe.com is not registered.
    before = admin_client.get("/catalog/stripe.com").json()
    assert before["registered"] is False

    # Seed a local API whose draft revision's source_url is the entry's spec_url
    # (this is exactly what catalog :import records — manifest /apis.json→/openapi.json).
    spec_url = f"{_MANIFEST_BASE}/openapi/stripe.com/main/2024-01-01/openapi.json"
    async with web_context.registry_db.session() as session:
        api = Api(vendor="stripe.com", name="stripe", version="2024-01-01", revision_count=1)
        session.add(api)
        await session.flush()
        session.add(
            ApiRevision(
                api_id=api.id,
                state="draft",  # un-promoted, as a real import leaves it
                spec_digest="sha256:stripe",
                source_type="url",
                source_url=spec_url,
            )
        )
        await session.commit()

    try:
        after = admin_client.get("/catalog/stripe.com").json()
        assert after["registered"] is True

        listing = admin_client.get("/catalog").json()
        assert listing["registered_count"] == 1
        stripe = next(e for e in listing["data"] if e["api_id"] == "stripe.com")
        assert stripe["registered"] is True
    finally:
        async with web_context.registry_db.session() as session:
            await session.execute(text("UPDATE registry.apis SET current_revision_id = NULL"))
            await session.execute(text("DELETE FROM registry.api_revisions"))
            await session.execute(text("DELETE FROM registry.apis"))
            await session.commit()


# ── get + 404 ────────────────────────────────────────────────────────────────


def test_get_entry(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    r = admin_client.get("/catalog/stripe.com")
    assert r.status_code == 200
    body = r.json()
    assert body["api_id"] == "stripe.com"
    assert "import" in body["_links"]


def test_get_unknown_entry_404(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    r = admin_client.get("/catalog/does-not-exist.com")
    assert r.status_code == 404
    assert "catalog_entry_not_found" in json.dumps(r.json())


# ── preview ──────────────────────────────────────────────────────────────────


def test_preview_operations(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    r = admin_client.get("/catalog/stripe.com/operations")
    assert r.status_code == 200
    body = r.json()
    methods = sorted(op["method"] for op in body["data"])
    assert methods == ["GET", "POST"]
    assert body["info"]["title"] == "Stripe"


def test_preview_operations_q_filters_across_spec(admin_client: TestClient) -> None:
    """`q` filters the full operation set server-side (method/path/summary/opId)."""
    admin_client.post("/catalog:refresh")
    r = admin_client.get("/catalog/stripe.com/operations", params={"q": "create"})
    assert r.status_code == 200
    body = r.json()
    # Only the "create charge" POST matches; total reflects the filtered count.
    assert body["total"] == 1
    assert len(body["data"]) == 1
    assert body["data"][0]["method"] == "POST"


def test_preview_operations_q_is_case_insensitive(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    r = admin_client.get("/catalog/stripe.com/operations", params={"q": "LISTCHARGES"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["data"][0]["operation_id"] == "listCharges"


def test_preview_operations_offset_limit_paginates(admin_client: TestClient) -> None:
    """offset/limit window the (filtered) set; total stays the full count."""
    admin_client.post("/catalog:refresh")
    first = admin_client.get("/catalog/stripe.com/operations", params={"limit": 1, "offset": 0})
    assert first.status_code == 200
    fbody = first.json()
    assert fbody["total"] == 2
    assert len(fbody["data"]) == 1
    assert fbody["truncated"] is True

    second = admin_client.get("/catalog/stripe.com/operations", params={"limit": 1, "offset": 1})
    sbody = second.json()
    assert len(sbody["data"]) == 1
    assert sbody["truncated"] is False
    assert sbody["data"][0]["method"] != fbody["data"][0]["method"]


def test_preview_operations_q_no_match_is_empty(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    r = admin_client.get("/catalog/stripe.com/operations", params={"q": "zzz-nope"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["data"] == []


def test_preview_operations_q_over_max_length_rejected(admin_client: TestClient) -> None:
    """`q` is capped at 500 chars (defensive bound); longer values are rejected."""
    admin_client.post("/catalog:refresh")
    r = admin_client.get("/catalog/stripe.com/operations", params={"q": "x" * 501})
    assert r.status_code == 422


def test_preview_operations_q_at_max_length_accepted(admin_client: TestClient) -> None:
    """A 500-char `q` is within bounds and processed normally (no match here)."""
    admin_client.post("/catalog:refresh")
    r = admin_client.get("/catalog/stripe.com/operations", params={"q": "x" * 500})
    assert r.status_code == 200
    assert r.json()["total"] == 0


# ── import ───────────────────────────────────────────────────────────────────


def test_import_entry_enqueues_job(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    r = admin_client.post("/catalog/stripe.com:import")
    assert r.status_code == 202
    body = r.json()
    assert "job_id" in body
    assert body["status"] == "queued"


def test_import_unknown_entry_404(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    assert admin_client.post("/catalog/nope.com:import").status_code == 404


def test_to_import_source_threads_catalog_vendor_and_api_name(web_context: Context) -> None:
    """Catalog imports carry the manifest-derived vendor and api_name so specs that
    omit ``info.x-vendor``/``contact.name``/``title`` (e.g. coincap) still resolve
    an api_identifier instead of failing with 'missing vendor' or 'missing name'."""
    view = CatalogEntryView(
        api_id="coincap.io",
        vendor="coincap.io",
        path="apis/openapi/coincap.io",
        spec_url="https://example.test/coincap/openapi.json",
        github_url=None,
        registered=False,
    )
    assert CatalogService(web_context)._to_import_source(view) == {
        "type": "url",
        "url": "https://example.test/coincap/openapi.json",
        "origin": "catalog",
        "vendor": "coincap.io",
        "api_name": "coincap.io",
    }


def test_to_import_source_threads_api_name_with_slash(web_context: Context) -> None:
    """A slash-containing api_id (e.g. slack.com/api) threads the full value as api_name."""
    view = CatalogEntryView(
        api_id="slack.com/api",
        vendor="slack.com",
        path="apis/openapi/slack.com/api",
        spec_url="https://example.test/slack/openapi.json",
        github_url=None,
        registered=False,
    )
    result = CatalogService(web_context)._to_import_source(view)
    assert result["api_name"] == "slack.com/api"
    assert result["vendor"] == "slack.com"
    assert result["origin"] == "catalog"


def test_to_import_source_omits_absent_vendor(web_context: Context) -> None:
    """A vendor-less entry imports url + api_name (importer falls back to spec info for vendor)."""
    view = CatalogEntryView(
        api_id="x",
        vendor=None,
        path="",
        spec_url="https://example.test/x/openapi.json",
        github_url=None,
        registered=False,
    )
    assert CatalogService(web_context)._to_import_source(view) == {
        "type": "url",
        "url": "https://example.test/x/openapi.json",
        "origin": "catalog",
        "api_name": "x",
    }


# ── ensure_imported (Credentials-PR hand-off seam) ─────────────────────────--


async def test_ensure_imported_enqueues_when_unregistered(web_context: Context) -> None:
    """The hand-off seam enqueues an import job for an unregistered entry."""
    await _seed_snapshot(web_context, ["stripe.com"])
    identity = Identity(sub="usr_1", email="u@test.local", permissions=[])
    job_id = await CatalogService(web_context).ensure_imported("stripe.com", identity)
    assert isinstance(job_id, str)


async def test_ensure_imported_noop_when_already_registered(web_context: Context) -> None:
    """No job is enqueued when the entry's spec_url already backs a local revision."""
    await _seed_snapshot(web_context, ["stripe.com"])
    spec_url = f"{_MANIFEST_BASE}/openapi/stripe.com/openapi.json"
    try:
        async with web_context.registry_db.session() as session:
            api = Api(vendor="stripe.com", name="stripe", version="v1", revision_count=1)
            session.add(api)
            await session.flush()
            session.add(
                ApiRevision(
                    api_id=api.id,
                    state="draft",
                    spec_digest="sha256:stripe",
                    source_type="url",
                    source_url=spec_url,
                )
            )
            await session.commit()

        identity = Identity(sub="usr_1", email="u@test.local", permissions=[])
        job_id = await CatalogService(web_context).ensure_imported("stripe.com", identity)
        assert job_id is None
    finally:
        async with web_context.registry_db.session() as session:
            await session.execute(text("UPDATE registry.apis SET current_revision_id = NULL"))
            await session.execute(text("DELETE FROM registry.api_revisions"))
            await session.execute(text("DELETE FROM registry.apis"))
            await session.commit()
