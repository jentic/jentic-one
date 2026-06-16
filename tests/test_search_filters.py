"""P5 — `/search` filter+search composition (source / type narrowing).

These tests pin the new query params (`source`, `type`) and the legacy
synonyms (`local`, `catalog`). They seed:

- one workspace API via `/import` so BM25 has a real `operation` row to
  return for the query "stripe-charge".
- a fake catalog manifest + workflow manifest pointing at a synthetic
  Stripe-like vendor so the catalog blender produces `catalog_api`
  rows (with `has_workflows: true`) for the same query.

The filtering rules under test are documented in
`discover-polish-plan.md` § P5; this file is the executable companion.
"""

import json

import pytest
from src.routers import catalog as catalog_router


_API_ID = "stripe-charge.test.local"


@pytest.fixture(autouse=True)
def _seed_catalog_manifests(monkeypatch, tmp_path):
    """Point the catalog/workflow manifest loaders at temp files we own.

    Both loaders read straight off disk — pointing them at temp paths is
    cleaner than monkeypatching the loader functions because it also
    exercises the real JSON parse path. The loaders run inside the
    request handler so the env tweak via `monkeypatch.setattr` on the
    module-level constants takes effect immediately.
    """
    api_manifest = tmp_path / "catalog_manifest.json"
    api_manifest.write_text(
        json.dumps(
            [
                # Use a vendor that no other test registers as a workspace
                # API. The dedup heuristic in /apis drops a catalog row when a
                # local API already covers its leaf vendor (extract_vendor →
                # last two dot-parts). `.test.io` collided with
                # `forced-petstore.test.io` from test_multi_source_lifecycle in
                # full-suite runs (shared session DB), dropping this row. A
                # dedicated `.sffixture.invalid` vendor is collision-proof.
                {
                    "api_id": "stripe-charge-cat.sffixture.invalid",
                    "path": "apis/openapi/stripe-charge-cat.sffixture.invalid",
                    "spec_url": "https://example.invalid/stripe-charge-cat.json",
                }
            ]
        )
    )
    wf_manifest = tmp_path / "workflow_manifest.json"
    wf_manifest.write_text(
        json.dumps(
            [
                {
                    "api_id": "stripe-charge-cat.sffixture.invalid",
                    "source_id": "stripe-charge-cat.sffixture.invalid",
                    "path": "workflows/stripe-charge-cat.sffixture.invalid",
                }
            ]
        )
    )
    monkeypatch.setattr(catalog_router, "CATALOG_MANIFEST_PATH", api_manifest)
    monkeypatch.setattr(catalog_router, "WORKFLOW_MANIFEST_PATH", wf_manifest)
    yield


@pytest.fixture(scope="module")
def _seeded_workspace_api(admin_client):
    """Register a workspace API once per module so BM25 has an operation
    row to return for our query.

    NOTE: BM25Okapi's IDF goes negative for any term that appears in 100%
    of docs in a tiny corpus, so we seed *several* unrelated APIs to give
    the index enough variance for `score > 0` ranking. Without this,
    `bm25.search("stripe-charge")` would return zero hits even though the
    summary contains the literal token.
    """
    # Seed unrelated noise APIs first so the BM25 corpus has variance.
    for noise_id, op_summary in [
        ("noise-alpha.test.local", "Alpha noise endpoint"),
        ("noise-beta.test.local", "Beta noise endpoint"),
        ("noise-gamma.test.local", "Gamma noise endpoint"),
    ]:
        admin_client.post(
            "/import",
            json={
                "sources": [
                    {
                        "type": "inline",
                        "content": json.dumps(
                            {
                                "openapi": "3.1.0",
                                "info": {"title": noise_id, "version": "1.0.0"},
                                "servers": [{"url": f"https://{noise_id}"}],
                                "paths": {
                                    "/x": {
                                        "get": {
                                            "operationId": f"x_{noise_id}",
                                            "summary": op_summary,
                                            "responses": {"200": {"description": "ok"}},
                                        }
                                    }
                                },
                            }
                        ),
                        "filename": f"{noise_id}.json",
                    }
                ]
            },
        )

    spec = {
        "openapi": "3.1.0",
        "info": {"title": _API_ID, "version": "1.0.0"},
        "servers": [{"url": f"https://{_API_ID}"}],
        "paths": {
            "/charges": {
                "post": {
                    "operationId": "createStripeCharge",
                    "summary": "Create a stripe-charge for the customer",
                    "description": "Creates a stripe-charge against the customer's saved card.",
                    "tags": ["Charges"],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    resp = admin_client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(spec),
                    "filename": f"{_API_ID}.json",
                }
            ]
        },
    )
    assert resp.status_code in (200, 201), f"seed import failed: {resp.text}"
    return _API_ID


