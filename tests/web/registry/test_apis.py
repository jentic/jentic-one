"""Web tests for the registry APIs endpoints."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from jentic_one.admin.core.schema.jobs import Job
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.repos.catalog_update_check_repo import CatalogUpdateCheckRepository
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


async def _seed_api(
    ctx: Context,
    *,
    vendor: str,
    name: str = "api",
    version: str = "v1",
    promote: bool = True,
    source_url: str | None = None,
) -> Api:
    """Seed a local Api with a revision (published+current when ``promote``)."""
    async with ctx.registry_db.session() as session:
        api = Api(vendor=vendor, name=name, version=version, revision_count=1)
        session.add(api)
        await session.flush()
        revision = ApiRevision(
            api_id=api.id,
            state="published" if promote else "draft",
            spec_digest=f"sha256:{vendor}-{name}-{version}",
            source_type="url",
            source_url=source_url,
        )
        session.add(revision)
        await session.flush()
        if promote:
            api.current_revision_id = revision.id
            await session.flush()
        await session.commit()
        return api


@pytest.fixture()
async def _cleanup_apis(web_context: Context) -> AsyncGenerator[None]:
    """Clean up seeded APIs after each test that seeds."""
    yield
    async with web_context.registry_db.session() as session:
        await session.execute(text("UPDATE registry.apis SET current_revision_id = NULL"))
        await session.execute(text("DELETE FROM registry.catalog_update_checks"))
        await session.execute(text("DELETE FROM registry.api_revisions"))
        await session.execute(text("DELETE FROM registry.apis"))
        await session.commit()


# --- GET /apis tests ---


def test_list_apis_returns_200(authed_client: TestClient) -> None:
    resp = authed_client.get("/apis")
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert "has_more" in data
    assert isinstance(data["data"], list)


def test_list_apis_without_auth_returns_401(unauthed_client: TestClient) -> None:
    resp = unauthed_client.get("/apis")
    assert resp.status_code == 401


def test_list_apis_empty_result(authed_client: TestClient) -> None:
    resp = authed_client.get("/apis", params={"vendor": "nonexistent-vendor-xyz"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"] == []
    assert data["has_more"] is False
    assert data["next_cursor"] is None


def test_list_apis_vendor_filter(authed_client: TestClient) -> None:
    resp = authed_client.get("/apis", params={"vendor": "test-filter-vendor"})
    assert resp.status_code == 200
    data = resp.json()
    for item in data["data"]:
        assert item["api"]["vendor"] == "test-filter-vendor"


def test_list_apis_pagination_limit(authed_client: TestClient) -> None:
    resp = authed_client.get("/apis", params={"limit": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) <= 1


def test_list_apis_invalid_cursor_returns_400(authed_client: TestClient) -> None:
    resp = authed_client.get("/apis", params={"cursor": "not-valid-base64"})
    assert resp.status_code == 400
    data = resp.json()
    assert data["detail"] == "Invalid pagination cursor"


def test_list_apis_response_shape(authed_client: TestClient) -> None:
    resp = authed_client.get("/apis")
    assert resp.status_code == 200
    data = resp.json()
    for item in data["data"]:
        assert "api" in item
        assert "vendor" in item["api"]
        assert "name" in item["api"]
        assert "version" in item["api"]
        assert "host" in item["api"]
        assert "display_name" in item
        assert "description" in item
        assert "revision_count" in item
        assert "operation_count" in item
        assert "security_schemes" in item
        assert "created_at" in item
        assert "updated_at" in item
        assert "_links" in item
        links = item["_links"]
        assert "self" in links
        assert "revisions" in links


# --- POST /apis tests ---


def test_import_returns_202(authed_client: TestClient) -> None:
    resp = authed_client.post(
        "/apis",
        json={
            "sources": [
                {
                    "type": "url",
                    "url": "https://api.example.com/openapi.yaml",
                }
            ]
        },
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "queued"
    assert "self" in data["_links"]


def test_import_missing_sources_returns_422(authed_client: TestClient) -> None:
    resp = authed_client.post("/apis", json={})
    assert resp.status_code == 422


def test_import_empty_sources_returns_422(authed_client: TestClient) -> None:
    resp = authed_client.post("/apis", json={"sources": []})
    assert resp.status_code == 422


def test_import_without_auth(unauthed_client: TestClient) -> None:
    resp = unauthed_client.post(
        "/apis",
        json={"sources": [{"type": "url", "url": "https://example.com/spec.yaml"}]},
    )
    assert resp.status_code == 401


async def _get_job_payload(ctx: Context, job_id: str) -> dict[str, Any]:
    async with ctx.admin_db.session() as session:
        row = await session.get(Job, job_id)
        assert row is not None
        assert row.payload is not None
        return dict(row.payload)


async def test_import_defaults_submitted_by_to_principal(
    authed_client: TestClient, web_context: Context
) -> None:
    """A source without `submitted_by` is attributed to the authenticated caller,
    so the resulting revision's `submitted_by` is not null (import attribution)."""
    resp = authed_client.post(
        "/apis",
        json={"sources": [{"type": "url", "url": "https://api.example.com/openapi.yaml"}]},
    )
    assert resp.status_code == 202
    payload = await _get_job_payload(web_context, resp.json()["job_id"])
    assert payload["sources"][0]["submitted_by"] == "usr_test_registry"


