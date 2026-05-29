"""Tests for `GET /catalog/{api_id}/workflows` — directory workflow preview.

Mirrors `test_operations_pagination.py` for the workflow side. The endpoint
fetches a vendor's `workflows.arazzo.json` from GitHub (here mocked) and
projects each workflow into a small UI-friendly shape:
`{workflow_id, slug, summary, description, steps_count}`.

Why these tests exist:
- The slug projection has to match what `lazy_import_catalog_workflows`
  computes at import time — otherwise the UI's pre-import deep link to
  `/workspace/workflows/<slug>` would 404 after import.
- The vendor fallback (`api.stripe.com` → `stripe.com`) is the same
  fallback that exists on the import path; the preview is useless if it
  silently disagrees with the actual import.
- Empty / 404 cases need to return `{data: [], total: 0}` rather than
  raising 404 so the API Detail Sheet always renders cleanly.
"""

import json
from unittest.mock import patch

import pytest
from src.routers import catalog as catalog_router


_API_ID = "stripe-mock.test.local"


def _build_arazzo(workflow_ids: list[str]) -> dict:
    """Synthetic Arazzo doc with N workflows. Uses just enough fields to
    exercise the projection — `workflowId`, `summary`, `description`,
    `steps`. Every workflow gets two steps so `steps_count` is non-zero
    and easy to assert on."""
    return {
        "arazzo": "1.0.0",
        "info": {"title": f"{_API_ID} workflows", "version": "1.0.0"},
        "sourceDescriptions": [
            {"name": "src", "url": f"https://{_API_ID}/openapi.json", "type": "openapi"}
        ],
        "workflows": [
            {
                "workflowId": wf_id,
                "summary": f"summary for {wf_id}",
                "description": f"description for {wf_id}",
                "steps": [
                    {"stepId": "step-1", "operationId": "op_1"},
                    {"stepId": "step-2", "operationId": "op_2"},
                ],
            }
            for wf_id in workflow_ids
        ],
    }


@pytest.fixture
def _mocked_arazzo(monkeypatch, tmp_path):
    """Plant a workflow manifest entry for `_API_ID` and intercept
    `urllib.request.urlopen` so the Arazzo fetch is deterministic."""
    wf_manifest = tmp_path / "workflow_manifest.json"
    wf_manifest.write_text(
        json.dumps(
            [
                {
                    "api_id": _API_ID,
                    "source_id": _API_ID,
                    "path": f"workflows/{_API_ID}",
                }
            ]
        )
    )
    monkeypatch.setattr(catalog_router, "WORKFLOW_MANIFEST_PATH", wf_manifest)

    arazzo_bytes = json.dumps(
        _build_arazzo(
            [
                "Process Payment Intent",
                "refund-payment",
                "create_customer_subscription",
            ]
        )
    ).encode("utf-8")

    class _FakeResp:
        def __init__(self, body: bytes):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(*_args, **_kwargs):
        return _FakeResp(arazzo_bytes)

    with patch("urllib.request.urlopen", _fake_urlopen):
        yield _API_ID


# ── Happy path ────────────────────────────────────────────────────────────────


def test_preview_returns_one_row_per_workflow(admin_client, _mocked_arazzo):
    api_id = _mocked_arazzo
    r = admin_client.get(f"/catalog/{api_id}/workflows")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["api_id"] == api_id
    assert body["total"] == 3
    assert len(body["data"]) == 3
    # Order is preserved from the Arazzo file — clients rely on this for
    # stable display order across reopens.
    assert [wf["workflow_id"] for wf in body["data"]] == [
        "Process Payment Intent",
        "refund-payment",
        "create_customer_subscription",
    ]
    # Both URLs are filled in so the UI can render a "View on GitHub" link
    # and (eventually) a debug link to the raw Arazzo doc.
    assert body["arazzo_url"].endswith("/workflows.arazzo.json")
    assert body["github_url"].startswith("https://github.com/")