def _search(client, **params):
    resp = client.get("/search", params={"q": "stripe-charge", **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── default behaviour (sanity) ────────────────────────────────────────────────


def test_default_returns_mixed_local_and_catalog_rows(admin_client, _seeded_workspace_api):
    rows = _search(admin_client)
    sources = {r.get("source") for r in rows}
    types = {r.get("type") for r in rows}
    assert "local" in sources
    assert "catalog" in sources
    # Both an operation and a catalog API row are expected.
    assert "operation" in types
    assert "catalog_api" in types
    # The catalog row for our seeded vendor must carry has_workflows=True
    # because the workflow manifest seeds an entry for the same api_id.
    seeded = next(
        (
            r
            for r in rows
            if r.get("type") == "catalog_api"
            and r.get("api_id") == "stripe-charge-cat.sffixture.invalid"
        ),
        None,
    )
    assert seeded is not None, "expected a catalog_api row for the seeded vendor"
    assert seeded.get("has_workflows") is True


def test_catalog_workflow_source_rows_are_no_longer_emitted(admin_client, _seeded_workspace_api):
    """Regression guard for the `catalog_workflow_source` collapse.

    The row type was 1:1 with `catalog_api` for the same api_id and is
    now folded into the API row as a `has_workflows` boolean. If this
    assertion fails, the UI's `searchResultToEntity` will start
    receiving phantom rows again.
    """
    rows = _search(admin_client)
    types = {r.get("type") for r in rows}
    assert "catalog_workflow_source" not in types


# ── source filter ─────────────────────────────────────────────────────────────


def test_source_workspace_drops_catalog_rows(admin_client, _seeded_workspace_api):
    rows = _search(admin_client, source="workspace")
    assert rows, "expected at least one workspace row for the seeded query"
    assert all(r.get("source") != "catalog" for r in rows)
    assert all(r.get("type") in ("operation", "workflow") for r in rows)


def test_source_directory_drops_local_rows(admin_client, _seeded_workspace_api):
    rows = _search(admin_client, source="directory")
    assert rows, "expected at least one catalog row for the seeded query"
    assert all(r.get("source") != "local" for r in rows)
    assert all(r.get("type") == "catalog_api" for r in rows)


def test_source_legacy_local_synonym_behaves_like_workspace(admin_client, _seeded_workspace_api):
    rows = _search(admin_client, source="local")
    # Same invariant as `source=workspace` — legacy clients (which
    # predate the rename) keep getting the right slice.
    assert all(r.get("source") != "catalog" for r in rows)


def test_source_legacy_catalog_synonym_behaves_like_directory(admin_client, _seeded_workspace_api):
    rows = _search(admin_client, source="catalog")
    assert all(r.get("source") != "local" for r in rows)


# ── type filter ───────────────────────────────────────────────────────────────


def test_type_endpoint_returns_only_operation_rows(admin_client, _seeded_workspace_api):
    rows = _search(admin_client, type="endpoint")
    assert rows, "expected at least one endpoint row for the seeded query"
    assert all(r.get("type") == "operation" for r in rows)


def test_type_workflow_returns_workspace_workflows_and_workflow_bearing_catalog_apis(
    admin_client, _seeded_workspace_api
):
    """`type=workflow` semantics after the `catalog_workflow_source` collapse.

    Allowed:
    - workspace `workflow` rows (genuine BM25 hits in the local index).
    - `catalog_api` rows for vendors that ship workflows in the public
      catalog (`has_workflows: true`).

    Disallowed:
    - `operation` rows (workspace endpoints).
    - `catalog_api` rows for vendors WITHOUT workflows — `type=workflow`
      is a "show me workflow-bearing things" filter on the directory side.
    - `catalog_workflow_source` (no longer exists).
    """
    rows = _search(admin_client, type="workflow")
    for r in rows:
        if r.get("type") == "workflow":
            continue
        if r.get("type") == "catalog_api":
            assert r.get("has_workflows") is True, (
                f"type=workflow leaked a catalog_api without workflows: {r}"
            )
            continue
        pytest.fail(f"unexpected row type for type=workflow: {r.get('type')!r} (row={r})")


def test_type_api_returns_only_catalog_api_rows(admin_client, _seeded_workspace_api):
    rows = _search(admin_client, type="api")
    assert rows, "expected at least one catalog_api row for the seeded query"
    assert all(r.get("type") == "catalog_api" for r in rows)


# ── unknown/empty values are tolerated ────────────────────────────────────────


def test_unknown_source_falls_back_to_all(admin_client, _seeded_workspace_api):
    rows = _search(admin_client, source="bogus")
    sources = {r.get("source") for r in rows}
    # Unknown values must not 4xx — they collapse to the default ("all").
    assert "local" in sources or "catalog" in sources


# ── /apis carries has_workflows for catalog rows ─────────────────────────────


def test_apis_endpoint_carries_has_workflows_for_catalog_rows(admin_client, _seeded_workspace_api):
    """`/apis` blends catalog rows with the workflow manifest.

    The directory browse grid renders the `+ workflows` chip from the
    `has_workflows` field on each row. Originally only the `/search`
    blender populated it, so the chip was visible on search results
    but invisible in browse mode — a regression that hid the existence
    of the API-detail-sheet workflow section. This test pins the
    contract so the field can't silently disappear from `/apis` again.

    `_seed_catalog_manifests` (autouse) provides:
      - one catalog API: `stripe-charge-cat.sffixture.invalid`
      - one matching workflow manifest entry for the same `api_id`

    Local rows must NOT carry the field — workspace workflows are
    already first-class entities listed under `GET /workflows` and
    don't need the chip.

    NOTE: `/apis` orders local rows before catalog rows and paginates
    (default limit 20). In a full-suite run the shared session DB holds
    far more than 20 workspace APIs, which would push the seeded catalog
    row off page 1 of the unfiltered listing. We therefore query each
    source explicitly with a high limit so the assertions are
    order-independent — this is a test-isolation concern, not a product
    behaviour we want to pin.
    """
    # Catalog row for the seeded vendor must carry has_workflows=True.
    catalog_resp = admin_client.get("/apis", params={"source": "catalog", "limit": 100})
    assert catalog_resp.status_code == 200, catalog_resp.text
    catalog_rows = catalog_resp.json()["data"]
    seeded = next(
        (r for r in catalog_rows if r.get("id") == "stripe-charge-cat.sffixture.invalid"),
        None,
    )
    assert seeded is not None, "expected the seeded catalog row in /apis?source=catalog"
    assert seeded.get("source") == "catalog"
    assert seeded.get("has_workflows") is True

    # Local rows now also carry has_workflows (from workspace enrichment).
    local_resp = admin_client.get("/apis", params={"source": "local", "limit": 100})
    assert local_resp.status_code == 200, local_resp.text
    local_rows = [r for r in local_resp.json()["data"] if r.get("source") == "local"]
    assert local_rows, "expected at least one workspace API in /apis"
    for r in local_rows:
        assert "has_workflows" in r, f"local row missing has_workflows field: {r}"


# ── /apis include_imported flag ──────────────────────────────────────────────


def test_apis_catalog_excludes_imported_by_default(
    admin_client, _seeded_workspace_api, monkeypatch, tmp_path
):
    """`/apis?source=catalog` must hide manifest entries whose api_id is
    already a registered workspace API — that's the long-standing
    behaviour `/workspace`'s "From the catalog" subsection depends on
    (it's meant to surface *importable* APIs, not the user's existing
    ones).

    Pin it so the new `include_imported=true` opt-in path doesn't
    accidentally become the default and start showing duplicate cards
    on the workspace page.
    """
    # Replace the manifest with an entry whose api_id collides with a
    # workspace API the module fixture already imported. Without
    # `include_imported`, this row should be filtered out.
    manifest = tmp_path / "manifest_imported.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "api_id": _API_ID,  # matches the seeded workspace API
                    "path": f"apis/openapi/{_API_ID}",
                    "spec_url": f"https://example.invalid/{_API_ID}.json",
                }
            ]
        )
    )
    monkeypatch.setattr(catalog_router, "CATALOG_MANIFEST_PATH", manifest)

    resp = admin_client.get("/apis?source=catalog")
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    matching = [r for r in rows if r.get("id") == _API_ID]
    assert matching == [], (
        f"imported catalog rows must NOT appear in the default catalog listing (found: {matching})"
    )


