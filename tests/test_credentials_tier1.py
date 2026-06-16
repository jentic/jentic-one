"""Tests for credentials Tier-1 endpoints — /test, /bindings, audit, Pipedream revoke cascade.

Covers Phase 0 of the credentials revamp:

  - POST /credentials/{id}/test          probe the upstream API with the credential injected
  - GET  /credentials/{id}/bindings      list toolkits this credential is bound to
  - GET  /audit?credential_id=…          read back the persistent audit log
  - DELETE /credentials/{id}             cascades to Pipedream revoke when auth_type=pipedream_oauth

Tests share `admin_client` (cookie session) so they exercise the full auth path,
not just direct vault writes.
"""

import json


def _register_api(
    client, api_id: str, *, scheme_type: str = "bearer", with_healthcheck: bool = False
):
    """Import a minimal API spec so credentials can be stored against it.

    `with_healthcheck=True` adds an `x-jentic-healthcheck: true` to a no-required-params
    GET so the /test endpoint's priority-1 selection has something to grab. We point the
    server URL at a stable internet host (httpbin.org) that responds quickly to GETs —
    we never assert on its body, only on the proxy round-trip succeeding.
    """
    if scheme_type == "bearer":
        schemes = {"BearerAuth": {"type": "http", "scheme": "bearer"}}
    else:
        schemes = {"ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-Api-Key"}}

    op_spec = {"operationId": "test", "responses": {"200": {"description": "ok"}}}
    if with_healthcheck:
        op_spec["x-jentic-healthcheck"] = True

    spec = {
        "openapi": "3.1.0",
        "info": {"title": api_id, "version": "1.0.0"},
        "servers": [{"url": f"https://{api_id}"}],
        "components": {"securitySchemes": schemes},
        "paths": {"/status/200": {"get": op_spec}},
    }

    resp = client.post(
        "/import",
        json={
            "sources": [
                {
                    "type": "inline",
                    "content": json.dumps(spec),
                    "filename": f"{api_id}.json",
                }
            ],
        },
    )
    assert resp.status_code in (200, 201), f"Import failed: {resp.text}"


def _create_credential(
    client, api_id: str, *, auth_type: str = "bearer", label: str = "tier1"
) -> str:
    resp = client.post(
        "/credentials",
        json={
            "label": label,
            "value": "secret-value-not-real",
            "api_id": api_id,
            "auth_type": auth_type,
        },
    )
    assert resp.status_code in (200, 201), f"Create failed: {resp.text}"
    return resp.json()["id"]


# ── /test ──────────────────────────────────────────────────────────────────────


def test_test_endpoint_returns_ok_shape_for_unreachable_host(admin_client):
    """The /test response shape is stable even when the upstream is unreachable.

    We pick a TLD that doesn't resolve so we never actually hit the network — the
    contract under test is the Pydantic-shaped JSON response (`ok`, `status`, `hint`,
    `probe_url`), not whether a specific upstream API is reachable from CI.

    Note: a non-resolvable host is now caught by the SSRF guard (which fails closed
    on resolution failure) and returns `blocked_host` before any outbound call — the
    response shape is identical, which is what this test asserts.
    """
    api_id = "tier1-test-unreachable.invalid"
    _register_api(admin_client, api_id)
    cid = _create_credential(admin_client, api_id)

    resp = admin_client.post(f"/credentials/{cid}/test")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) >= {"ok", "status", "hint", "probe_url"}
    # Unreachable / non-resolvable host → ok=False with a network/guard sentinel.
    assert body["ok"] is False
    assert body["hint"] in {"timeout", "network_error", "blocked_host"}
    assert body["probe_url"] and body["probe_url"].startswith("https://")


def test_test_endpoint_404_for_missing_credential(admin_client):
    resp = admin_client.post("/credentials/nope_does_not_exist/test")
    assert resp.status_code == 404


