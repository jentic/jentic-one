"""Re-import link preservation tests.

Verifies that credentials and toolkit bindings behave correctly across
the delete → re-import lifecycle for both soft (non-cascade) and cascade modes.

Scenarios tested:
  1. Soft delete + re-import: credentials survive but api_id is cleared;
     after re-import the credential api_id should be restored.
  2. Cascade delete + re-import: credentials are gone; after re-import
     no credentials exist (clean slate).
  3. Toolkit bindings survive soft delete and remain functional after re-import.
  4. Toolkit bindings are removed by cascade delete and stay gone after re-import.
"""

import json


# ── Shared spec fixture ───────────────────────────────────────────────────────

REIMPORT_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Reimport Test API", "version": "1.0.0"},
    "servers": [{"url": "https://api.reimport-test.example.com"}],
    "components": {
        "securitySchemes": {"ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-Api-Key"}}
    },
    "security": [{"ApiKeyAuth": []}],
    "paths": {
        "/health": {
            "get": {
                "operationId": "healthCheck",
                "summary": "Health check",
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/data": {
            "post": {
                "operationId": "createData",
                "summary": "Create data",
                "responses": {"201": {"description": "Created"}},
            }
        },
    },
}

SOFT_REIMPORT_API_ID = "reimport-soft.example.com"
HARD_REIMPORT_API_ID = "reimport-hard.example.com"
TOOLKIT_REIMPORT_API_ID = "reimport-toolkit.example.com"


def _import_api(client, api_id, filename):
    resp = client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(REIMPORT_SPEC),
                    "filename": filename,
                    "force_api_id": api_id,
                }
            ]
        },
    )
    assert resp.status_code == 200, f"Import failed: {resp.text}"
    assert resp.json()["succeeded"] >= 1
    return resp.json()


def _create_credential(client, api_id, label):
    resp = client.post(
        "/credentials",
        json={
            "label": label,
            "api_id": api_id,
            "auth_type": "apiKey",
            "value": json.dumps({"key": f"secret-{label}"}),
        },
    )
    assert resp.status_code in (200, 201), f"Credential creation failed: {resp.text}"
    return resp.json()


def _get_credential_by_label(client, label):
    resp = client.get("/credentials")
    assert resp.status_code == 200
    return next((c for c in resp.json() if c.get("label") == label), None)


def _get_credentials_for_api(client, api_id):
    resp = client.get(f"/credentials?api_id={api_id}")
    assert resp.status_code == 200
    return resp.json()


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 1: Soft delete + re-import — credential link restoration
# ══════════════════════════════════════════════════════════════════════════════


def test_soft_reimport_setup(admin_client):
    """Import API and create a credential for it."""
    _import_api(admin_client, SOFT_REIMPORT_API_ID, "reimport_soft.json")
    _create_credential(admin_client, SOFT_REIMPORT_API_ID, "reimport-soft-cred")

    creds = _get_credentials_for_api(admin_client, SOFT_REIMPORT_API_ID)
    assert len(creds) >= 1
    assert any(c["label"] == "reimport-soft-cred" for c in creds)


def test_soft_reimport_delete(admin_client):
    """Soft-delete the API — credential survives with api_id intact."""
    resp = admin_client.delete(f"/apis/{SOFT_REIMPORT_API_ID}")
    assert resp.status_code == 204

    cred = _get_credential_by_label(admin_client, "reimport-soft-cred")
    assert cred is not None, "Credential should survive soft delete"
    assert cred.get("api_id") == SOFT_REIMPORT_API_ID, "api_id should be preserved"


def test_soft_reimport_credential_still_linked_by_api_id(admin_client):
    """After soft delete, credential still references the api_id (API row gone)."""
    creds = _get_credentials_for_api(admin_client, SOFT_REIMPORT_API_ID)
    assert any(c["label"] == "reimport-soft-cred" for c in creds), (
        "Credential should still be queryable by api_id even though API is deleted"
    )


def test_soft_reimport_reimport(admin_client):
    """Re-import the same API."""
    _import_api(admin_client, SOFT_REIMPORT_API_ID, "reimport_soft.json")
    resp = admin_client.get(f"/apis/{SOFT_REIMPORT_API_ID}")
    assert resp.status_code == 200


def test_soft_reimport_credential_restored(admin_client):
    """After re-import, credential api_id should be restored."""
    creds = _get_credentials_for_api(admin_client, SOFT_REIMPORT_API_ID)
    linked = [c for c in creds if c["label"] == "reimport-soft-cred"]
    assert len(linked) == 1, (
        f"Credential should be re-linked after re-import. "
        f"Found: {[c.get('api_id') for c in [_get_credential_by_label(admin_client, 'reimport-soft-cred')]]}"
    )