def test_preview_slug_matches_lazy_import_slug(admin_client, _mocked_arazzo):
    """Slug projection must match `lazy_import_catalog_workflows` byte-for-byte.

    Without this guarantee the UI's pre-import deep link
    (`/workspace/workflows/<slug>`) would 404 after the user imports —
    the post-import row would have a different slug and the link would
    silently break.
    """
    api_id = _mocked_arazzo
    r = admin_client.get(f"/catalog/{api_id}/workflows")
    rows = {wf["workflow_id"]: wf["slug"] for wf in r.json()["data"]}

    # Spaces → dashes, lowercased, collapsed runs of dashes.
    assert rows["Process Payment Intent"] == "process-payment-intent"
    # Already kebab-case stays as-is.
    assert rows["refund-payment"] == "refund-payment"
    # Underscores are not in the slug allowlist — they get rewritten to
    # dashes and collapsed so the output stays a single hyphenated word.
    assert rows["create_customer_subscription"] == "create-customer-subscription"


def test_preview_includes_steps_count(admin_client, _mocked_arazzo):
    api_id = _mocked_arazzo
    rows = admin_client.get(f"/catalog/{api_id}/workflows").json()["data"]
    # Every workflow in `_build_arazzo` carries two steps.
    assert all(wf["steps_count"] == 2 for wf in rows)


# ── Empty / fallback paths ────────────────────────────────────────────────────


def test_preview_returns_empty_for_unknown_api(admin_client, _mocked_arazzo):
    """An api_id with no workflow manifest entry returns an empty
    envelope (not 404) — the API Detail Sheet always asks, sometimes the
    answer is just "none". Branching the UI on 404 vs `{data: []}` is
    needless complexity for a perfectly normal case."""
    r = admin_client.get("/catalog/no-workflows-here.test.local/workflows")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["data"] == []
    assert body["arazzo_url"] is None
    assert body["github_url"] is None


def test_preview_vendor_fallback_finds_subdomain_apis(monkeypatch, tmp_path, admin_client):
    """`api.stripe.com` should find the `stripe.com` workflow bundle —
    same fallback as `lazy_import_catalog_workflows`. Without this the
    preview would falsely report zero workflows for subdomain-keyed APIs
    while the *actual* import (triggered by `POST /credentials`) would
    successfully import some, which is the worst kind of inconsistency."""
    # `extract_vendor` keeps the last two dotted segments
    # (`api.stripemock.com` → `stripemock.com`). The fixture has to use
    # a 3-segment subdomain so the fallback has somewhere to land — a
    # 4-segment id like `api.stripe.test.local` would fall back to
    # `test.local`, not the vendor we seeded.
    wf_manifest = tmp_path / "workflow_manifest.json"
    wf_manifest.write_text(
        json.dumps(
            [
                {
                    "api_id": "stripemock.com",
                    "source_id": "stripemock.com",
                    "path": "workflows/stripemock.com",
                }
            ]
        )
    )
    # An API manifest is required only because `extract_vendor` is
    # spec-aware; we don't actually hit it here. Plant one defensively.
    api_manifest = tmp_path / "catalog_manifest.json"
    api_manifest.write_text("[]")
    monkeypatch.setattr(catalog_router, "WORKFLOW_MANIFEST_PATH", wf_manifest)
    monkeypatch.setattr(catalog_router, "CATALOG_MANIFEST_PATH", api_manifest)

    arazzo_bytes = json.dumps(_build_arazzo(["one"])).encode("utf-8")

    class _FakeResp:
        def read(self):
            return arazzo_bytes

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    with patch("urllib.request.urlopen", lambda *_a, **_k: _FakeResp()):
        # `api.stripemock.com` is the subdomain — falls back to the
        # vendor `stripemock.com` entry via `extract_vendor`.
        r = admin_client.get("/catalog/api.stripemock.com/workflows")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["data"][0]["workflow_id"] == "one"