def test_test_endpoint_pipedream_returns_diagnostic(admin_client):
    """Pipedream credentials can't be probed directly — endpoint returns a hint."""
    api_id = "tier1-pipedream.example.com"
    _register_api(admin_client, api_id, scheme_type="apiKey")
    # Seed a pipedream_oauth credential by writing directly via the vault — the
    # create endpoint correctly rejects this reserved auth_type (see
    # test_create_rejects_reserved_pipedream_oauth), so we bypass it here to set
    # up the state that the Pipedream sync would otherwise produce.
    import asyncio  # noqa: PLC0415

    import src.vault as vault  # noqa: PLC0415

    async def _seed():
        cred = await vault.create_credential(
            "pd-seed",
            "apn_test_account_id",
            api_id=api_id,
            scheme_name="pipedream_oauth",
        )
        return cred["id"]

    cid = asyncio.run(_seed())

    resp = admin_client.post(f"/credentials/{cid}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["hint"] == "pipedream_unsupported"


# ── /bindings ─────────────────────────────────────────────────────────────────


def test_bindings_returns_toolkit_rows(admin_client):
    api_id = "tier1-bindings.example.com"
    _register_api(admin_client, api_id)
    cid = _create_credential(admin_client, api_id, label="bindings-test")

    # Create a toolkit and bind the credential
    tk_resp = admin_client.post(
        "/toolkits", json={"name": "bindings-toolkit", "description": "tier1 binding test"}
    )
    assert tk_resp.status_code in (200, 201), tk_resp.text
    toolkit_id = tk_resp.json()["id"]

    bind_resp = admin_client.post(
        f"/toolkits/{toolkit_id}/credentials",
        json={"credential_id": cid, "alias": "primary"},
    )
    assert bind_resp.status_code in (200, 201), bind_resp.text

    resp = admin_client.get(f"/credentials/{cid}/bindings")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert isinstance(rows, list)
    assert any(r["toolkit_id"] == toolkit_id for r in rows), rows
    # Each row carries toolkit_name + alias, even when alias defaults from label.
    for r in rows:
        assert "toolkit_name" in r
        assert "alias" in r


def test_bindings_404_for_missing_credential(admin_client):
    resp = admin_client.get("/credentials/nope_does_not_exist/bindings")
    assert resp.status_code == 404


def test_bindings_empty_list_for_unbound_credential(admin_client):
    api_id = "tier1-bindings-empty.example.com"
    _register_api(admin_client, api_id)
    cid = _create_credential(admin_client, api_id)

    resp = admin_client.get(f"/credentials/{cid}/bindings")
    assert resp.status_code == 200
    assert resp.json() == []


# ── audit log ─────────────────────────────────────────────────────────────────


def test_audit_log_records_credential_lifecycle(admin_client):
    """Create + delete a credential, then check the audit endpoint surfaces both events."""
    api_id = "tier1-audit.example.com"
    _register_api(admin_client, api_id)
    cid = _create_credential(admin_client, api_id, label="audit-test")

    resp = admin_client.delete(f"/credentials/{cid}")
    assert resp.status_code == 204, resp.text

    audit_resp = admin_client.get(f"/audit?credential_id={cid}")
    assert audit_resp.status_code == 200, audit_resp.text
    events = audit_resp.json()
    event_names = {e["event"] for e in events}
    assert {"CREDENTIAL_CREATED", "CREDENTIAL_DELETED"}.issubset(event_names), events
    # Each row carries the target binding so the UI can filter.
    for e in events:
        assert e["target_kind"] == "credential"
        assert e["target_id"] == cid


def test_audit_log_filters_by_event_name(admin_client):
    api_id = "tier1-audit-filter.example.com"
    _register_api(admin_client, api_id)
    cid = _create_credential(admin_client, api_id)

    # Patching is a separate event — verifying we can isolate it from CREATED.
    patch_resp = admin_client.patch(
        f"/credentials/{cid}", json={"description": "rotated for compliance"}
    )
    assert patch_resp.status_code == 200, patch_resp.text

    resp = admin_client.get(f"/audit?credential_id={cid}&event=CREDENTIAL_UPDATED")
    assert resp.status_code == 200
    rows = resp.json()
    assert rows, "expected at least one CREDENTIAL_UPDATED row"
    assert all(r["event"] == "CREDENTIAL_UPDATED" for r in rows)


# ── description / last_used_at field surfacing ────────────────────────────────


def test_credential_out_includes_new_fields(admin_client):
    """`description` and `last_used_at` appear on GET (last_used_at is None until used)."""
    api_id = "tier1-fields.example.com"
    _register_api(admin_client, api_id)
    resp = admin_client.post(
        "/credentials",
        json={
            "label": "field-check",
            "value": "sk-secret",
            "api_id": api_id,
            "auth_type": "bearer",
            "description": "Used by the nightly digest job",
        },
    )
    assert resp.status_code in (200, 201), resp.text
    cid = resp.json()["id"]

    got = admin_client.get(f"/credentials/{cid}").json()
    assert got["description"] == "Used by the nightly digest job"
    assert got["last_used_at"] is None


# ── reserved auth_type rejection ──────────────────────────────────────────────


def test_create_rejects_reserved_pipedream_oauth(admin_client):
    """`pipedream_oauth` is reserved for the Pipedream sync — create must reject it
    so a caller can't self-assign it and create a credential with no backing broker."""
    api_id = "tier1-reserved-create.example.com"
    _register_api(admin_client, api_id, scheme_type="apiKey")
    resp = admin_client.post(
        "/credentials",
        json={
            "label": "reserved-attempt",
            "value": "secret",
            "api_id": api_id,
            "auth_type": "pipedream_oauth",
        },
    )
    assert resp.status_code == 400, resp.text
    assert "reserved" in resp.text.lower()


def test_create_rejects_reserved_jentic_api_key(admin_client):
    """`JenticApiKey` marks the internal admin key — never settable via the API."""
    api_id = "tier1-reserved-jentic.example.com"
    _register_api(admin_client, api_id, scheme_type="apiKey")
    resp = admin_client.post(
        "/credentials",
        json={
            "label": "reserved-attempt-2",
            "value": "secret",
            "api_id": api_id,
            "auth_type": "JenticApiKey",
        },
    )
    assert resp.status_code == 400, resp.text


def test_patch_rejects_reserved_auth_type(admin_client):
    """Patching auth_type to a reserved value is rejected too."""
    api_id = "tier1-reserved-patch.example.com"
    _register_api(admin_client, api_id)
    cid = _create_credential(admin_client, api_id, label="reserved-patch")
    resp = admin_client.patch(f"/credentials/{cid}", json={"auth_type": "pipedream_oauth"})
    assert resp.status_code == 400, resp.text
    assert "reserved" in resp.text.lower()


# ── /test SSRF guard ──────────────────────────────────────────────────────────


def test_test_endpoint_blocks_private_host(admin_client):
    """The /test probe must refuse private / loopback / metadata hosts even when a
    valid credential is bound — the probe URL is derived from user-controlled input.

    Seed the credential directly via the vault (no spec import) so the probe URL
    falls back to ``https://{api_id}/`` and we exercise the SSRF guard itself
    rather than the import-time self-hosted overlay.
    """
    import asyncio  # noqa: PLC0415

    import src.vault as vault  # noqa: PLC0415

    async def _seed(host: str, label: str) -> str:
        cred = await vault.create_credential(
            label, "secret-value", api_id=host, scheme_name="bearer"
        )
        return cred["id"]

    # 169.254.169.254 = cloud metadata endpoint (link-local).
    cid = asyncio.run(_seed("169.254.169.254", "ssrf-metadata"))
    resp = admin_client.post(f"/credentials/{cid}/test")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["hint"] == "blocked_host", body


def test_test_endpoint_blocks_loopback_host(admin_client):
    import asyncio  # noqa: PLC0415

    import src.vault as vault  # noqa: PLC0415

    async def _seed() -> str:
        cred = await vault.create_credential(
            "ssrf-loopback", "secret-value", api_id="127.0.0.1", scheme_name="bearer"
        )
        return cred["id"]

    cid = asyncio.run(_seed())
    resp = admin_client.post(f"/credentials/{cid}/test")
    assert resp.status_code == 200, resp.text
    assert resp.json()["hint"] == "blocked_host"


# ── agent authorization gating (P0-1 / P2-4) ──────────────────────────────────


def test_test_endpoint_rejects_ungranted_agent(admin_client, agent_only_client):
    """An agent key without an explicit POST /credentials allow rule cannot probe a
    credential — the probe decrypts and uses the secret, so it is gated like a write."""
    api_id = "tier1-agent-test-gate.example.com"
    _register_api(admin_client, api_id)
    cid = _create_credential(admin_client, api_id, label="agent-test-gate")

    resp = agent_only_client.post(f"/credentials/{cid}/test")
    assert resp.status_code == 403, resp.text


def test_bindings_requires_human_session(admin_client, agent_only_client):
    api_id = "tier1-agent-bindings-gate.example.com"
    _register_api(admin_client, api_id)
    cid = _create_credential(admin_client, api_id, label="agent-bindings-gate")

    resp = agent_only_client.get(f"/credentials/{cid}/bindings")
    assert resp.status_code == 403, resp.text


def test_audit_requires_human_session(agent_only_client):
    resp = agent_only_client.get("/audit")
    assert resp.status_code == 403, resp.text


def test_patch_rejects_ungranted_agent(admin_client, agent_only_client):
    """An agent key without an explicit PATCH /credentials allow rule cannot
    mutate a credential. Only POST is exercised elsewhere; assert PATCH too."""
    api_id = "tier1-agent-patch-gate.example.com"
    _register_api(admin_client, api_id)
    cid = _create_credential(admin_client, api_id, label="agent-patch-gate")

    resp = agent_only_client.patch(f"/credentials/{cid}", json={"label": "renamed"})
    assert resp.status_code == 403, resp.text
    # The credential must be untouched.
    got = admin_client.get(f"/credentials/{cid}")
    assert got.status_code == 200
    assert got.json()["label"] == "agent-patch-gate"


def test_delete_rejects_ungranted_agent(admin_client, agent_only_client):
    """An agent key without an explicit DELETE /credentials allow rule cannot
    delete a credential. Only POST is exercised elsewhere; assert DELETE too."""
    api_id = "tier1-agent-delete-gate.example.com"
    _register_api(admin_client, api_id)
    cid = _create_credential(admin_client, api_id, label="agent-delete-gate")

    resp = agent_only_client.delete(f"/credentials/{cid}")
    assert resp.status_code == 403, resp.text
    # The credential must still exist.
    got = admin_client.get(f"/credentials/{cid}")
    assert got.status_code == 200, got.text


# ── Pipedream revoke cascade on DELETE /credentials ───────────────────────────


def test_delete_pipedream_credential_does_not_500_without_broker(admin_client):
    """Deleting a credential whose `auth_type='pipedream_oauth'` must not 500
    when no broker config or live registry entry can be found upstream — the
    local row is the source of truth and cleanup proceeds.
    """
    import asyncio  # noqa: PLC0415

    import src.vault as vault  # noqa: PLC0415

    api_id = "tier1-pd-cascade.example.com"
    _register_api(admin_client, api_id, scheme_type="apiKey")

    async def _seed():
        cred = await vault.create_credential(
            "pd-cascade-seed",
            "apn_test_account_id",
            api_id=api_id,
            scheme_name="pipedream_oauth",
        )
        return cred["id"]

    cid = asyncio.run(_seed())

    resp = admin_client.delete(f"/credentials/{cid}")
    assert resp.status_code == 204, resp.text
    # Audit row should record the cascade attempt outcome (revoke result is False
    # here because there's no live broker — but the event is still persisted).
    audit_resp = admin_client.get(f"/audit?credential_id={cid}&event=CREDENTIAL_DELETED")
    assert audit_resp.status_code == 200
    rows = audit_resp.json()
    assert rows
    payload = rows[0]["payload"]
    assert payload.get("auth_type") == "pipedream_oauth"
    assert "pipedream_revoked" in payload  # may be False, that's fine