def test_soft_reimport_cleanup(admin_client):
    """Clean up."""
    admin_client.delete(f"/apis/{SOFT_REIMPORT_API_ID}?cascade=true")


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 2: Cascade delete + re-import — clean slate
# ══════════════════════════════════════════════════════════════════════════════


def test_hard_reimport_setup(admin_client):
    """Import API and create credentials."""
    _import_api(admin_client, HARD_REIMPORT_API_ID, "reimport_hard.json")
    _create_credential(admin_client, HARD_REIMPORT_API_ID, "reimport-hard-cred-1")
    _create_credential(admin_client, HARD_REIMPORT_API_ID, "reimport-hard-cred-2")

    creds = _get_credentials_for_api(admin_client, HARD_REIMPORT_API_ID)
    assert len(creds) >= 2


def test_hard_reimport_cascade_delete(admin_client):
    """Cascade-delete the API — credentials are destroyed."""
    resp = admin_client.delete(f"/apis/{HARD_REIMPORT_API_ID}?cascade=true")
    assert resp.status_code == 204

    cred1 = _get_credential_by_label(admin_client, "reimport-hard-cred-1")
    cred2 = _get_credential_by_label(admin_client, "reimport-hard-cred-2")
    assert cred1 is None, "Credential 1 should be deleted by cascade"
    assert cred2 is None, "Credential 2 should be deleted by cascade"


def test_hard_reimport_reimport(admin_client):
    """Re-import after cascade — API comes back with no credentials."""
    _import_api(admin_client, HARD_REIMPORT_API_ID, "reimport_hard.json")
    resp = admin_client.get(f"/apis/{HARD_REIMPORT_API_ID}")
    assert resp.status_code == 200

    creds = _get_credentials_for_api(admin_client, HARD_REIMPORT_API_ID)
    assert len(creds) == 0, "No credentials should exist after cascade + re-import"


def test_hard_reimport_cleanup(admin_client):
    """Clean up."""
    admin_client.delete(f"/apis/{HARD_REIMPORT_API_ID}?cascade=true")


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 3: Toolkit bindings across soft delete + re-import
# ══════════════════════════════════════════════════════════════════════════════


def _create_toolkit(client, name):
    resp = client.post("/toolkits", json={"name": name})
    assert resp.status_code in (200, 201), f"Toolkit creation failed: {resp.text}"
    return resp.json()


def _bind_credential_to_toolkit(client, toolkit_id, credential_id):
    resp = client.post(
        f"/toolkits/{toolkit_id}/credentials",
        json={"credential_id": credential_id},
    )
    assert resp.status_code in (200, 201), f"Binding failed: {resp.text}"
    return resp.json()


def _get_toolkit_detail(client, toolkit_id):
    resp = client.get(f"/toolkits/{toolkit_id}")
    assert resp.status_code == 200
    return resp.json()


def test_toolkit_reimport_setup(admin_client):
    """Import API, create credential, create toolkit, bind credential."""
    _import_api(admin_client, TOOLKIT_REIMPORT_API_ID, "reimport_toolkit.json")
    cred = _create_credential(admin_client, TOOLKIT_REIMPORT_API_ID, "reimport-tk-cred")
    cred_id = cred.get("id") or cred.get("credential_id")
    assert cred_id, f"No credential ID in response: {cred}"

    tk = _create_toolkit(admin_client, "Reimport Link Test Toolkit")
    tk_id = tk.get("id") or tk.get("toolkit_id")
    assert tk_id, f"No toolkit ID in response: {tk}"

    _bind_credential_to_toolkit(admin_client, tk_id, cred_id)

    detail = _get_toolkit_detail(admin_client, tk_id)
    bound_apis = detail.get("bound_apis", [])
    assert TOOLKIT_REIMPORT_API_ID in bound_apis, (
        f"Toolkit should reference the API. bound_apis={bound_apis}"
    )


def test_toolkit_reimport_soft_delete(admin_client):
    """Soft-delete the API — toolkit binding should remain (credential persists)."""
    resp = admin_client.delete(f"/apis/{TOOLKIT_REIMPORT_API_ID}")
    assert resp.status_code == 204

    # Credential survives (api_id preserved)
    cred = _get_credential_by_label(admin_client, "reimport-tk-cred")
    assert cred is not None
    assert cred.get("api_id") == TOOLKIT_REIMPORT_API_ID

    # Toolkit binding still exists (toolkit_credentials row references credential_id)
    resp = admin_client.get("/toolkits")
    assert resp.status_code == 200
    toolkits = resp.json()
    tk = next((t for t in toolkits if t["name"] == "Reimport Link Test Toolkit"), None)
    assert tk is not None

    detail = _get_toolkit_detail(admin_client, tk["id"])
    creds_in_toolkit = detail.get("credentials", [])
    assert any(
        c.get("credential_id") == cred["id"] or c.get("label") == "reimport-tk-cred"
        for c in creds_in_toolkit
    ), f"Toolkit should still have the credential bound. credentials={creds_in_toolkit}"