def test_apis_catalog_include_imported_pivots_to_local_row(
    admin_client, _seeded_workspace_api, monkeypatch, tmp_path
):
    """With `include_imported=true`, manifest entries that are already
    registered locally must surface — but as the *local* row (so the
    UI's status pill resolves to Ready/Credential expired), not as a
    duplicate catalog row.

    This is the May 2026 `/discover` behaviour change: imported APIs
    used to vanish from `/discover` after import, which felt like the
    import had failed. The flag preserves them with workspace state
    semantics so users keep their bearings.
    """
    manifest = tmp_path / "manifest_imported.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "api_id": _API_ID,  # matches the seeded workspace API
                    "path": f"apis/openapi/{_API_ID}",
                    "spec_url": f"https://example.invalid/{_API_ID}.json",
                }
            ]
        )
    )
    monkeypatch.setattr(catalog_router, "CATALOG_MANIFEST_PATH", manifest)

    resp = admin_client.get("/apis?source=catalog&include_imported=true")
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    matching = [r for r in rows if r.get("id") == _API_ID]
    assert len(matching) == 1, f"expected exactly one row for the imported api_id, got {matching}"
    pivoted = matching[0]
    # Source flips from catalog→local so the UI renders the Ready /
    # Credential expired pill instead of Available.
    assert pivoted["source"] == "local", pivoted
    # No spec_url / _links on local rows — that's the contract the
    # workspace branch produces.
    assert "spec_url" not in pivoted, pivoted
    # has_credentials is the boolean the workspace fixture's import
    # path leaves alone; assert presence + boolean shape rather than a
    # specific value (the seed doesn't attach credentials).
    assert isinstance(pivoted["has_credentials"], bool), pivoted


