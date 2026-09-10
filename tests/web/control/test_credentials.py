"""Control web tests for the credential PATCH contract (#739, #589).

Exercises the real HTTP path (router → service → DB) to pin two invariants:

- ``updated_at`` moves iff a change was persisted (#739) — a no-op PATCH must
  leave it frozen.
- The api_key ``field_name``/``location`` binding is immutable after create
  (#589) — a PATCH that changes it returns 409 ``immutable_field`` and never
  leaks secret material.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from jentic_one.admin.repos import AgentCredentialBindingRepository
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


def _parse_ts(value: str) -> datetime:
    """Parse an API ``updated_at`` timestamp for order-safe comparison.

    Comparing the raw ISO strings works today but breaks silently if the
    serialization format or timezone suffix ever changes; parse instead.
    """
    return datetime.fromisoformat(value)


def _create_api_key(client: TestClient) -> str:
    resp = client.post(
        "/credentials",
        json={
            "type": "api_key",
            "name": "web-cred-589",
            "api": {"vendor": "openweathermap.org", "name": "onecall", "version": "3.0"},
            "provider": "static",
            "key": "sk-web-test-key-123",
            "location": "query",
            "field_name": "appid",
        },
    )
    assert resp.status_code == 201, resp.text
    credential_id: str = resp.json()["credential"]["credential_id"]
    return credential_id


def test_patch_changing_field_name_is_rejected(cred_writer_client: TestClient) -> None:
    """A PATCH that changes the api_key field name returns 409 immutable_field."""
    cred_id = _create_api_key(cred_writer_client)

    resp = cred_writer_client.patch(
        f"/credentials/{cred_id}",
        json={"type": "api_key", "field_name": "Default"},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["type"] == "immutable_field"
    # No secret / key material may leak into the error body (redaction rule).
    assert "sk-web-test-key-123" not in resp.text

    # The stored binding is unchanged.
    after = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert after["details"]["field_name"] == "appid"


def test_patch_noop_does_not_move_updated_at(cred_writer_client: TestClient) -> None:
    """A PATCH that echoes the stored binding and nothing else keeps updated_at frozen."""
    cred_id = _create_api_key(cred_writer_client)
    before = cred_writer_client.get(f"/credentials/{cred_id}").json()

    resp = cred_writer_client.patch(
        f"/credentials/{cred_id}",
        json={"type": "api_key", "field_name": "appid", "location": "query"},
    )
    assert resp.status_code == 200, resp.text
    assert _parse_ts(resp.json()["updated_at"]) == _parse_ts(before["updated_at"])


def test_patch_key_rotation_moves_updated_at(cred_writer_client: TestClient) -> None:
    """Rotating the secret persists a change, so updated_at advances (#739)."""
    cred_id = _create_api_key(cred_writer_client)
    before = cred_writer_client.get(f"/credentials/{cred_id}").json()

    resp = cred_writer_client.patch(
        f"/credentials/{cred_id}",
        json={"type": "api_key", "key": "sk-web-test-key-rotated"},
    )
    assert resp.status_code == 200, resp.text
    assert _parse_ts(resp.json()["updated_at"]) > _parse_ts(before["updated_at"])


# --- OAuth connect state on the redacted projection (#890) ---


def _create_oauth2(client: TestClient, *, grant_type: str, name: str) -> str:
    payload: dict[str, object] = {
        "type": "oauth2",
        "name": name,
        "api": {"vendor": "webtest-oauth.example", "name": "", "version": ""},
        "provider": "static",
        "grant_type": grant_type,
        "token_url": "https://auth.example/token",
        "client_id": "webtest-client",
        "client_secret": "webtest-secret",
    }
    if grant_type == "authorization_code":
        payload["authorize_url"] = "https://auth.example/authorize"
    resp = client.post("/credentials", json=payload)
    assert resp.status_code == 201, resp.text
    credential_id: str = resp.json()["credential"]["credential_id"]
    return credential_id


async def test_authorization_code_connected_flips_on_token(
    cred_writer_client: TestClient, web_context: Context
) -> None:
    """An authorization_code credential reports connected=False until its
    sign-in lands a usable token row, then True — and back to False when the
    token is revoked or can no longer mint (expired with no refresh token).
    Lets list consumers (the fulfilment wizard's adopt picker) warn about a
    never-connected pick before it fails at execute time (#890). grant_type
    is the honest stored grant, not the historical client_credentials
    hardcode.
    """
    cred_id = _create_oauth2(
        cred_writer_client, grant_type="authorization_code", name="web-cred-connect"
    )

    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["details"]["grant_type"] == "authorization_code"
    assert got["details"]["connected"] is False

    listed = cred_writer_client.get("/credentials", params={"vendor": "webtest-oauth-example"})
    rows = [c for c in listed.json()["data"] if c["credential_id"] == cred_id]
    # The vendor filter matches the slugified stored form of the dotted input.
    assert len(rows) == 1
    assert rows[0]["details"]["connected"] is False

    # A completed connect flow persists the token row (simulated directly —
    # the flow itself is exercised in tests/integration/control/test_connect_flow.py).
    async with web_context.control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO oauth_tokens (id, credential_id, encrypted_access_token) "
                "VALUES ('oat_webtest_conn', :cred, 'enc-webtest') ON CONFLICT DO NOTHING"
            ),
            {"cred": cred_id},
        )
        await session.commit()
    # No local cleanup: the credential-delete teardown cascades to oauth_tokens.

    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["details"]["connected"] is True

    # Expired with no refresh token: the row exists but cannot mint again —
    # the sign-in must be redone, so the flag drops back to False.
    async with web_context.control_db.session() as session:
        await session.execute(
            text(
                "UPDATE oauth_tokens SET expires_at = '2020-01-01T00:00:00Z' "
                "WHERE id = 'oat_webtest_conn'"
            )
        )
        await session.commit()
    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["details"]["connected"] is False

    # A refresh token rescues an expired access token (the broker re-mints).
    async with web_context.control_db.session() as session:
        await session.execute(
            text(
                "UPDATE oauth_tokens SET encrypted_refresh_token = 'enc-refresh' "
                "WHERE id = 'oat_webtest_conn'"
            )
        )
        await session.commit()
    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["details"]["connected"] is True

    # Revocation trumps everything.
    async with web_context.control_db.session() as session:
        await session.execute(
            text("UPDATE oauth_tokens SET revoked_at = now() WHERE id = 'oat_webtest_conn'")
        )
        await session.commit()
    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["details"]["connected"] is False


