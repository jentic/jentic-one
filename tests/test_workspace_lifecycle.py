"""Workspace lifecycle tests — import + delete for APIs and workflows.

Covers the full add/remove cycle:
  1. Import an API via POST /import (inline spec)
  2. Verify the API, its operations, and auto-imported workflow appear
  3. Delete a workflow via DELETE /workflows/{slug}
  4. Delete the API via DELETE /apis/{api_id}
  5. Verify cascaded cleanup (operations, workflows, disk artifacts)

Also covers credential lifecycle in the context of API deletion:
  - Credentials survive API removal (api_id nulled, credential preserved)
"""

import json


# ── Fixtures ─────────────────────────────────────────────────────────────────

SAMPLE_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Test Lifecycle API", "version": "1.0.0"},
    "servers": [{"url": "https://api.lifecycle-test.example.com"}],
    "components": {
        "securitySchemes": {"ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-Api-Key"}}
    },
    "security": [{"ApiKeyAuth": []}],
    "paths": {
        "/items": {
            "get": {
                "operationId": "listItems",
                "summary": "List all items",
                "responses": {"200": {"description": "OK"}},
            },
            "post": {
                "operationId": "createItem",
                "summary": "Create an item",
                "responses": {"201": {"description": "Created"}},
            },
        },
        "/items/{id}": {
            "get": {
                "operationId": "getItem",
                "summary": "Get a single item",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "responses": {"200": {"description": "OK"}},
            },
            "delete": {
                "operationId": "deleteItem",
                "summary": "Delete an item",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "responses": {"204": {"description": "Deleted"}},
            },
        },
    },
}

SAMPLE_ARAZZO = {
    "arazzo": "1.0.0",
    "info": {
        "title": "Lifecycle Test Workflow",
        "version": "1.0.0",
        "description": "E2E test workflow",
    },
    "sourceDescriptions": [
        {"name": "lifecycle-api", "type": "openapi", "url": "./lifecycle_test_openapi.json"}
    ],
    "workflows": [
        {
            "workflowId": "lifecycle-test-wf",
            "summary": "Create and retrieve an item",
            "steps": [
                {
                    "stepId": "create",
                    "operationId": "lifecycle-api.createItem",
                    "parameters": [],
                },
                {
                    "stepId": "retrieve",
                    "operationId": "lifecycle-api.getItem",
                    "parameters": [{"name": "id", "in": "path", "value": "test-id"}],
                },
            ],
        }
    ],
}

API_ID = "lifecycle-test.example.com"


# ── Import Tests ─────────────────────────────────────────────────────────────


def test_import_api(admin_client):
    """POST /import with an inline OpenAPI spec registers the API and its operations."""
    resp = admin_client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(SAMPLE_SPEC),
                    "filename": "lifecycle_test_openapi.json",
                    "force_api_id": API_ID,
                }
            ]
        },
    )
    assert resp.status_code == 200, f"Import failed: {resp.text}"
    data = resp.json()
    assert data["succeeded"] >= 1
    assert data["failed"] == 0


def test_api_appears_in_list(admin_client):
    """The imported API appears in GET /apis."""
    resp = admin_client.get("/apis?source=local")
    assert resp.status_code == 200
    apis = resp.json()
    ids = [a["id"] for a in apis["data"]]
    assert API_ID in ids


def test_api_has_operations(admin_client):
    """The imported API has the expected number of operations."""
    resp = admin_client.get(f"/apis/{API_ID}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["operation_count"] == 4


def test_import_workflow(admin_client):
    """POST /import with an inline Arazzo doc registers the workflow."""
    resp = admin_client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(SAMPLE_ARAZZO),
                    "filename": "lifecycle_test_workflow.json",
                }
            ]
        },
    )
    assert resp.status_code == 200, f"Workflow import failed: {resp.text}"
    data = resp.json()
    assert data["succeeded"] >= 1
    assert data["failed"] == 0


def test_workflow_appears_in_list(admin_client):
    """The imported workflow appears in GET /workflows."""
    resp = admin_client.get("/workflows")
    assert resp.status_code == 200
    workflows = resp.json()
    slugs = [w["slug"] for w in workflows if w.get("source") == "local"]
    assert "lifecycle-test-wf" in slugs


def test_workflow_detail(admin_client):
    """GET /workflows/{slug} returns the workflow definition."""
    resp = admin_client.get("/workflows/lifecycle-test-wf")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Create and retrieve an item"
    assert data["steps_count"] == 2


# ── Credential Lifecycle (API deletion preserves credentials) ────────────────


def test_create_credential_for_api(admin_client):
    """Create a credential bound to the test API."""
    resp = admin_client.post(
        "/credentials",
        json={
            "label": "lifecycle-test-cred",
            "value": "test-secret-123",
            "api_id": API_ID,
            "auth_type": "apiKey",
        },
    )
    assert resp.status_code in (200, 201), f"Credential creation failed: {resp.text}"


