"""P9-be — operations endpoints: `offset`, `limit`, `tag`, `truncated`.

Both `GET /catalog/{api_id}/operations` and `GET /apis/{api_id}/operations`
were originally `?limit`-only with `total` + `truncated`. P9 added two
parameters that have to behave identically across both endpoints so the
UI can reuse a single load-more renderer:

  - `?offset: int = 0`            — pagination cursor
  - `?tag: str | None = None`     — case-insensitive substring on op.tags

The contract under test:
  - `total` is the post-tag-filter count
  - `truncated` reflects whether the response window stops before `total`
  - tag filtering is case-insensitive substring, not exact
"""

import json
from unittest.mock import patch

import pytest
from src.routers import catalog as catalog_router


# ── Catalog operations endpoint ──────────────────────────────────────────────


def _build_large_spec(n_ops: int = 60) -> dict:
    """Build a synthetic OpenAPI doc with `n_ops` operations split between
    two tag groups so we can test both pagination and tag narrowing."""
    paths: dict = {}
    for i in range(n_ops):
        # Half land under `Users`, the other half under `Posts`. The
        # 80/20 split is arbitrary but keeps tag-filter assertions
        # honest (we want both groups present).
        tag = "Users" if i < n_ops // 2 else "Posts"
        paths[f"/{tag.lower()}/{i}"] = {
            "get": {
                "operationId": f"op_{i}",
                "summary": f"Operation {i}",
                "description": f"Mock op {i}",
                "tags": [tag],
                "responses": {"200": {"description": "ok"}},
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {"title": "big-mock", "version": "1.0.0"},
        "servers": [{"url": "https://big-mock.test.local"}],
        "paths": paths,
    }


@pytest.fixture
def _mocked_catalog_spec(monkeypatch, tmp_path):
    """Plant a single-entry catalog manifest pointing at a fake URL and
    intercept `urllib.request.urlopen` so the spec fetch is deterministic."""
    api_id = "big-mock.test.local"
    manifest_path = tmp_path / "catalog_manifest.json"
    manifest_path.write_text(
        json.dumps(
            [
                {
                    "api_id": api_id,
                    "path": f"apis/openapi/{api_id}",
                    "spec_url": f"https://example.invalid/{api_id}.json",
                }
            ]
        )
    )
    monkeypatch.setattr(catalog_router, "CATALOG_MANIFEST_PATH", manifest_path)

    spec_bytes = json.dumps(_build_large_spec(60)).encode("utf-8")

    class _FakeResp:
        def __init__(self, body: bytes):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(*args, **kwargs):
        return _FakeResp(spec_bytes)

    with patch("urllib.request.urlopen", _fake_urlopen):
        yield api_id


def test_catalog_operations_offset_pagination(admin_client, _mocked_catalog_spec):
    api_id = _mocked_catalog_spec
    # First page (offset=0, limit=25)
    r1 = admin_client.get(f"/catalog/{api_id}/operations", params={"offset": 0, "limit": 25})
    assert r1.status_code == 200, r1.text
    page1 = r1.json()
    assert page1["total"] == 60
    assert page1["limit"] == 25
    assert page1["offset"] == 0
    assert len(page1["data"]) == 25
    assert page1["truncated"] is True

    # Second page (offset=25, limit=25)
    r2 = admin_client.get(f"/catalog/{api_id}/operations", params={"offset": 25, "limit": 25})
    page2 = r2.json()
    assert page2["offset"] == 25
    assert len(page2["data"]) == 25
    assert page2["truncated"] is True
    # Adjacent windows must not overlap.
    p1_ids = {o["operation_id"] for o in page1["data"]}
    p2_ids = {o["operation_id"] for o in page2["data"]}
    assert p1_ids.isdisjoint(p2_ids)

    # Tail page that runs past `total`.
    r3 = admin_client.get(f"/catalog/{api_id}/operations", params={"offset": 50, "limit": 25})
    page3 = r3.json()
    assert page3["offset"] == 50
    assert len(page3["data"]) == 10  # only 10 left in 60-item set
    assert page3["truncated"] is False


def test_catalog_operations_tag_narrowing_is_case_insensitive(admin_client, _mocked_catalog_spec):
    api_id = _mocked_catalog_spec
    # `tag=Users` should narrow to the 30 ops carrying that tag.
    resp = admin_client.get(f"/catalog/{api_id}/operations", params={"tag": "Users"})
    body = resp.json()
    assert body["total"] == 30, body
    for op in body["data"]:
        assert any("user" in t.lower() for t in op["tags"]), op

    # Lowercased query exhibits the same behaviour — filter is case-insensitive.
    resp_lower = admin_client.get(f"/catalog/{api_id}/operations", params={"tag": "users"})
    assert resp_lower.json()["total"] == 30


def test_catalog_operations_total_reflects_post_tag_filter(admin_client, _mocked_catalog_spec):
    api_id = _mocked_catalog_spec
    # Tag-narrowed first page must report `total=30` (post-filter), not 60.
    resp = admin_client.get(
        f"/catalog/{api_id}/operations",
        params={"tag": "Users", "offset": 0, "limit": 10},
    )
    body = resp.json()
    assert body["total"] == 30
    assert body["limit"] == 10
    assert body["offset"] == 0
    assert len(body["data"]) == 10
    assert body["truncated"] is True

    # Second page of the same filter — `total` stays the same, window shrinks.
    resp2 = admin_client.get(
        f"/catalog/{api_id}/operations",
        params={"tag": "Users", "offset": 25, "limit": 10},
    )
    body2 = resp2.json()
    assert body2["total"] == 30
    assert len(body2["data"]) == 5  # 30 - 25 left
    assert body2["truncated"] is False


# ── Workspace operations endpoint ────────────────────────────────────────────


_WORKSPACE_API_ID = "p9-pagination-mock.test.local"


@pytest.fixture(scope="module")
def _seeded_workspace_for_pagination(admin_client):
    """Seed a workspace API with 60 operations split between Users/Posts
    tags so we can mirror the catalog assertions on `/apis/.../operations`."""
    spec = _build_large_spec(60)
    spec["info"]["title"] = _WORKSPACE_API_ID
    spec["servers"] = [{"url": f"https://{_WORKSPACE_API_ID}"}]
    resp = admin_client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(spec),
                    "filename": f"{_WORKSPACE_API_ID}.json",
                }
            ]
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return _WORKSPACE_API_ID


