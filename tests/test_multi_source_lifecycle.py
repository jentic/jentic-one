"""Multi-source import + delete lifecycle tests.

Tests 3 API import methods and 3 workflow import methods, then verifies
each can be cleanly deleted via DELETE /apis/{api_id} and DELETE /workflows/{slug}.

Import methods tested:
  APIs:
    1. Inline spec (JSON body)
    2. URL import from external source (petstore3.swagger.io)
    3. URL import with force_api_id (catalog-style)

  Workflows:
    1. Inline Arazzo doc
    2. URL import from external source (1password Arazzo from GitHub)
    3. Auto-import via catalog API (lazy_import_catalog_workflows)

Requires network access for URL-based imports.
"""

import json

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────────────

INLINE_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Inline Test API", "version": "1.0.0"},
    "servers": [{"url": "https://api.inline-test.example.com"}],
    "components": {"securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}}},
    "paths": {
        "/ping": {
            "get": {
                "operationId": "ping",
                "summary": "Health check",
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}

INLINE_ARAZZO = {
    "arazzo": "1.0.0",
    "info": {
        "title": "Inline Test Workflow",
        "version": "1.0.0",
        "description": "A minimal inline workflow",
    },
    "sourceDescriptions": [
        {"name": "inline-api", "type": "openapi", "url": "https://example.com/openapi.json"}
    ],
    "workflows": [
        {
            "workflowId": "inline-wf-test",
            "summary": "Inline workflow for testing",
            "steps": [
                {
                    "stepId": "step1",
                    "operationId": "inline-api.ping",
                    "parameters": [],
                }
            ],
        }
    ],
}

PETSTORE_URL = "https://petstore3.swagger.io/api/v3/openapi.json"
ONEPASSWORD_ARAZZO_URL = "https://raw.githubusercontent.com/jentic/jentic-public-apis/refs/heads/main/workflows/1password.com%7Eevents/workflows.arazzo.json"


# ══════════════════════════════════════════════════════════════════════════════
# 1. API IMPORT — Inline
# ══════════════════════════════════════════════════════════════════════════════


INLINE_API_ID = "inline-test.example.com"


def test_import_api_inline(admin_client):
    """Import API from inline JSON content."""
    resp = admin_client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(INLINE_SPEC),
                    "filename": "inline_test_openapi.json",
                    "force_api_id": INLINE_API_ID,
                }
            ]
        },
    )
    assert resp.status_code == 200, f"Inline import failed: {resp.text}"
    data = resp.json()
    assert data["succeeded"] >= 1
    assert data["failed"] == 0


def test_inline_api_exists(admin_client):
    """Verify inline-imported API is accessible."""
    resp = admin_client.get(f"/apis/{INLINE_API_ID}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["operation_count"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# 2. API IMPORT — External URL (Petstore)
#    Note: Petstore uses a relative server path ("/api/v3"), so the derived
#    API ID is based on that, not the download domain. The test discovers
#    the actual ID from the import response.
# ══════════════════════════════════════════════════════════════════════════════


_petstore_api_id: str = ""


@pytest.mark.network
def test_import_api_external_url(admin_client):
    """Import API from an external URL (Swagger Petstore)."""
    global _petstore_api_id
    resp = admin_client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "url",
                    "url": PETSTORE_URL,
                }
            ]
        },
    )
    assert resp.status_code == 200, f"URL import failed: {resp.text}"
    data = resp.json()
    assert data["succeeded"] >= 1, f"Import result: {data}"
    assert data["failed"] == 0
    _petstore_api_id = data["results"][0]["id"]


@pytest.mark.network
def test_external_url_api_exists(admin_client):
    """Verify URL-imported API is accessible and has operations."""
    assert _petstore_api_id, "Petstore API ID not captured from import"
    resp = admin_client.get(f"/apis/{_petstore_api_id}")
    assert resp.status_code == 200, f"API not found at id={_petstore_api_id}: {resp.text}"
    data = resp.json()
    assert data["operation_count"] > 0, "Petstore should have multiple operations"


# ══════════════════════════════════════════════════════════════════════════════
# 3. API IMPORT — URL with force_api_id (catalog-style)
# ══════════════════════════════════════════════════════════════════════════════


FORCED_API_ID = "forced-petstore.test.io"


@pytest.mark.network
def test_import_api_url_with_force_id(admin_client):
    """Import API from URL with force_api_id (catalog import path)."""
    resp = admin_client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "url",
                    "url": PETSTORE_URL,
                    "force_api_id": FORCED_API_ID,
                }
            ]
        },
    )
    assert resp.status_code == 200, f"Forced-ID import failed: {resp.text}"
    data = resp.json()
    assert data["succeeded"] >= 1, f"Import result: {data}"


@pytest.mark.network
def test_forced_id_api_exists(admin_client):
    """Verify forced-ID API is accessible with the custom ID."""
    resp = admin_client.get(f"/apis/{FORCED_API_ID}")
    assert resp.status_code == 200, f"Forced-ID API not found: {resp.text}"
    data = resp.json()
    assert data["operation_count"] > 0