def test_credential_bound_to_api(admin_client):
    """The credential has api_id set."""
    resp = admin_client.get("/credentials")
    assert resp.status_code == 200
    creds = resp.json()
    cred = next((c for c in creds if c.get("label") == "lifecycle-test-cred"), None)
    assert cred is not None
    assert cred["api_id"] == API_ID


# ── Delete Workflow ──────────────────────────────────────────────────────────


def test_delete_workflow(admin_client):
    """DELETE /workflows/{slug} removes the workflow."""
    resp = admin_client.delete("/workflows/lifecycle-test-wf")
    assert resp.status_code == 204


def test_workflow_gone_after_delete(admin_client):
    """The workflow no longer appears in the list or by direct GET."""
    resp = admin_client.get("/workflows/lifecycle-test-wf")
    assert resp.status_code == 404

    resp = admin_client.get("/workflows")
    assert resp.status_code == 200
    slugs = [w["slug"] for w in resp.json() if w.get("source") == "local"]
    assert "lifecycle-test-wf" not in slugs


# ── Delete API ───────────────────────────────────────────────────────────────


def test_delete_api(admin_client):
    """DELETE /apis/{api_id} removes the API."""
    resp = admin_client.delete(f"/apis/{API_ID}")
    assert resp.status_code == 204


def test_api_gone_after_delete(admin_client):
    """The API no longer appears in the list."""
    resp = admin_client.get("/apis?source=local")
    assert resp.status_code == 200
    ids = [a["id"] for a in resp.json()["data"]]
    assert API_ID not in ids


def test_api_detail_404_after_delete(admin_client):
    """Direct GET returns 404 for the deleted API."""
    resp = admin_client.get(f"/apis/{API_ID}")
    assert resp.status_code == 404


def test_credential_survives_api_delete(admin_client):
    """Credentials are preserved after API deletion (api_id kept for re-import)."""
    resp = admin_client.get("/credentials")
    assert resp.status_code == 200
    creds = resp.json()
    cred = next((c for c in creds if c.get("label") == "lifecycle-test-cred"), None)
    assert cred is not None, "Credential should survive API deletion"
    assert cred.get("api_id") == API_ID, (
        f"Credential api_id should be preserved for re-import, got: {cred.get('api_id')}"
    )


# ── Delete API (idempotent 404) ──────────────────────────────────────────────


def test_delete_api_again_404(admin_client):
    """Deleting an already-deleted API returns 404."""
    resp = admin_client.delete(f"/apis/{API_ID}")
    assert resp.status_code == 404


def test_delete_workflow_again_404(admin_client):
    """Deleting an already-deleted workflow returns 404."""
    resp = admin_client.delete("/workflows/lifecycle-test-wf")
    assert resp.status_code == 404


# ── Cleanup credential ───────────────────────────────────────────────────────


def test_cleanup_credential(admin_client):
    """Clean up the test credential."""
    resp = admin_client.get("/credentials")
    assert resp.status_code == 200
    cred = next((c for c in resp.json() if c.get("label") == "lifecycle-test-cred"), None)
    assert cred is not None, "Credential should exist for cleanup"
    resp = admin_client.delete(f"/credentials/{cred['id']}")
    assert resp.status_code == 204


# ── Toolkit Cascade Test ─────────────────────────────────────────────────────


def test_toolkit_delete_cascades(admin_client):
    """Creating and deleting a toolkit properly cascades to keys and bindings."""
    # Create toolkit
    resp = admin_client.post("/toolkits", json={"name": "Toolkit Delete Cascade Test"})
    assert resp.status_code in (200, 201), f"Toolkit creation failed: {resp.text}"
    toolkit_id = resp.json()["id"]

    # Create a key for it
    resp = admin_client.post(f"/toolkits/{toolkit_id}/keys", json={"label": "cascade-key"})
    assert resp.status_code in (200, 201), f"Key creation failed: {resp.text}"

    # Verify key exists
    resp = admin_client.get(f"/toolkits/{toolkit_id}/keys")
    assert resp.status_code == 200
    assert len(resp.json()["keys"]) >= 1

    # Delete toolkit
    resp = admin_client.delete(f"/toolkits/{toolkit_id}")
    assert resp.status_code == 204

    # Verify toolkit is gone
    resp = admin_client.get(f"/toolkits/{toolkit_id}")
    assert resp.status_code == 404


def test_default_toolkit_cannot_be_deleted(admin_client):
    """The default toolkit rejects deletion with 403."""
    resp = admin_client.delete("/toolkits/default")
    assert resp.status_code == 403
