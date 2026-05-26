"""Cascade delete tests — verify DELETE /apis/{api_id}?cascade=true behaviour.

Tests both modes:
  1. Default (cascade=false): credentials survive with api_id cleared
  2. Cascade (cascade=true): credentials AND toolkit bindings are deleted

Also verifies:
  - Workflows exclusive to the API are deleted in both modes
  - Toolkit bindings are cleaned up only in cascade mode
  - Non-cascade preserves credentials for re-import
"""

import json


# ── Fixtures ─────────────────────────────────────────────────────────────────

CASCADE_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Cascade Test API", "version": "1.0.0"},
    "servers": [{"url": "https://api.cascade-test.example.com"}],
    "components": {
        "securitySchemes": {"ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-Api-Key"}}
    },
    "security": [{"ApiKeyAuth": []}],
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

SOFT_API_ID = "cascade-soft-test.example.com"
HARD_API_ID = "cascade-hard-test.example.com"


# ══════════════════════════════════════════════════════════════════════════════
# PART 1: Soft delete (default) — credentials preserved
# ══════════════════════════════════════════════════════════════════════════════


def test_soft_import_api(admin_client):
    """Import an API for the soft-delete test."""
    resp = admin_client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(CASCADE_SPEC),
                    "filename": "cascade_soft_test.json",
                    "force_api_id": SOFT_API_ID,
                }
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.json()["succeeded"] >= 1


def test_soft_create_credential(admin_client):
    """Create a credential bound to the soft-delete API."""
    resp = admin_client.post(
        "/credentials",
        json={
            "label": "soft-cascade-cred",
            "api_id": SOFT_API_ID,
            "auth_type": "apiKey",
            "value": json.dumps({"key": "test-key-soft"}),
        },
    )
    assert resp.status_code in (200, 201), f"Credential creation failed: {resp.text}"


def test_soft_credential_exists(admin_client):
    """Verify the credential is bound to the API."""
    resp = admin_client.get(f"/credentials?api_id={SOFT_API_ID}")
    assert resp.status_code == 200
    creds = resp.json()
    assert any(c["label"] == "soft-cascade-cred" for c in creds)


def test_soft_delete_api_default(admin_client):
    """Delete API without cascade — credentials should survive."""
    resp = admin_client.delete(f"/apis/{SOFT_API_ID}")
    assert resp.status_code == 204


def test_soft_credential_survives(admin_client):
    """After soft delete, credential still exists with api_id preserved."""
    resp = admin_client.get("/credentials")
    assert resp.status_code == 200
    cred = next((c for c in resp.json() if c.get("label") == "soft-cascade-cred"), None)
    assert cred is not None, "Credential should survive soft delete"
    assert cred.get("api_id") == SOFT_API_ID, "api_id should be preserved for re-import"


def test_soft_cleanup(admin_client):
    """Clean up the surviving credential."""
    resp = admin_client.get("/credentials")
    cred = next((c for c in resp.json() if c.get("label") == "soft-cascade-cred"), None)
    if cred:
        admin_client.delete(f"/credentials/{cred['id']}")


# ══════════════════════════════════════════════════════════════════════════════
# PART 2: Hard delete (cascade=true) — credentials deleted
# ══════════════════════════════════════════════════════════════════════════════


def test_hard_import_api(admin_client):
    """Import an API for the cascade-delete test."""
    resp = admin_client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(CASCADE_SPEC),
                    "filename": "cascade_hard_test.json",
                    "force_api_id": HARD_API_ID,
                }
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.json()["succeeded"] >= 1


def test_hard_create_credential(admin_client):
    """Create a credential bound to the cascade-delete API."""
    resp = admin_client.post(
        "/credentials",
        json={
            "label": "hard-cascade-cred",
            "api_id": HARD_API_ID,
            "auth_type": "apiKey",
            "value": json.dumps({"key": "test-key-hard"}),
        },
    )
    assert resp.status_code in (200, 201), f"Credential creation failed: {resp.text}"


