"""Control web tests for the credential PATCH contract (#739, #589).

Exercises the real HTTP path (router → service → DB) to pin two invariants:

- ``updated_at`` moves iff a change was persisted (#739) — a no-op PATCH must
  leave it frozen.
- The api_key ``field_name``/``location`` binding is immutable after create
  (#589) — a PATCH that changes it returns 409 ``immutable_field`` and never
  leaks secret material.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from jentic_one.admin.repos import AgentCredentialBindingRepository
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from tests.web.control.conftest import _build_app, _effective

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


# --- Per-binding permission rules (theme 5 phase 1) ---


def test_agent_permissions_lifecycle(
    cred_writer_client: TestClient, bound_agents: tuple[str, list[str]]
) -> None:
    """PUT replaces the ordered list, GET reads it back, PATCH adds/removes."""
    credential_id, (agent_id, _) = bound_agents
    base = f"/credentials/{credential_id}/agents/{agent_id}/permissions"

    # A fresh binding has no rules — default-deny with nothing to show.
    assert cred_writer_client.get(base).json() == {"data": []}

    # PUT an ordered list: deny DELETE first, then allow the rest.
    rules = [
        {"effect": "deny", "methods": ["DELETE"], "path": ".*"},
        {"effect": "allow", "methods": ["GET", "POST"], "path": "/v1/.*"},
    ]
    resp = cred_writer_client.put(base, json=rules)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert [(r["effect"], r["methods"]) for r in body] == [
        ("deny", ["DELETE"]),
        ("allow", ["GET", "POST"]),
    ]

    # PUT is idempotent replacement, not append.
    resp = cred_writer_client.put(base, json=rules)
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 2

    # PATCH: remove the deny (index 0), add a narrower one at the end.
    resp = cred_writer_client.patch(
        base,
        json={
            "remove": [0],
            "add": [{"effect": "deny", "methods": ["DELETE"], "path": "/v1/payments/.*"}],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert [(r["effect"], r["path"]) for r in body] == [
        ("allow", "/v1/.*"),
        ("deny", "/v1/payments/.*"),
    ]


def test_agent_permissions_test_endpoint(
    cred_writer_client: TestClient, bound_agents: tuple[str, list[str]]
) -> None:
    """:test evaluates the binding's own ordered list — first match wins,
    default-deny on no match, condition-less allow rejected at authoring."""
    credential_id, (agent_id, _) = bound_agents
    base = f"/credentials/{credential_id}/agents/{agent_id}/permissions"

    # A condition-less allow is rejected at the schema boundary — the broker's
    # evaluation-time skip for such rules can therefore only be hit by legacy
    # rows, and an authored blanket grant never enters the list.
    resp = cred_writer_client.put(base, json=[{"effect": "allow"}])
    assert resp.status_code == 422

    resp = cred_writer_client.put(
        base,
        json=[
            {"effect": "deny", "methods": ["DELETE"], "path": ".*"},
            {"effect": "allow", "methods": ["GET"], "path": "/v1/.*"},
        ],
    )
    assert resp.status_code == 200, resp.text

    # First-match-wins deny.
    got = cred_writer_client.post(
        f"{base}:test", json={"method": "delete", "path": "/v1/payments/p_1"}
    ).json()
    assert got["allowed"] is False
    assert got["matched"] is True
    assert got["effect"] == "deny"
    assert got["rule_index"] == 0
    assert got["credential_id"] == credential_id

    # Allow via the second rule.
    got = cred_writer_client.post(f"{base}:test", json={"method": "GET", "path": "/v1/ok"}).json()
    assert got == {
        "allowed": True,
        "matched": True,
        "effect": "allow",
        "rule_index": 1,
        "credential_id": credential_id,
        "is_system": False,
    }

    # No rule matches → default-deny, nothing to attribute.
    got = cred_writer_client.post(f"{base}:test", json={"method": "GET", "path": "/other"}).json()
    assert got == {
        "allowed": False,
        "matched": False,
        "effect": None,
        "rule_index": None,
        "credential_id": None,
        "is_system": None,
    }


def test_agent_permissions_unbound_agent_is_404(
    cred_writer_client: TestClient, bound_agents: tuple[str, list[str]]
) -> None:
    """The binding must exist on both axes — a real credential with no binding
    for this agent is a 404 agent_binding_not_found."""
    credential_id, _ = bound_agents
    resp = cred_writer_client.get(
        f"/credentials/{credential_id}/agents/agnt_never_bound/permissions"
    )
    assert resp.status_code == 404
    assert resp.json()["type"] == "agent_binding_not_found"


def test_agent_permissions_unknown_credential_is_404(cred_writer_client: TestClient) -> None:
    resp = cred_writer_client.get("/credentials/cred_nonexistent/agents/agnt_whatever/permissions")
    assert resp.status_code == 404
    assert resp.json()["type"] == "credential_not_found"


def test_agent_permissions_owner_gated(
    bound_orphan_client: TestClient, bound_agents: tuple[str, list[str]]
) -> None:
    """A caller who cannot see the credential gets the same uniform 404 on the
    rules endpoints as everywhere else — a binding's policy is exactly as
    sensitive as the credential it governs."""
    credential_id, (agent_id, _) = bound_agents
    resp = bound_orphan_client.get(f"/credentials/{credential_id}/agents/{agent_id}/permissions")
    assert resp.status_code == 404
    assert resp.json()["type"] == "credential_not_found"


def test_agent_permissions_write_needs_write_scope(
    delegated_agent_client: TestClient, bound_agents: tuple[str, list[str]]
) -> None:
    """owner:credentials:read admits reads but never writes — PUT/PATCH require
    credentials:write."""
    credential_id, (agent_id, _) = bound_agents
    base = f"/credentials/{credential_id}/agents/{agent_id}/permissions"
    assert delegated_agent_client.put(base, json=[]).status_code == 403
    assert delegated_agent_client.patch(base, json={"remove": [0]}).status_code == 403


# --- Shared permission rule sets (theme 5 phase 1, Q-04) ---


@pytest.fixture()
async def clean_rule_sets(web_context: Context) -> AsyncGenerator[None, None]:
    """Empty the rule-set tables after each test (rules cascade with sets)."""
    yield
    async with web_context.control_db.session() as session:
        await session.execute(text("DELETE FROM permission_rule_sets"))
        await session.commit()


@pytest.fixture()
def plain_writer_client(web_context: Context) -> Iterator[TestClient]:
    """A credentials:write caller who is NOT org:admin and NOT the set creator.

    Exists to pin the provisional creator-or-admin write gate (plan OQ-6):
    holding the write scope alone must not admit edits to someone else's set.
    """
    identity = Identity(
        sub="usr_webtest_plain_writer",
        email="plainwriter@test.local",
        permissions=_effective("credentials:read", "credentials:write"),
    )
    app = _build_app(web_context, identity)
    with TestClient(app) as tc:
        yield tc


_RULES = [
    {"effect": "deny", "path": "/v1/admin/.*"},
    {"effect": "allow", "methods": ["GET"]},
]


def test_rule_set_crud_lifecycle(cred_writer_client: TestClient, clean_rule_sets: None) -> None:
    """Create → get → list → rename → replace rules → delete round-trip."""
    resp = cred_writer_client.post(
        "/permission-rule-sets",
        json={"name": "read-only", "description": "GETs only", "rules": _RULES},
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    set_id = created["rule_set_id"]
    assert set_id.startswith("prs_")
    assert created["binding_count"] == 0
    assert [r["effect"] for r in created["rules"]] == ["deny", "allow"]

    got = cred_writer_client.get(f"/permission-rule-sets/{set_id}").json()
    assert got["name"] == "read-only"
    assert got["rules"][0]["path"] == "/v1/admin/.*"

    listed = cred_writer_client.get("/permission-rule-sets").json()
    rows = {r["rule_set_id"]: r for r in listed["data"]}
    assert rows[set_id]["rule_count"] == 2

    renamed = cred_writer_client.patch(
        f"/permission-rule-sets/{set_id}", json={"name": "read-only-v2"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "read-only-v2"

    replaced = cred_writer_client.put(
        f"/permission-rule-sets/{set_id}/rules",
        json=[{"effect": "allow", "methods": ["GET", "HEAD"]}],
    )
    assert replaced.status_code == 200
    assert len(replaced.json()["data"]) == 1

    assert cred_writer_client.delete(f"/permission-rule-sets/{set_id}").status_code == 204
    assert cred_writer_client.get(f"/permission-rule-sets/{set_id}").status_code == 404


def test_rule_set_name_conflict(cred_writer_client: TestClient, clean_rule_sets: None) -> None:
    """Names are unique — create and rename both 409 on a taken name."""
    first = cred_writer_client.post("/permission-rule-sets", json={"name": "taken"})
    assert first.status_code == 201
    other = cred_writer_client.post("/permission-rule-sets", json={"name": "other"})
    assert other.status_code == 201

    dup = cred_writer_client.post("/permission-rule-sets", json={"name": "taken"})
    assert dup.status_code == 409
    assert dup.json()["type"] == "rule_set_name_conflict"

    rename = cred_writer_client.patch(
        f"/permission-rule-sets/{other.json()['rule_set_id']}", json={"name": "taken"}
    )
    assert rename.status_code == 409
    assert rename.json()["type"] == "rule_set_name_conflict"


async def test_rule_set_delete_in_use_conflict(
    cred_writer_client: TestClient,
    web_context: Context,
    clean_rule_sets: None,
) -> None:
    """A set a binding still points at cannot be deleted (409 rule_set_in_use).

    The binding's rule_set_id pointer is FK-less across the DB seam, so this
    application-level refusal is the only thing keeping a set from vanishing
    under bindings that still evaluate through it."""
    credential_id = _create_api_key(cred_writer_client)
    set_id = cred_writer_client.post(
        "/permission-rule-sets", json={"name": "in-use", "rules": _RULES}
    ).json()["rule_set_id"]

    agent_id = "agnt_ruleset_inuse"
    async with web_context.admin_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO agents (id, name, registered_by, status, created_by) "
                "VALUES (:id, 'ruleset-inuse', 'usr_webtest_cred_writer', 'pending', 'usr_test') "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": agent_id},
        )
        await AgentCredentialBindingRepository.bind(
            session, agent_id=agent_id, credential_id=credential_id, created_by="usr_test"
        )
        await session.execute(
            text(
                "UPDATE agent_credential_bindings SET rule_set_id = :sid "
                "WHERE agent_id = :aid AND credential_id = :cid"
            ),
            {"sid": set_id, "aid": agent_id, "cid": credential_id},
        )
    try:
        resp = cred_writer_client.delete(f"/permission-rule-sets/{set_id}")
        assert resp.status_code == 409
        assert resp.json()["type"] == "rule_set_in_use"

        # get still reports the reference.
        assert (
            cred_writer_client.get(f"/permission-rule-sets/{set_id}").json()["binding_count"] == 1
        )

        # Detach, then deletion goes through.
        async with web_context.admin_db.transaction() as session:
            await session.execute(
                text(
                    "UPDATE agent_credential_bindings SET rule_set_id = NULL "
                    "WHERE rule_set_id = :sid"
                ),
                {"sid": set_id},
            )
        assert cred_writer_client.delete(f"/permission-rule-sets/{set_id}").status_code == 204
    finally:
        async with web_context.admin_db.session() as session:
            await session.execute(
                text("DELETE FROM agent_credential_bindings WHERE agent_id = :aid"),
                {"aid": agent_id},
            )
            await session.execute(text("DELETE FROM agents WHERE id = :aid"), {"aid": agent_id})
            await session.commit()


def test_rule_set_mutations_gated_to_creator_or_admin(
    cred_writer_client: TestClient,
    plain_writer_client: TestClient,
    clean_rule_sets: None,
) -> None:
    """Provisional OQ-6 gate: a non-admin non-creator with credentials:write can
    read a shared set but not mutate it; their own sets they can mutate."""
    set_id = cred_writer_client.post("/permission-rule-sets", json={"name": "admins-set"}).json()[
        "rule_set_id"
    ]

    # Non-creator, non-admin: read OK, mutate 403.
    assert plain_writer_client.get(f"/permission-rule-sets/{set_id}").status_code == 200
    for resp in (
        plain_writer_client.patch(f"/permission-rule-sets/{set_id}", json={"name": "hijack"}),
        plain_writer_client.put(f"/permission-rule-sets/{set_id}/rules", json=[]),
        plain_writer_client.delete(f"/permission-rule-sets/{set_id}"),
    ):
        assert resp.status_code == 403
        assert resp.json()["type"] == "rule_set_access_denied"

    # Their own set they fully control.
    own_id = plain_writer_client.post("/permission-rule-sets", json={"name": "writers-own"}).json()[
        "rule_set_id"
    ]
    assert (
        plain_writer_client.patch(
            f"/permission-rule-sets/{own_id}", json={"description": "mine"}
        ).status_code
        == 200
    )
    assert plain_writer_client.delete(f"/permission-rule-sets/{own_id}").status_code == 204

    # org:admin edits anyone's.
    assert cred_writer_client.delete(f"/permission-rule-sets/{set_id}").status_code == 204


def test_rule_set_write_needs_write_scope(
    delegated_agent_client: TestClient, cred_writer_client: TestClient, clean_rule_sets: None
) -> None:
    """owner:credentials:read admits reads but never writes."""
    set_id = cred_writer_client.post("/permission-rule-sets", json={"name": "scope-gate"}).json()[
        "rule_set_id"
    ]
    assert delegated_agent_client.get(f"/permission-rule-sets/{set_id}").status_code == 200
    assert (
        delegated_agent_client.post("/permission-rule-sets", json={"name": "nope"}).status_code
        == 403
    )
    assert delegated_agent_client.delete(f"/permission-rule-sets/{set_id}").status_code == 403


def test_rule_set_unknown_id_is_404(cred_writer_client: TestClient) -> None:
    resp = cred_writer_client.get("/permission-rule-sets/prs_nonexistent")
    assert resp.status_code == 404
    assert resp.json()["type"] == "rule_set_not_found"


async def test_rule_set_attach_lifecycle(
    cred_writer_client: TestClient,
    bound_agents: tuple[str, list[str]],
    clean_rule_sets: None,
) -> None:
    """Attach a shared set to a binding: the reverse lookup shows the pointer,
    permissions:test evaluates the set's rules (inline rules go dormant, not
    lost), and detach restores inline evaluation. Attach/detach are idempotent."""
    credential_id, (agent_id, _) = bound_agents
    binding_base = f"/credentials/{credential_id}/agents/{agent_id}"

    # Inline rules allow GET /inline only.
    put = cred_writer_client.put(
        f"{binding_base}/permissions",
        json=[{"effect": "allow", "methods": ["GET"], "path": "/inline"}],
    )
    assert put.status_code == 200, put.text

    # Shared set allows GET /shared only.
    set_id = cred_writer_client.post(
        "/permission-rule-sets",
        json={
            "name": "attach-test",
            "rules": [{"effect": "allow", "methods": ["GET"], "path": "/shared"}],
        },
    ).json()["rule_set_id"]

    # Before attach: inline rules govern.
    got = cred_writer_client.post(
        f"{binding_base}/permissions:test", json={"method": "GET", "path": "/inline"}
    ).json()
    assert got["allowed"] is True
    got = cred_writer_client.post(
        f"{binding_base}/permissions:test", json={"method": "GET", "path": "/shared"}
    ).json()
    assert got["allowed"] is False

    # Attach (twice — idempotent PUT).
    for _ in range(2):
        assert (
            cred_writer_client.put(
                f"{binding_base}/rule-set", json={"rule_set_id": set_id}
            ).status_code
            == 204
        )

    # Reverse lookup shows the pointer.
    rows = {
        r["agent_id"]: r
        for r in cred_writer_client.get(f"/credentials/{credential_id}/agents").json()["data"]
    }
    assert rows[agent_id]["rule_set_id"] == set_id

    # While attached: the set's list is the effective policy.
    got = cred_writer_client.post(
        f"{binding_base}/permissions:test", json={"method": "GET", "path": "/shared"}
    ).json()
    assert got["allowed"] is True
    got = cred_writer_client.post(
        f"{binding_base}/permissions:test", json={"method": "GET", "path": "/inline"}
    ).json()
    assert got["allowed"] is False

    # The attached set cannot be deleted out from under the binding.
    resp = cred_writer_client.delete(f"/permission-rule-sets/{set_id}")
    assert resp.status_code == 409
    assert resp.json()["type"] == "rule_set_in_use"

    # Detach (twice — idempotent): inline rules govern again, delete succeeds.
    for _ in range(2):
        assert cred_writer_client.delete(f"{binding_base}/rule-set").status_code == 204
    got = cred_writer_client.post(
        f"{binding_base}/permissions:test", json={"method": "GET", "path": "/inline"}
    ).json()
    assert got["allowed"] is True
    assert cred_writer_client.delete(f"/permission-rule-sets/{set_id}").status_code == 204


async def test_rule_set_attach_errors(
    cred_writer_client: TestClient,
    bound_agents: tuple[str, list[str]],
    clean_rule_sets: None,
) -> None:
    """Attach requires an existing set and an existing binding — both 404."""
    credential_id, (agent_id, _) = bound_agents

    resp = cred_writer_client.put(
        f"/credentials/{credential_id}/agents/{agent_id}/rule-set",
        json={"rule_set_id": "prs_nonexistent"},
    )
    assert resp.status_code == 404
    assert resp.json()["type"] == "rule_set_not_found"

    set_id = cred_writer_client.post(
        "/permission-rule-sets", json={"name": "orphan-attach"}
    ).json()["rule_set_id"]
    resp = cred_writer_client.put(
        f"/credentials/{credential_id}/agents/agnt_never_bound/rule-set",
        json={"rule_set_id": set_id},
    )
    assert resp.status_code == 404
    assert resp.json()["type"] == "agent_binding_not_found"


def test_rule_set_attach_needs_write_scope(
    delegated_agent_client: TestClient, bound_agents: tuple[str, list[str]]
) -> None:
    """owner:credentials:read admits binding reads but never rule-set writes."""
    credential_id, (agent_id, _) = bound_agents
    base = f"/credentials/{credential_id}/agents/{agent_id}/rule-set"
    assert delegated_agent_client.put(base, json={"rule_set_id": "prs_x"}).status_code == 403
    assert delegated_agent_client.delete(base).status_code == 403


# --- Direct-binding visibility widening (theme 5 phase 1) ---


def _agent_client(web_context: Context, agent_id: str) -> TestClient:
    """A bound-but-orphaned agent identity (owns nothing, delegated read only).

    Mirrors BOUND_ORPHAN_IDENTITY in conftest: it passes the route gate via
    owner:credentials:read, so any credential it can actually read must owe
    that visibility purely to its own direct binding.
    """
    identity = Identity(
        sub=agent_id,
        email=f"{agent_id}@test.local",
        permissions=["owner:credentials:read"],
        actor_type=ActorType.AGENT,
        parent_actor_id=None,
    )
    return TestClient(_build_app(web_context, identity))


async def test_direct_binding_widens_credential_visibility(
    cred_writer_client: TestClient,
    web_context: Context,
    bound_agents: tuple[str, list[str]],
) -> None:
    """An agent with an active direct binding can read the bound credential it
    doesn't own (get + list); a suspended binding grants no visibility — the
    cut-off cuts credential reads too, not just execution."""
    credential_id, (active_id, suspended_id) = bound_agents

    with _agent_client(web_context, active_id) as client:
        resp = client.get(f"/credentials/{credential_id}")
        assert resp.status_code == 200, resp.text
        listed = client.get("/credentials").json()
        assert credential_id in {c["credential_id"] for c in listed["data"]}

    with _agent_client(web_context, suspended_id) as client:
        resp = client.get(f"/credentials/{credential_id}")
        assert resp.status_code == 404
        assert resp.json()["type"] == "credential_not_found"
        listed = client.get("/credentials").json()
        assert credential_id not in {c["credential_id"] for c in listed["data"]}

    # And an agent with no binding at all sees nothing (unchanged baseline).
    with _agent_client(web_context, "agnt_never_bound") as client:
        assert client.get(f"/credentials/{credential_id}").status_code == 404