def test_workspace_operations_offset_pagination(admin_client, _seeded_workspace_for_pagination):
    api_id = _seeded_workspace_for_pagination
    r1 = admin_client.get(f"/apis/{api_id}/operations", params={"offset": 0, "limit": 25})
    assert r1.status_code == 200, r1.text
    page1 = r1.json()
    assert page1["total"] == 60
    assert page1["limit"] == 25
    assert page1["offset"] == 0
    assert len(page1["data"]) == 25
    assert page1["truncated"] is True

    r2 = admin_client.get(f"/apis/{api_id}/operations", params={"offset": 25, "limit": 25})
    page2 = r2.json()
    assert page2["offset"] == 25
    assert len(page2["data"]) == 25
    p1_ids = {o["id"] for o in page1["data"]}
    p2_ids = {o["id"] for o in page2["data"]}
    assert p1_ids.isdisjoint(p2_ids)


def test_workspace_operations_tag_narrowing(admin_client, _seeded_workspace_for_pagination):
    api_id = _seeded_workspace_for_pagination
    resp = admin_client.get(f"/apis/{api_id}/operations", params={"tag": "Users"})
    body = resp.json()
    assert body["total"] == 30
    for op in body["data"]:
        # Every op must carry the projected `tags` list and contain the
        # filtered tag (case-insensitive substring).
        assert "tags" in op, op
        assert any("user" in t.lower() for t in op["tags"]), op


def test_workspace_operations_total_reflects_post_tag_filter(
    admin_client, _seeded_workspace_for_pagination
):
    api_id = _seeded_workspace_for_pagination
    resp = admin_client.get(
        f"/apis/{api_id}/operations",
        params={"tag": "users", "offset": 0, "limit": 10},
    )
    body = resp.json()
    assert body["total"] == 30
    assert len(body["data"]) == 10
    assert body["truncated"] is True

    resp2 = admin_client.get(
        f"/apis/{api_id}/operations",
        params={"tag": "users", "offset": 25, "limit": 10},
    )
    body2 = resp2.json()
    assert body2["total"] == 30
    assert len(body2["data"]) == 5
    assert body2["truncated"] is False