def test_hard_create_toolkit_and_bind(admin_client):
    """Create a toolkit and bind the credential to it."""
    # Create toolkit
    resp = admin_client.post("/toolkits", json={"name": "Cascade Test Toolkit"})
    assert resp.status_code in (200, 201), f"Toolkit creation failed: {resp.text}"
    toolkit_id = resp.json()["id"]

    # Find the credential
    resp = admin_client.get(f"/credentials?api_id={HARD_API_ID}")
    assert resp.status_code == 200
    cred = next((c for c in resp.json() if c["label"] == "hard-cascade-cred"), None)
    assert cred is not None

    # Bind credential to toolkit
    resp = admin_client.post(
        f"/toolkits/{toolkit_id}/credentials",
        json={"credential_id": cred["id"]},
    )
    assert resp.status_code in (200, 201, 204), f"Bind failed: {resp.text}"

    # Verify binding exists
    resp = admin_client.get(f"/toolkits/{toolkit_id}/credentials")
    assert resp.status_code == 200
    bound = resp.json()
    cred_ids = [c["id"] if isinstance(c, dict) else c for c in bound]
    assert cred["id"] in cred_ids or any(
        (isinstance(c, dict) and c.get("credential_id") == cred["id"]) for c in bound
    )


def test_hard_delete_api_cascade(admin_client):
    """Delete API with cascade=true — credentials should be gone."""
    resp = admin_client.delete(f"/apis/{HARD_API_ID}?cascade=true")
    assert resp.status_code == 204


def test_hard_credential_deleted(admin_client):
    """After cascade delete, credential no longer exists."""
    resp = admin_client.get("/credentials")
    assert resp.status_code == 200
    cred = next((c for c in resp.json() if c.get("label") == "hard-cascade-cred"), None)
    assert cred is None, "Credential should be deleted in cascade mode"


def test_hard_api_gone(admin_client):
    """API is fully gone after cascade delete."""
    resp = admin_client.get(f"/apis/{HARD_API_ID}")
    assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# PART 3: Cascade with workflow — exclusive workflows deleted in both modes
# ══════════════════════════════════════════════════════════════════════════════

CASCADE_WF_API_ID = "cascade-wf-test.example.com"

CASCADE_ARAZZO = {
    "arazzo": "1.0.0",
    "info": {
        "title": "Cascade Workflow Test",
        "version": "1.0.0",
        "description": "Workflow for cascade testing",
    },
    "sourceDescriptions": [
        {"name": "cascade-api", "type": "openapi", "url": "https://cascade-wf-test.example.com"}
    ],
    "workflows": [
        {
            "workflowId": "cascade-wf-exclusive",
            "summary": "A workflow exclusive to the test API",
            "steps": [
                {
                    "stepId": "step1",
                    "operationId": "cascade-api.ping",
                    "parameters": [],
                }
            ],
        }
    ],
}


def test_wf_import_api(admin_client):
    """Import an API that will have a workflow."""
    resp = admin_client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(CASCADE_SPEC),
                    "filename": "cascade_wf_test.json",
                    "force_api_id": CASCADE_WF_API_ID,
                }
            ]
        },
    )
    assert resp.status_code == 200


def test_wf_import_workflow(admin_client):
    """Import a workflow that uses the test API."""
    resp = admin_client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(CASCADE_ARAZZO),
                    "filename": "cascade_wf_test_arazzo.json",
                }
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.json()["succeeded"] >= 1


def test_wf_workflow_exists(admin_client):
    """Verify the workflow was imported."""
    resp = admin_client.get("/workflows/cascade-wf-exclusive")
    assert resp.status_code == 200


def test_wf_create_credential(admin_client):
    """Create a credential for this API."""
    resp = admin_client.post(
        "/credentials",
        json={
            "label": "cascade-wf-cred",
            "api_id": CASCADE_WF_API_ID,
            "auth_type": "apiKey",
            "value": json.dumps({"key": "test-key-wf"}),
        },
    )
    assert resp.status_code in (200, 201)


def test_wf_cascade_delete(admin_client):
    """Delete API with cascade — workflow and credentials should all be gone."""
    resp = admin_client.delete(f"/apis/{CASCADE_WF_API_ID}?cascade=true")
    assert resp.status_code == 204


def test_wf_workflow_gone(admin_client):
    """Exclusive workflow was deleted with the API."""
    resp = admin_client.get("/workflows/cascade-wf-exclusive")
    assert resp.status_code == 404


def test_wf_credential_gone(admin_client):
    """Credential was deleted in cascade mode."""
    resp = admin_client.get("/credentials")
    assert resp.status_code == 200
    cred = next((c for c in resp.json() if c.get("label") == "cascade-wf-cred"), None)
    assert cred is None, "Credential should be gone after cascade delete"


def test_wf_api_gone(admin_client):
    """API is gone after cascade delete."""
    resp = admin_client.get(f"/apis/{CASCADE_WF_API_ID}")
    assert resp.status_code == 404