def test_apis_catalog_path_style_sibling_is_not_suppressed_by_dedup(
    admin_client, monkeypatch, tmp_path
):
    """Importing a path-style sub-API (e.g. `slack.com/openai`) must NOT
    hide its sibling leaf-vendor catalog row (`slack.com`).

    The two are different specs with disjoint operations — the catalog
    `slack.com` row is the full Slack Web API, the local
    `slack.com/openai` row is the small "Slack AI Plugin" subset. Before
    the May 2027 fix, the cross-vendor dedup harvested `slack.com` from
    the local id's hostname into `covered_leaf_vendors`, which silently
    dropped the leaf catalog row. Symptom in the UI: Discover showed
    `slack.com/openai` with the imported pill but the genuinely-missing
    `slack.com` row was nowhere to be found, leaving the user unable to
    import the full Slack API to back workflows that needed it.

    Pin the desired behaviour: with `include_imported=true`, both rows
    must survive — the imported one as `source=local` and the leaf
    vendor as `source=catalog`.
    """
    sibling_local_id = "slack-test.local/openai"
    sibling_leaf_id = "slack-test.local"

    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Slack AI Plugin (test)", "version": "1.0.0"},
        # Force the api_id below via the import payload — the spec's
        # base_url isn't load-bearing here.
        "servers": [{"url": "https://slack-test.local/openai"}],
        "paths": {
            "/x": {
                "get": {
                    "operationId": "x_slack_test_openai",
                    "summary": "stub",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    resp = admin_client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(spec),
                    "filename": "slack_test_openai.json",
                    "force_api_id": sibling_local_id,
                }
            ]
        },
    )
    assert resp.status_code in (200, 201), resp.text

    try:
        manifest = tmp_path / "manifest_slack_siblings.json"
        manifest.write_text(
            json.dumps(
                [
                    {
                        "api_id": sibling_leaf_id,
                        "path": f"apis/openapi/{sibling_leaf_id}",
                        "spec_url": f"https://example.invalid/{sibling_leaf_id}.json",
                    },
                    {
                        "api_id": sibling_local_id,
                        "path": f"apis/openapi/{sibling_local_id}",
                        "spec_url": f"https://example.invalid/{sibling_local_id}.json",
                    },
                ]
            )
        )
        monkeypatch.setattr(catalog_router, "CATALOG_MANIFEST_PATH", manifest)

        resp = admin_client.get("/apis?source=catalog&include_imported=true")
        assert resp.status_code == 200, resp.text
        rows = resp.json()["data"]
        ids = {r["id"]: r["source"] for r in rows}

        # The imported sub-API surfaces as the local-pivoted row.
        assert ids.get(sibling_local_id) == "local", (
            f"{sibling_local_id} should pivot to local, got {ids}"
        )
        # The leaf-vendor catalog row is genuinely *not* imported and
        # must remain available for the user to import. Before the fix
        # this row was dropped by the leaf-vendor dedup.
        assert ids.get(sibling_leaf_id) == "catalog", (
            f"{sibling_leaf_id} should appear as an importable catalog row, got {ids}"
        )
    finally:
        admin_client.delete(f"/apis/{sibling_local_id}?cascade=true")