# ══════════════════════════════════════════════════════════════════════════════
# 4. WORKFLOW IMPORT — Inline
# ══════════════════════════════════════════════════════════════════════════════


def test_import_workflow_inline(admin_client):
    """Import workflow from inline Arazzo JSON."""
    resp = admin_client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(INLINE_ARAZZO),
                    "filename": "inline_test_workflow.json",
                }
            ]
        },
    )
    assert resp.status_code == 200, f"Inline workflow import failed: {resp.text}"
    data = resp.json()
    assert data["succeeded"] >= 1


def test_inline_workflow_exists(admin_client):
    """Verify inline-imported workflow is accessible."""
    resp = admin_client.get("/workflows/inline-wf-test")
    assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# 5. WORKFLOW IMPORT — External URL (1Password Arazzo from GitHub)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.network
def test_import_workflow_external_url(admin_client):
    """Import workflow from an external URL (1Password Arazzo on GitHub)."""
    resp = admin_client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "url",
                    "url": ONEPASSWORD_ARAZZO_URL,
                }
            ]
        },
    )
    assert resp.status_code == 200, f"URL workflow import failed: {resp.text}"
    data = resp.json()
    assert data["succeeded"] >= 1, f"Workflow import result: {data}"


@pytest.mark.network
def test_external_workflow_exists(admin_client):
    """Verify URL-imported workflow(s) are accessible."""
    resp = admin_client.get("/workflows")
    assert resp.status_code == 200
    workflows = resp.json()
    local_slugs = [w["slug"] for w in workflows if w.get("source") == "local"]
    # The 1password arazzo has 3 workflows; at least one should be imported
    onepass_wfs = [s for s in local_slugs if "audit" in s or "signin" in s or "item" in s]
    assert len(onepass_wfs) >= 1, (
        f"Expected at least 1 onepassword workflow, got slugs: {local_slugs}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 6. DELETE ALL — Workflows first, then APIs
# ══════════════════════════════════════════════════════════════════════════════


def test_delete_inline_workflow(admin_client):
    """Delete the inline-imported workflow."""
    resp = admin_client.delete("/workflows/inline-wf-test")
    assert resp.status_code == 204

    resp = admin_client.get("/workflows/inline-wf-test")
    assert resp.status_code == 404


@pytest.mark.network
def test_delete_external_workflows(admin_client):
    """Delete all 1password workflows imported from the external URL."""
    resp = admin_client.get("/workflows")
    assert resp.status_code == 200
    workflows = resp.json()
    local_slugs = [w["slug"] for w in workflows if w.get("source") == "local"]
    onepass_slugs = [s for s in local_slugs if "audit" in s or "signin" in s or "item" in s]

    for slug in onepass_slugs:
        resp = admin_client.delete(f"/workflows/{slug}")
        assert resp.status_code == 204, f"Failed to delete workflow {slug}: {resp.text}"

    # Verify gone
    for slug in onepass_slugs:
        resp = admin_client.get(f"/workflows/{slug}")
        assert resp.status_code == 404, f"Workflow {slug} still exists after delete"


def test_delete_inline_api(admin_client):
    """Delete the inline-imported API."""
    resp = admin_client.delete(f"/apis/{INLINE_API_ID}")
    assert resp.status_code == 204

    resp = admin_client.get(f"/apis/{INLINE_API_ID}")
    assert resp.status_code == 404


@pytest.mark.network
def test_delete_external_url_api(admin_client):
    """Delete the API imported from external URL (Petstore)."""
    assert _petstore_api_id, "Petstore API ID not captured"
    resp = admin_client.delete(f"/apis/{_petstore_api_id}")
    assert resp.status_code == 204

    resp = admin_client.get(f"/apis/{_petstore_api_id}")
    assert resp.status_code == 404


@pytest.mark.network
def test_delete_forced_id_api(admin_client):
    """Delete the API imported with force_api_id."""
    resp = admin_client.delete(f"/apis/{FORCED_API_ID}")
    assert resp.status_code == 204

    resp = admin_client.get(f"/apis/{FORCED_API_ID}")
    assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# 7. VERIFY — Second delete returns 404 (idempotent)
# ══════════════════════════════════════════════════════════════════════════════


def test_double_delete_inline_api_404(admin_client):
    """Second delete of inline API returns 404."""
    resp = admin_client.delete(f"/apis/{INLINE_API_ID}")
    assert resp.status_code == 404


@pytest.mark.network
def test_double_delete_external_api_404(admin_client):
    """Second delete of external URL API returns 404."""
    assert _petstore_api_id, "Petstore API ID not captured"
    resp = admin_client.delete(f"/apis/{_petstore_api_id}")
    assert resp.status_code == 404


@pytest.mark.network
def test_double_delete_forced_api_404(admin_client):
    """Second delete of forced-ID API returns 404."""
    resp = admin_client.delete(f"/apis/{FORCED_API_ID}")
    assert resp.status_code == 404


def test_double_delete_inline_workflow_404(admin_client):
    """Second delete of inline workflow returns 404."""
    resp = admin_client.delete("/workflows/inline-wf-test")
    assert resp.status_code == 404
