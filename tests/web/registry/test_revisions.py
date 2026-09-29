"""Web tests for the revision endpoints."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.servers import Server
from jentic_one.registry.core.url_index import build_index_entry
from jentic_one.registry.repos.operation_repo import OperationInput, OperationRepository
from jentic_one.registry.repos.url_index_repo import UrlIndexRepository
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


async def _seed_api_with_revisions(
    ctx: Context,
    *,
    vendor: str = "acme",
    name: str = "payments",
    version: str = "v1",
    revision_states: list[str] | None = None,
    source_type: str | None = "url",
    source_url: str | None = "https://api.acme.com/openapi.yaml",
    server_url: str = "https://api.acme.com/v1",
) -> tuple[Api, list[ApiRevision]]:
    """Seed an API with one or more revisions."""
    if revision_states is None:
        revision_states = ["published"]

    async with ctx.registry_db.session() as session:
        api = Api(
            vendor=vendor,
            name=name,
            version=version,
            display_name="Acme Payments",
            revision_count=len(revision_states),
            operation_count=5,
        )
        session.add(api)
        await session.flush()

        revisions: list[ApiRevision] = []
        base_time = datetime(2024, 1, 1, tzinfo=UTC)
        for i, state in enumerate(revision_states):
            revision = ApiRevision(
                api_id=api.id,
                state=state,
                spec_digest=f"sha256:rev{i}",
                source_type=source_type,
                source_url=source_url if source_type == "url" else None,
                source_filename="spec.yaml" if source_type == "inline" else None,
                submitted_by="test-user",
                operation_count=5,
                promoted_at=base_time + timedelta(hours=i) if state == "published" else None,
                archived_at=base_time + timedelta(hours=i) if state == "archived" else None,
                created_at=base_time + timedelta(hours=i),
            )
            session.add(revision)
            await session.flush()

            server = Server(revision_id=revision.id, url=server_url)
            session.add(server)
            await session.flush()

            revisions.append(revision)

        if any(r.state == "published" for r in revisions):
            published = next(r for r in revisions if r.state == "published")
            api.current_revision_id = published.id
            await session.flush()

        await session.commit()
        return api, revisions


@pytest.fixture(autouse=True)
async def _cleanup(web_context: Context) -> AsyncGenerator[None]:
    yield
    async with web_context.registry_db.session() as session:
        # apis.current_revision_id references api_revisions, and
        # api_revisions.api_id references apis (a cycle), so break the cycle by
        # clearing current_revision_id before deleting either table.
        await session.execute(text("UPDATE registry.apis SET current_revision_id = NULL"))
        await session.execute(text("DELETE FROM registry.servers"))
        await session.execute(text("DELETE FROM registry.security_schemes"))
        await session.execute(text("DELETE FROM registry.api_revisions"))
        await session.execute(text("DELETE FROM registry.apis"))
        await session.commit()


async def test_list_revisions_returns_200(authed_client: TestClient, web_context: Context) -> None:
    await _seed_api_with_revisions(web_context)
    resp = authed_client.get("/apis/acme/payments/v1/revisions")
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert "has_more" in data
    assert len(data["data"]) == 1
    item = data["data"][0]
    assert "revision_id" in item
    assert item["api"]["vendor"] == "acme"
    assert item["state"] == "published"
    assert item["is_current"] is True
    assert "_links" in item
    assert "self" in item["_links"]
    assert "api" in item["_links"]


async def test_list_revisions_state_filter(authed_client: TestClient, web_context: Context) -> None:
    await _seed_api_with_revisions(web_context, revision_states=["draft", "published"])
    resp = authed_client.get("/apis/acme/payments/v1/revisions", params={"state": "draft"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["state"] == "draft"


async def test_list_revisions_pagination(authed_client: TestClient, web_context: Context) -> None:
    await _seed_api_with_revisions(web_context, revision_states=["draft", "draft", "draft"])
    resp = authed_client.get("/apis/acme/payments/v1/revisions", params={"limit": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == 1
    assert data["has_more"] is True
    assert data["next_cursor"] is not None

    resp2 = authed_client.get(
        "/apis/acme/payments/v1/revisions",
        params={"limit": 1, "cursor": data["next_cursor"]},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["data"]) == 1
    assert data2["data"][0]["revision_id"] != data["data"][0]["revision_id"]


async def test_list_revisions_unknown_api_returns_404(authed_client: TestClient) -> None:
    resp = authed_client.get("/apis/unknown/api/v99/revisions")
    assert resp.status_code == 404
    data = resp.json()
    assert data["type"] == "api_not_found"


async def test_list_revisions_invalid_cursor_returns_400(
    authed_client: TestClient, web_context: Context
) -> None:
    await _seed_api_with_revisions(web_context)
    resp = authed_client.get("/apis/acme/payments/v1/revisions", params={"cursor": "not-valid"})
    assert resp.status_code == 400


async def test_get_revision_returns_200(authed_client: TestClient, web_context: Context) -> None:
    _, revisions = await _seed_api_with_revisions(web_context)
    revision_id = str(revisions[0].id)
    resp = authed_client.get(f"/apis/acme/payments/v1/revisions/{revision_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["revision_id"] == revision_id
    assert data["api"]["vendor"] == "acme"
    assert data["state"] == "published"
    assert data["is_current"] is True
    assert data["operation_count"] == 5
    assert data["spec_digest"] == "sha256:rev0"
    assert "_links" in data
    assert "self" in data["_links"]
    assert "api" in data["_links"]


async def test_get_revision_draft_has_promote_archive_links(
    authed_client: TestClient, web_context: Context
) -> None:
    _, revisions = await _seed_api_with_revisions(web_context, revision_states=["draft"])
    revision_id = str(revisions[0].id)
    resp = authed_client.get(f"/apis/acme/payments/v1/revisions/{revision_id}")
    assert resp.status_code == 200
    data = resp.json()
    links = data["_links"]
    assert links["promote"] is not None
    assert ":promote" in links["promote"]
    assert links["archive"] is not None
    assert ":archive" in links["archive"]


async def test_get_revision_published_no_promote_archive_links(
    authed_client: TestClient, web_context: Context
) -> None:
    _, revisions = await _seed_api_with_revisions(web_context, revision_states=["published"])
    revision_id = str(revisions[0].id)
    resp = authed_client.get(f"/apis/acme/payments/v1/revisions/{revision_id}")
    assert resp.status_code == 200
    data = resp.json()
    links = data["_links"]
    assert links["promote"] is None
    assert links["archive"] is None


async def test_get_revision_unknown_api_returns_404(authed_client: TestClient) -> None:
    resp = authed_client.get(f"/apis/unknown/api/v99/revisions/{uuid.uuid4()}")
    assert resp.status_code == 404
    data = resp.json()
    assert data["type"] == "api_not_found"


async def test_get_revision_unknown_revision_returns_404(
    authed_client: TestClient, web_context: Context
) -> None:
    await _seed_api_with_revisions(web_context)
    fake_id = str(uuid.uuid4())
    resp = authed_client.get(f"/apis/acme/payments/v1/revisions/{fake_id}")
    assert resp.status_code == 404
    data = resp.json()
    assert data["type"] == "revision_not_found"


async def test_list_revisions_without_auth_returns_401(unauthed_client: TestClient) -> None:
    resp = unauthed_client.get("/apis/acme/payments/v1/revisions")
    assert resp.status_code == 401


async def test_get_revision_source_url(authed_client: TestClient, web_context: Context) -> None:
    _, revisions = await _seed_api_with_revisions(
        web_context, source_type="url", source_url="https://example.com/spec.yaml"
    )
    revision_id = str(revisions[0].id)
    resp = authed_client.get(f"/apis/acme/payments/v1/revisions/{revision_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"]["type"] == "url"
    assert data["source"]["url"] == "https://example.com/spec.yaml"


async def test_get_revision_source_inline(authed_client: TestClient, web_context: Context) -> None:
    _, revisions = await _seed_api_with_revisions(
        web_context, source_type="inline", source_url=None
    )
    revision_id = str(revisions[0].id)
    resp = authed_client.get(f"/apis/acme/payments/v1/revisions/{revision_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"]["type"] == "inline"
    assert data["source"]["filename"] == "spec.yaml"


async def _index_url(ctx: Context, revision: ApiRevision, host: str, path: str) -> None:
    async with ctx.registry_db.session() as session:
        op_ids = await OperationRepository.bulk_create(
            session, revision.id, [OperationInput(path=path, method="GET")], created_by="usr_test"
        )
        await UrlIndexRepository.upsert_entry(
            session,
            revision_id=revision.id,
            operation_id=op_ids[0],
            method="GET",
            entry=build_index_entry(host, path, "https"),
            created_by="usr_test",
        )
        await session.commit()


async def test_promote_onto_host_served_by_other_vendor_returns_409(
    authed_client: TestClient, web_context: Context
) -> None:
    _owner, [owner_live] = await _seed_api_with_revisions(web_context, vendor="acme")
    await _index_url(web_context, owner_live, "api.acme.com", "/v1/charges")
    _other, [other_draft] = await _seed_api_with_revisions(
        web_context, vendor="other", revision_states=["draft"]
    )
    await _index_url(web_context, other_draft, "api.acme.com", "/v1/charges")

    resp = authed_client.post(f"/apis/other/payments/v1/revisions/{other_draft.id}:promote")

    assert resp.status_code == 409
    body = resp.json()
    assert body["type"].endswith("host_owned_by_other_vendor")
    assert "api.acme.com" in body["detail"]