async def test_import_keeps_explicit_submitted_by(
    authed_client: TestClient, web_context: Context
) -> None:
    """A client-supplied `submitted_by` (importing on behalf of someone) wins."""
    resp = authed_client.post(
        "/apis",
        json={
            "sources": [
                {
                    "type": "url",
                    "url": "https://api.example.com/openapi.yaml",
                    "submitted_by": "someone-else@example.com",
                }
            ]
        },
    )
    assert resp.status_code == 202
    payload = await _get_job_payload(web_context, resp.json()["job_id"])
    assert payload["sources"][0]["submitted_by"] == "someone-else@example.com"


def test_import_inline_source(authed_client: TestClient) -> None:
    resp = authed_client.post(
        "/apis",
        json={
            "sources": [
                {
                    "type": "inline",
                    "content": "openapi: 3.1.0\ninfo:\n  title: Test\n  version: 1.0.0\npaths: {}",
                    "filename": "test.yaml",
                }
            ]
        },
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "queued"


# --- GET /apis is imported-only (catalog lives at /catalog) (D-005a) ---


async def test_list_apis_all_items_are_local(
    authed_client: TestClient, web_context: Context, _cleanup_apis: None
) -> None:
    """/apis is local-only — items carry no catalog-*blend* fields.

    ``catalog_api_id`` is NOT the old blend coming back: the blended contract
    used it to mark catalog-sourced *list rows*; the field on today's contract
    is persisted import provenance on the local Api row itself (#910) — null
    for APIs that never came from the catalog, like these seeded ones.
    """
    await _seed_api(web_context, vendor="local-a.com")
    await _seed_api(web_context, vendor="local-b.com")
    resp = authed_client.get("/apis")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) >= 2  # the de-blend assertions below are not vacuous
    for item in data:
        # the old blended contract's fields are gone, not just constant
        assert "source" not in item
        assert "registered" not in item
        # import provenance is present but null for non-catalog imports
        assert item["catalog_api_id"] is None
        # and an import link never appears on a local api row
        assert "import" not in item["_links"]


async def test_list_apis_ignores_legacy_source_param(
    authed_client: TestClient, web_context: Context, _cleanup_apis: None
) -> None:
    """The removed ?source= param is ignored; /apis never blends catalog rows."""
    await _seed_api(web_context, vendor="local-only.com")
    resp = authed_client.get("/apis", params={"source": "catalog"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) >= 1
    vendors = {item["api"]["vendor"] for item in data}
    assert "local-only.com" in vendors
    # de-blend: no catalog-flavored fields leak in regardless of the param
    for item in data:
        assert "source" not in item


async def test_list_apis_paginates_across_boundary(
    authed_client: TestClient, web_context: Context, _cleanup_apis: None
) -> None:
    """Following next_cursor yields every local API exactly once (no overlap/gaps)."""
    for i in range(3):
        await _seed_api(web_context, vendor=f"page-{i}.com")

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):  # safety bound; 3 items at limit=2 needs 2 pages
        params: dict[str, object] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        resp = authed_client.get("/apis", params=params)
        assert resp.status_code == 200
        body = resp.json()
        seen.extend(item["api"]["vendor"] for item in body["data"])
        cursor = body["next_cursor"]
        if not body["has_more"]:
            break

    assert sorted(seen) == ["page-0.com", "page-1.com", "page-2.com"]
    assert len(seen) == len(set(seen))  # no duplicates across pages


async def test_list_and_detail_agree_on_update_available_for_published_over_catalog(
    authed_client: TestClient, web_context: Context, _cleanup_apis: None
) -> None:
    """The /apis list and single-API detail must agree on ``update_available``.

    Regression for ren-jentic M2: the list surface flags ``update_available`` purely on
    ``api_id in outdated_api_ids`` (no gate), but the detail path used to short-circuit on
    ``if current_revision.source_url is not None`` — so in the *documented* caveat case (a
    manually PUBLISHED revision, ``source_url=None``, superseding a catalog import while the
    upstream is still outdated) the list lit the badge and the detail returned False. An
    operator would click a lit card and see nothing. The gate is now dropped so both key on
    the (exact, api_id-scoped) outdated set.     Here we seed exactly that state — a published
    current revision with no ``source_url`` plus a check row whose notified digest differs
    from the served digest — and assert both surfaces report ``update_available=True``.
    """
    api = await _seed_api(web_context, vendor="published-over-catalog.com", source_url=None)
    # A prior catalog import left a check row; upstream then advanced past what we now serve.
    async with web_context.registry_db.transaction() as session:
        await CatalogUpdateCheckRepository.upsert(
            session,
            local_api_id=api.id,
            spec_url="https://upstream.example/openapi.json",
            etag='"v2"',
            digest="sha256:upstream-new",
            checked_at=datetime.now(UTC),
            notified_digest="sha256:upstream-new",  # != served f"sha256:{vendor}-api-v1"
        )

    list_resp = authed_client.get("/apis", params={"vendor": "published-over-catalog.com"})
    assert list_resp.status_code == 200
    list_items = list_resp.json()["data"]
    assert len(list_items) == 1
    assert list_items[0]["update_available"] is True

    detail_resp = authed_client.get("/apis/published-over-catalog.com/api/v1")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["source_url"] is None  # the caveat precondition: manually published
    assert detail["update_available"] is True  # must match the list, not short-circuit off
