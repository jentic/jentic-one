"""Tests for the optional pagination on `GET /workflows`.

The endpoint historically returned a bare array; many consumers (stats
strip, sheet body, api-detail-view, dashboard) still depend on that
shape. The workspace grid now opts in to a `{data, total, page, limit,
total_pages}` envelope by passing `page`/`limit` so it can fan out
across pages instead of dumping the entire workflow table on every
load.

These tests pin both shapes — drop them and the workspace grid will
silently break (or worse, regress to the unpaginated payload that
motivated the fan-out in the first place).
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def imported_workflow(admin_client):
    """Import the canonical Arazzo fixture so the workflow table is non-empty."""
    workflow_path = Path(__file__).parent / "fixtures" / "test-workflow.arazzo.json"
    assert workflow_path.exists(), f"Test workflow fixture not found: {workflow_path}"
    resp = admin_client.post(
        "/import",
        json={"sources": [{"type": "path", "path": str(workflow_path)}]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_list_workflows_returns_bare_array_without_pagination(admin_client, imported_workflow):
    """No `page`/`limit` → historical bare-array response.

    Locks in backward compatibility for every consumer that still calls
    `GET /workflows` without pagination params.
    """
    resp = admin_client.get("/workflows")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) > 0


def test_list_workflows_returns_envelope_when_paginated(admin_client, imported_workflow):
    """Either pagination param flips the response into the envelope shape."""
    resp = admin_client.get("/workflows?page=1&limit=20")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict)
    assert set(body.keys()) >= {"data", "total", "page", "limit", "total_pages"}
    assert isinstance(body["data"], list)
    assert body["page"] == 1
    assert body["limit"] == 20
    assert body["total"] >= 1
    assert body["total_pages"] >= 1
    assert len(body["data"]) <= body["limit"]


def test_list_workflows_pagination_slices_by_page(admin_client, imported_workflow):
    """`limit=1` must produce one row per page and a stable total across pages."""
    page1 = admin_client.get("/workflows?page=1&limit=1&source=local").json()
    assert page1["limit"] == 1
    assert len(page1["data"]) <= 1
    total = page1["total"]
    pages = page1["total_pages"]
    assert pages == max(1, total)

    if pages > 1:
        page2 = admin_client.get("/workflows?page=2&limit=1&source=local").json()
        assert page2["page"] == 2
        assert page2["total"] == total
        # Different page → different row (rows are ordered by created_at DESC,
        # and the SQLite ordering is stable for distinct timestamps; the
        # fixture import always produces a single row, but if more rows
        # exist they must not be re-emitted on page 2).
        if page1["data"] and page2["data"]:
            assert page1["data"][0]["slug"] != page2["data"][0]["slug"]


def test_list_workflows_envelope_with_only_limit(admin_client, imported_workflow):
    """Just `limit` (no `page`) is enough to opt in — defaults `page=1`."""
    resp = admin_client.get("/workflows?limit=5")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict)
    assert body["page"] == 1
    assert body["limit"] == 5