def test_toolkit_reimport_after_reimport(admin_client):
    """After re-import, toolkit should show the API in bound_apis again."""
    _import_api(admin_client, TOOLKIT_REIMPORT_API_ID, "reimport_toolkit.json")

    # The credential's api_id should be restored for the toolkit to show the API
    cred = _get_credential_by_label(admin_client, "reimport-tk-cred")
    assert cred is not None

    resp = admin_client.get("/toolkits")
    toolkits = resp.json()
    tk = next((t for t in toolkits if t["name"] == "Reimport Link Test Toolkit"), None)
    assert tk is not None

    detail = _get_toolkit_detail(admin_client, tk["id"])
    bound_apis = detail.get("bound_apis", [])
    # This test verifies the credential api_id was restored on re-import
    assert TOOLKIT_REIMPORT_API_ID in bound_apis, (
        f"After re-import, toolkit should show API in bound_apis. "
        f"Got: {bound_apis}. Credential api_id={cred.get('api_id')}"
    )


def test_toolkit_reimport_cleanup(admin_client):
    """Clean up toolkit and API."""
    resp = admin_client.get("/toolkits")
    toolkits = resp.json()
    tk = next((t for t in toolkits if t["name"] == "Reimport Link Test Toolkit"), None)
    if tk:
        admin_client.delete(f"/toolkits/{tk['id']}")
    admin_client.delete(f"/apis/{TOOLKIT_REIMPORT_API_ID}?cascade=true")


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 4: Toolkit bindings after cascade delete + re-import
# ══════════════════════════════════════════════════════════════════════════════

TOOLKIT_CASCADE_API_ID = "reimport-tk-cascade.example.com"


def test_toolkit_cascade_reimport_setup(admin_client):
    """Import API, create credential, create toolkit, bind credential."""
    _import_api(admin_client, TOOLKIT_CASCADE_API_ID, "reimport_tk_cascade.json")
    cred = _create_credential(admin_client, TOOLKIT_CASCADE_API_ID, "reimport-tk-cascade-cred")
    cred_id = cred.get("id") or cred.get("credential_id")

    tk = _create_toolkit(admin_client, "Reimport Cascade Toolkit")
    tk_id = tk.get("id") or tk.get("toolkit_id")

    _bind_credential_to_toolkit(admin_client, tk_id, cred_id)

    detail = _get_toolkit_detail(admin_client, tk_id)
    assert TOOLKIT_CASCADE_API_ID in detail.get("bound_apis", [])


def test_toolkit_cascade_delete(admin_client):
    """Cascade-delete the API — credential + toolkit binding destroyed."""
    resp = admin_client.delete(f"/apis/{TOOLKIT_CASCADE_API_ID}?cascade=true")
    assert resp.status_code == 204

    # Credential gone
    cred = _get_credential_by_label(admin_client, "reimport-tk-cascade-cred")
    assert cred is None, "Credential should be deleted by cascade"

    # Toolkit exists but has no credentials for this API
    resp = admin_client.get("/toolkits")
    toolkits = resp.json()
    tk = next((t for t in toolkits if t["name"] == "Reimport Cascade Toolkit"), None)
    assert tk is not None, "Toolkit itself should survive"

    detail = _get_toolkit_detail(admin_client, tk["id"])
    assert TOOLKIT_CASCADE_API_ID not in detail.get("bound_apis", [])


def test_toolkit_cascade_reimport(admin_client):
    """Re-import after cascade — toolkit has no bindings for this API."""
    _import_api(admin_client, TOOLKIT_CASCADE_API_ID, "reimport_tk_cascade.json")

    resp = admin_client.get("/toolkits")
    toolkits = resp.json()
    tk = next((t for t in toolkits if t["name"] == "Reimport Cascade Toolkit"), None)
    assert tk is not None

    detail = _get_toolkit_detail(admin_client, tk["id"])
    assert TOOLKIT_CASCADE_API_ID not in detail.get("bound_apis", []), (
        "After cascade + re-import, toolkit should NOT have API bindings (clean slate)"
    )


def test_toolkit_cascade_reimport_cleanup(admin_client):
    """Clean up."""
    resp = admin_client.get("/toolkits")
    toolkits = resp.json()
    tk = next((t for t in toolkits if t["name"] == "Reimport Cascade Toolkit"), None)
    if tk:
        admin_client.delete(f"/toolkits/{tk['id']}")
    admin_client.delete(f"/apis/{TOOLKIT_CASCADE_API_ID}?cascade=true")