async def test_managed_provider_account_ref_counts_as_connected(
    cred_writer_client: TestClient, web_context: Context
) -> None:
    """Managed providers (e.g. Pipedream) complete connect by stamping
    `provider_account_ref` without a local token row — the ref alone must
    read as connected, or the picker would warn forever on a working
    credential.
    """
    cred_id = _create_oauth2(
        cred_writer_client, grant_type="authorization_code", name="web-cred-managed"
    )
    async with web_context.control_db.session() as session:
        await session.execute(
            text("UPDATE credentials SET provider_account_ref = 'acc_webtest' WHERE id = :cred"),
            {"cred": cred_id},
        )
        await session.commit()
    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["details"]["connected"] is True


def test_client_credentials_has_no_connect_state(cred_writer_client: TestClient) -> None:
    """client_credentials needs no interactive sign-in step, so connect state
    is meaningless there: the key is absent (None is dropped from the wire),
    never a scary false."""
    cred_id = _create_oauth2(
        cred_writer_client, grant_type="client_credentials", name="web-cred-cc"
    )
    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["details"]["grant_type"] == "client_credentials"
    assert "connected" not in got["details"]


def test_catalog_api_id_round_trips_verbatim(cred_writer_client: TestClient) -> None:
    """A create that carries the catalog identity slug stores it verbatim and
    exposes it on the create echo, read, and list responses (#910).

    Verbatim matters: the slug's `domain/sub-api` shape is exactly what the
    vendor/name tuple loses to slugification, and the UI derives friendly
    titles from the separable form.
    """
    resp = cred_writer_client.post(
        "/credentials",
        json={
            "type": "api_key",
            "name": "web-cred-910",
            "api": {
                "vendor": "nytimes.com",
                "name": "article_search",
                "version": "",
                "catalog_api_id": "nytimes.com/article_search",
            },
            "provider": "static",
            "key": "sk-web-test-key-910",
            "location": "query",
            "field_name": "api-key",
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()["credential"]
    assert created["catalog_api_id"] == "nytimes.com/article_search"

    got = cred_writer_client.get(f"/credentials/{created['credential_id']}").json()
    assert got["catalog_api_id"] == "nytimes.com/article_search"

    listed = cred_writer_client.get("/credentials").json()["data"]
    row = next(r for r in listed if r["credential_id"] == created["credential_id"])
    assert row["catalog_api_id"] == "nytimes.com/article_search"


def test_catalog_api_id_defaults_to_null(cred_writer_client: TestClient) -> None:
    """Creates that don't know the slug (older clients, manual imports) store
    and expose null — the UI falls back to the vendor/name tuple."""
    cred_id = _create_api_key(cred_writer_client)
    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["catalog_api_id"] is None


# --- Agent bindings (reverse lookup, theme 5 phase 1) ---


@pytest.fixture()
async def bound_agents(
    cred_writer_client: TestClient, web_context: Context
) -> AsyncGenerator[tuple[str, list[str]], None]:
    """A credential with two directly-bound agents, the second suspended.

    Yields ``(credential_id, [agent_id_active, agent_id_suspended])``. Bindings
    live in the admin DB (cross-DB from the credential row) — exactly the seam
    the reverse-lookup endpoint has to bridge.
    """
    credential_id = _create_api_key(cred_writer_client)
    agent_ids = ["agnt_credagents_active", "agnt_credagents_suspend"]
    async with web_context.admin_db.transaction() as session:
        for agent_id, name in zip(
            agent_ids, ("cred-agents-active", "cred-agents-suspended"), strict=True
        ):
            await session.execute(
                text(
                    "INSERT INTO agents (id, name, registered_by, status, created_by) "
                    "VALUES (:id, :name, :registered_by, 'pending', 'usr_test') "
                    "ON CONFLICT DO NOTHING"
                ),
                {"id": agent_id, "name": name, "registered_by": "usr_webtest_cred_writer"},
            )
            await AgentCredentialBindingRepository.bind(
                session, agent_id=agent_id, credential_id=credential_id, created_by="usr_test"
            )
        await AgentCredentialBindingRepository.set_suspended(
            session, agent_id=agent_ids[1], credential_id=credential_id, suspended=True
        )
    yield credential_id, agent_ids

    async with web_context.admin_db.session() as session:
        await session.execute(
            text("DELETE FROM agent_credential_bindings WHERE credential_id = :cid"),
            {"cid": credential_id},
        )
        for agent_id in agent_ids:
            await session.execute(text("DELETE FROM agents WHERE id = :id"), {"id": agent_id})
        await session.commit()


async def test_list_credential_agents(
    cred_writer_client: TestClient, bound_agents: tuple[str, list[str]]
) -> None:
    """The reverse lookup returns every direct binding with its suspended flag —
    a suspended binding is shown (reversible cut-off), never hidden."""
    credential_id, (active_id, suspended_id) = bound_agents

    resp = cred_writer_client.get(f"/credentials/{credential_id}/agents")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_more"] is False
    assert body["next_cursor"] is None
    rows = {r["agent_id"]: r for r in body["data"]}
    assert set(rows) == {active_id, suspended_id}
    assert rows[active_id]["agent_name"] == "cred-agents-active"
    assert rows[active_id]["suspended"] is False
    assert rows[suspended_id]["suspended"] is True
    for row in rows.values():
        assert row["status"] == "pending"
        assert row["bound_at"] is not None


async def test_list_credential_agents_paginates(
    cred_writer_client: TestClient, bound_agents: tuple[str, list[str]]
) -> None:
    """limit=1 walks the two bindings across two pages via next_cursor."""
    credential_id, agent_ids = bound_agents

    first = cred_writer_client.get(f"/credentials/{credential_id}/agents?limit=1").json()
    assert len(first["data"]) == 1
    assert first["has_more"] is True
    assert first["next_cursor"]

    second = cred_writer_client.get(
        f"/credentials/{credential_id}/agents",
        params={"limit": 1, "cursor": first["next_cursor"]},
    ).json()
    assert len(second["data"]) == 1
    assert second["has_more"] is False
    seen = {first["data"][0]["agent_id"], second["data"][0]["agent_id"]}
    assert seen == set(agent_ids)


def test_list_credential_agents_empty(cred_writer_client: TestClient) -> None:
    """A credential with no direct bindings returns an empty page, not 404."""
    credential_id = _create_api_key(cred_writer_client)
    resp = cred_writer_client.get(f"/credentials/{credential_id}/agents")
    assert resp.status_code == 200
    assert resp.json() == {"data": [], "has_more": False, "next_cursor": None}


def test_list_credential_agents_not_found(cred_writer_client: TestClient) -> None:
    resp = cred_writer_client.get("/credentials/cred_nonexistent/agents")
    assert resp.status_code == 404


def test_list_credential_agents_respects_limit_bounds(cred_writer_client: TestClient) -> None:
    credential_id = _create_api_key(cred_writer_client)
    assert cred_writer_client.get(f"/credentials/{credential_id}/agents?limit=0").status_code == 422
    assert (
        cred_writer_client.get(f"/credentials/{credential_id}/agents?limit=201").status_code == 422
    )


def test_list_credential_agents_owner_gated(
    bound_orphan_client: TestClient,
    cred_writer_client: TestClient,
    bound_agents: tuple[str, list[str]],
) -> None:
    """A caller who cannot see the credential gets a uniform 404 — who is bound
    to a credential is exactly as sensitive as the credential itself (owner
    gating on both axes, theme hard problems 7/9)."""
    credential_id, _ = bound_agents
    # The bound orphan holds owner:credentials:read (passes the route gate) but
    # has no visibility path to this credential: not its creator, not org:admin,
    # and it is not shared via any toolkit the orphan is bound to.
    resp = bound_orphan_client.get(f"/credentials/{credential_id}/agents")
    assert resp.status_code == 404


def test_list_credential_agents_wrong_scope_is_403(
    wrong_scope_client: TestClient, cred_writer_client: TestClient
) -> None:
    """A caller without any credentials-read scope is rejected at the gate."""
    credential_id = _create_api_key(cred_writer_client)
    resp = wrong_scope_client.get(f"/credentials/{credential_id}/agents")
    assert resp.status_code == 403
