"""Web tests for the access-requests HTTP surface (theme-5 Phase 3 vocabulary).

``credential:bind`` binds the filing agent directly to a credential; the
toolkit vocabulary is rejected at the Pydantic boundary; every bind carries a
policy (inline ``rules`` or a ``rule_set_id``); a provisioning plan is the
2-item ``credential:provision`` + ``credential:bind`` chain.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from jentic_one.shared.context import Context

from .conftest import FILER_SUB, OWNER_SUB

pytestmark = pytest.mark.integration

_DEFAULT_RULES = [{"effect": "allow", "methods": ["GET"]}]


@pytest.fixture(autouse=True)
async def clean_bind_artifacts(web_context: Context) -> AsyncGenerator[None, None]:
    """Remove binding rows + rules that approved credential:bind items create.

    The seed_binding fixture owns the agent/credential rows, but a decide()
    that approves a bind writes an ``agent_credential_bindings`` row (admin)
    and ``agent_permission_rules`` rows (control) keyed by the filer agent —
    left behind, they would flip a later test's ``already_satisfied`` probe or
    ``already_bound`` assertion.
    """

    async def _cleanup() -> None:
        async with web_context.admin_db.session() as session:
            await session.execute(
                text("DELETE FROM agent_credential_bindings WHERE agent_id = :aid"),
                {"aid": FILER_SUB},
            )
            await session.commit()
        async with web_context.control_db.session() as session:
            await session.execute(
                text("DELETE FROM agent_permission_rules WHERE agent_id = :aid"),
                {"aid": FILER_SUB},
            )
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


def _file_request(client: TestClient, **item_overrides: Any) -> dict[str, Any]:
    """Helper: file a single-item credential:bind request, return the body."""
    item: dict[str, Any] = {
        "resource_type": "credential",
        "action": "bind",
        "resource_id": "cred_001",
    }
    item.update(item_overrides)
    resp = client.post(
        "/access-requests",
        json={"reason": "Need access", "items": [item]},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()  # type: ignore[no-any-return]


# --- File ---


def test_file_returns_202(filer_client: TestClient) -> None:
    data = _file_request(filer_client)
    assert data["id"].startswith("areq_")
    assert data["status"] == "pending"
    assert "/access-requests/" in data["approve_url"]
    assert data["created_by"] == FILER_SUB


def test_file_stamps_default_rules_on_bind(filer_client: TestClient) -> None:
    """A credential:bind filed with no policy gets the read-only default rules
    stamped (hard problem 6: a rule-less bind is a live default-deny the
    operator believes granted, so filing substitutes the safe default)."""
    data = _file_request(filer_client)
    item = data["items"][0]
    assert item["rules"] == _DEFAULT_RULES
    assert item["rule_set_id"] is None


def test_file_rule_set_id_round_trips(filer_client: TestClient) -> None:
    """A shared-set pointer is the alternative policy carrier: filing with it
    suppresses the default rules and the pointer rides through file + GET."""
    data = _file_request(filer_client, rule_set_id="prs_webtest_rt")
    item = data["items"][0]
    assert item["rule_set_id"] == "prs_webtest_rt"
    assert item["rules"] is None

    got = filer_client.get(f"/access-requests/{data['id']}").json()
    assert got["items"][0]["rule_set_id"] == "prs_webtest_rt"


def test_file_duplicate_returns_409(filer_client: TestClient) -> None:
    first = _file_request(filer_client)
    resp = filer_client.post(
        "/access-requests",
        json={
            "items": [
                {
                    "resource_type": "credential",
                    "action": "bind",
                    "resource_id": "cred_001",
                }
            ],
        },
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["type"] == "access_request_duplicate_pending"
    assert body["approve_url"] == first["approve_url"]
    assert body["existing_request_id"] == first["id"]


def test_file_toolkit_vocabulary_rejected_by_schema(filer_client: TestClient) -> None:
    """Theme-5 Phase 3: toolkit:create / toolkit:bind are unrepresentable — the
    resource_type Literal rejects them before any service code runs."""
    for action in ("create", "bind"):
        resp = filer_client.post(
            "/access-requests",
            json={"items": [{"resource_type": "toolkit", "action": action}]},
        )
        assert resp.status_code == 422, resp.text


def test_file_rules_and_rule_set_id_rejected(filer_client: TestClient) -> None:
    """Inline rules and a shared-set pointer are mutually exclusive carriers."""
    resp = filer_client.post(
        "/access-requests",
        json={
            "items": [
                {
                    "resource_type": "credential",
                    "action": "bind",
                    "resource_id": "cred_001",
                    "rules": [{"effect": "allow", "methods": ["GET"]}],
                    "rule_set_id": "prs_1",
                }
            ],
        },
    )
    assert resp.status_code == 422, resp.text


def test_file_rules_on_scope_grant_returns_422(filer_client: TestClient) -> None:
    """Rules attached to a scope:grant can't be enforced (no binding key) — reject."""
    resp = filer_client.post(
        "/access-requests",
        json={
            "items": [
                {
                    "resource_type": "scope",
                    "action": "grant",
                    "resource_id": "apis:write",
                    "rules": [{"effect": "allow", "methods": ["GET"]}],
                }
            ],
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["type"] == "access_request_rules_not_supported_for_bind"


def test_file_rule_set_id_on_scope_grant_returns_422(filer_client: TestClient) -> None:
    """The shared-set pointer is a policy carrier too — same rejection as rules."""
    resp = filer_client.post(
        "/access-requests",
        json={
            "items": [
                {
                    "resource_type": "scope",
                    "action": "grant",
                    "resource_id": "apis:write",
                    "rule_set_id": "prs_1",
                }
            ],
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["type"] == "access_request_rules_not_supported_for_bind"


def test_amend_rules_onto_scope_grant_returns_422(filer_client: TestClient) -> None:
    """The amend back door is closed too: rules can't be stitched onto a scope:grant."""
    filed = filer_client.post(
        "/access-requests",
        json={
            "items": [{"resource_type": "scope", "action": "grant", "resource_id": "apis:write"}],
        },
    )
    assert filed.status_code == 202, filed.text
    data = filed.json()
    item_id = data["items"][0]["id"]
    resp = filer_client.post(
        f"/access-requests/{data['id']}:amend",
        json={"items": [{"item_id": item_id, "rules": [{"effect": "allow", "methods": ["GET"]}]}]},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["type"] == "access_request_rules_not_supported_for_bind"


# --- List ---


def test_list_returns_pagination_envelope(filer_client: TestClient) -> None:
    _file_request(filer_client)
    resp = filer_client.get("/access-requests")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "has_more" in body
    assert "next_cursor" in body
    assert len(body["data"]) == 1


def test_list_filters_by_actor_id(filer_client: TestClient) -> None:
    _file_request(filer_client)
    resp = filer_client.get(f"/access-requests?actor_id={FILER_SUB}")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1

    resp = filer_client.get("/access-requests?actor_id=unknown_actor")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 0


def test_list_filters_by_status(filer_client: TestClient) -> None:
    _file_request(filer_client)
    resp = filer_client.get("/access-requests?status=pending")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1

    resp = filer_client.get("/access-requests?status=approved")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 0


def test_list_respects_limit(filer_client: TestClient) -> None:
    resp = filer_client.get("/access-requests?limit=1")
    assert resp.status_code == 200


# --- Filer-owner enrichment ---


@pytest.fixture()
async def seed_owner_user(web_context: Context) -> AsyncGenerator[None, None]:
    """Seed the filer's owner (OWNER_SUB) as a real admin-DB user for enrichment.

    Raw SQL rather than ``UserService`` deliberately, mirroring the
    ``seed_binding`` precedent in conftest: these web tests exercise the
    control app, and booting the admin service stack just to plant one roster
    row would couple them to admin bootstrapping. Teardown only removes the
    row if this fixture actually inserted it (the seed is a no-op when a
    concurrent suite already owns the id).
    """
    async with web_context.admin_db.session() as session:
        existing = await session.execute(
            text("SELECT 1 FROM users WHERE id = :id"), {"id": OWNER_SUB}
        )
        created = existing.scalar_one_or_none() is None
        if created:
            await session.execute(
                text(
                    "INSERT INTO users (id, email, first_name, last_name) "
                    "VALUES (:id, :email, :first, :last)"
                ),
                {"id": OWNER_SUB, "email": "owner@test.local", "first": "Olive", "last": "Owner"},
            )
        await session.commit()
    yield
    if created:
        async with web_context.admin_db.session() as session:
            await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": OWNER_SUB})
            await session.commit()


def test_list_and_get_enrich_filer_owner(filer_client: TestClient, seed_owner_user: None) -> None:
    """When filer_owner_id resolves to a user, list/get carry its display info."""
    data = _file_request(filer_client)
    listed = filer_client.get("/access-requests").json()["data"][0]
    assert listed["filer_owner_id"] == OWNER_SUB
    assert listed["filer_owner"] == {
        "id": OWNER_SUB,
        "email": "owner@test.local",
        "display_name": "Olive Owner",
    }
    got = filer_client.get(f"/access-requests/{data['id']}").json()
    assert got["filer_owner"] == listed["filer_owner"]


def test_filer_owner_absent_when_id_does_not_resolve(filer_client: TestClient) -> None:
    """No admin-DB user behind filer_owner_id → the optional field stays null."""
    data = _file_request(filer_client)
    got = filer_client.get(f"/access-requests/{data['id']}").json()
    assert got["filer_owner_id"] == OWNER_SUB
    assert got["filer_owner"] is None


async def test_filer_owner_falls_back_to_created_by(
    owner_client: TestClient,
    seed_owner_user: None,
    web_context: Context,
) -> None:
    """Null filer_owner_id (legacy rows) resolves via created_by — the same
    fallback consumers render — so the label doesn't silently vanish."""
    # A user-filed scope:grant (created_by == OWNER_SUB, a real admin-DB user).
    filed = owner_client.post(
        "/access-requests",
        json={
            "items": [{"resource_type": "scope", "action": "grant", "resource_id": "apis:write"}]
        },
    )
    assert filed.status_code == 202, filed.text
    request_id = filed.json()["id"]
    async with web_context.control_db.session() as session:
        await session.execute(
            text("UPDATE access_requests SET filer_owner_id = NULL WHERE id = :id"),
            {"id": request_id},
        )
        await session.commit()
    got = owner_client.get(f"/access-requests/{request_id}").json()
    assert got["filer_owner_id"] is None
    assert got["filer_owner"] == {
        "id": OWNER_SUB,
        "email": "owner@test.local",
        "display_name": "Olive Owner",
    }


# --- Get ---


def test_get_includes_evaluation(filer_client: TestClient, owner_client: TestClient) -> None:
    data = _file_request(filer_client)
    resp = owner_client.get(f"/access-requests/{data['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["evaluation"] is not None
    assert "can_fulfill" in body["evaluation"]
    assert "checks" in body["evaluation"]


def test_get_not_found_returns_404(filer_client: TestClient) -> None:
    resp = filer_client.get("/access-requests/areq_nonexistent")
    assert resp.status_code == 404
    assert resp.json()["type"] == "access_request_not_found"


# --- already_satisfied enrichment (issue #826) ---


async def test_credential_bind_already_satisfied_flips_on_manual_binding(
    filer_client: TestClient, owner_client: TestClient, web_context: Context
) -> None:
    """A pending credential:bind flips False → True once the direct binding exists.

    The manual-fulfilment loop: an operator binds the agent to the credential
    by hand (outside the wizard), and the request's GET now reports the item
    as already in effect so the reviewer can approve instead of re-doing it.
    List pages skip the enrichment (null) by design, and a viewer who cannot
    see the credential gets no hint at all (null) — the probe mirrors
    decide-time credential visibility so it can't be used as a
    binding-existence oracle for amended-in foreign ids.
    """
    data = _file_request(filer_client)  # credential:bind → cred_001 (owned by OWNER_SUB)
    got = owner_client.get(f"/access-requests/{data['id']}").json()
    assert got["items"][0]["already_satisfied"] is False

    # The filer agent can't see cred_001 (owned by OWNER_SUB, no delegation
    # scope) → hint not computed for it.
    got = filer_client.get(f"/access-requests/{data['id']}").json()
    assert got["items"][0]["already_satisfied"] is None

    listed = owner_client.get("/access-requests").json()["data"][0]
    assert listed["items"][0]["already_satisfied"] is None

    async with web_context.admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings (id, agent_id, credential_id) "
                "VALUES ('acb_webtest_sat', :agent, 'cred_001') ON CONFLICT DO NOTHING"
            ),
            {"agent": FILER_SUB},
        )
        await session.commit()
    # clean_bind_artifacts removes the row after the test.
    got = owner_client.get(f"/access-requests/{data['id']}").json()
    assert got["items"][0]["already_satisfied"] is True
    # The satisfying credential is named so consumers can point at the object.
    assert got["items"][0]["already_satisfied_by"] == "cred_001"


def test_credential_bind_hint_null_once_decided(
    filer_client: TestClient, owner_client: TestClient
) -> None:
    """Once decided, the satisfaction hint is no longer computed (null)."""
    data = _file_request(filer_client)
    item_id = data["items"][0]["id"]
    decided = owner_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": item_id, "decision": "approved"}]},
    )
    assert decided.status_code == 200, decided.text
    got = owner_client.get(f"/access-requests/{data['id']}").json()
    assert got["items"][0]["status"] == "approved"
    assert got["items"][0]["already_satisfied"] is None
    assert got["items"][0]["already_satisfied_by"] is None


@pytest.fixture()
async def seed_reference_credential(web_context: Context) -> AsyncGenerator[str, None]:
    """A canonical-vendor credential owned by OWNER_SUB for reference tests."""
    async with web_context.control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO credentials (id, type, name, api_vendor, created_by) "
                "VALUES ('cred_refsat', 'token_value', 'cred-refsat', 'webtest-refsat', :owner) "
                "ON CONFLICT DO NOTHING"
            ),
            {"owner": OWNER_SUB},
        )
        await session.commit()
    yield "cred_refsat"
    async with web_context.control_db.session() as session:
        await session.execute(text("DELETE FROM credentials WHERE id = 'cred_refsat'"))
        await session.commit()


async def test_credential_bind_by_reference_already_satisfied(
    filer_client: TestClient,
    owner_client: TestClient,
    web_context: Context,
    seed_reference_credential: str,
) -> None:
    """A reference-only credential:bind resolves under the viewer's owner axis
    and reports True when the agent is already bound to the resolved credential."""
    async with web_context.admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings (id, agent_id, credential_id) "
                "VALUES ('acb_refsat', :agent, 'cred_refsat') ON CONFLICT DO NOTHING"
            ),
            {"agent": FILER_SUB},
        )
        await session.commit()
    filed = _file_request(
        filer_client, resource_id=None, resource_reference={"vendor": "webtest-refsat"}
    )

    # The owner sees cred_refsat (they own it): the reference resolves to it
    # and the agent is already bound → True, naming the credential.
    got = owner_client.get(f"/access-requests/{filed['id']}").json()
    assert got["items"][0]["already_satisfied"] is True
    assert got["items"][0]["already_satisfied_by"] == "cred_refsat"

    # The filer agent owns nothing and its owner axis resolves no covering
    # credential — determinately unsatisfied under ITS scope.
    got = filer_client.get(f"/access-requests/{filed['id']}").json()
    assert got["items"][0]["already_satisfied"] is False
    assert got["items"][0]["already_satisfied_by"] is None


async def test_credential_bind_raw_vendor_reference_is_slugified(
    filer_client: TestClient,
    owner_client: TestClient,
    web_context: Context,
    seed_reference_credential: str,
) -> None:
    """A reference filed with a raw vendor still resolves: the annotator
    slugifies it before matching the canonical (slugified-on-write) rows."""
    async with web_context.admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings (id, agent_id, credential_id) "
                "VALUES ('acb_rawsat', :agent, 'cred_refsat') ON CONFLICT DO NOTHING"
            ),
            {"agent": FILER_SUB},
        )
        await session.commit()
    filed = _file_request(
        # Raw, unslugged — as agents actually file them.
        filer_client,
        resource_id=None,
        resource_reference={"vendor": "Webtest.Refsat"},
    )
    got = owner_client.get(f"/access-requests/{filed['id']}").json()
    assert got["items"][0]["already_satisfied"] is True
    assert got["items"][0]["already_satisfied_by"] == "cred_refsat"


async def test_credential_bind_ambiguous_reference_not_annotated(
    filer_client: TestClient,
    owner_client: TestClient,
    web_context: Context,
    seed_reference_credential: str,
) -> None:
    """A reference resolving to several credentials stays null: decide-time
    resolution would refuse it (CredentialReferenceAmbiguousError), so a hint
    would advertise an approval that cannot succeed as filed."""
    async with web_context.control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO credentials (id, type, name, api_vendor, created_by) "
                "VALUES ('cred_refsat_2', 'token_value', 'cred-refsat-2', 'webtest-refsat', "
                ":owner) ON CONFLICT DO NOTHING"
            ),
            {"owner": OWNER_SUB},
        )
        await session.commit()
    try:
        filed = _file_request(
            filer_client, resource_id=None, resource_reference={"vendor": "webtest-refsat"}
        )
        got = owner_client.get(f"/access-requests/{filed['id']}").json()
        assert got["items"][0]["already_satisfied"] is None
        assert got["items"][0]["already_satisfied_by"] is None
    finally:
        async with web_context.control_db.session() as session:
            await session.execute(text("DELETE FROM credentials WHERE id = 'cred_refsat_2'"))
            await session.commit()


async def test_scope_grant_already_satisfied_flips_on_manual_grant(
    filer_client: TestClient, web_context: Context
) -> None:
    """A pending scope:grant flips False → True once the actor holds the scope."""
    filed = filer_client.post(
        "/access-requests",
        json={
            "items": [{"resource_type": "scope", "action": "grant", "resource_id": "apis:write"}]
        },
    )
    assert filed.status_code == 202, filed.text
    request_id = filed.json()["id"]

    got = filer_client.get(f"/access-requests/{request_id}").json()
    assert got["items"][0]["already_satisfied"] is False

    try:
        async with web_context.admin_db.session() as session:
            await session.execute(
                text(
                    "INSERT INTO actor_scope_grants (id, actor_id, actor_type, scope, granted_by) "
                    "VALUES ('asg_webtest_sat', :actor, 'agent', 'apis:write', :granted_by) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"actor": FILER_SUB, "granted_by": OWNER_SUB},
            )
            await session.commit()
        got = filer_client.get(f"/access-requests/{request_id}").json()
        assert got["items"][0]["already_satisfied"] is True
    finally:
        async with web_context.admin_db.session() as session:
            await session.execute(
                text("DELETE FROM actor_scope_grants WHERE id = 'asg_webtest_sat'")
            )
            await session.commit()


# --- Decide ---


def test_decide_approve_returns_200(filer_client: TestClient, owner_client: TestClient) -> None:
    data = _file_request(filer_client)
    item_id = data["items"][0]["id"]
    resp = owner_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": item_id, "decision": "approved"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    approved_item = next(i for i in body["items"] if i["id"] == item_id)
    effects = approved_item["applied_effects"]
    assert effects is not None
    # credential:bind records the direct agent↔credential binding it created.
    assert effects["binding_id"].startswith("acb_")
    assert effects["credential_id"] == "cred_001"
    assert effects["rules_applied"] == 1  # the stamped default rule
    assert effects["already_bound"] is False


async def test_decide_approve_writes_binding_and_rules(
    filer_client: TestClient, owner_client: TestClient, web_context: Context
) -> None:
    """The two-stage effect actually lands both halves: the control-DB rules
    and the admin-DB binding row (rule-less binds must be impossible)."""
    data = _file_request(filer_client)
    item_id = data["items"][0]["id"]
    resp = owner_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": item_id, "decision": "approved"}]},
    )
    assert resp.status_code == 200, resp.text

    async with web_context.admin_db.session() as session:
        binding = await session.execute(
            text(
                "SELECT id FROM agent_credential_bindings "
                "WHERE agent_id = :agent AND credential_id = 'cred_001'"
            ),
            {"agent": FILER_SUB},
        )
        assert binding.scalar_one_or_none() is not None
    async with web_context.control_db.session() as session:
        rules = await session.execute(
            text(
                "SELECT effect FROM agent_permission_rules "
                "WHERE agent_id = :agent AND credential_id = 'cred_001'"
            ),
            {"agent": FILER_SUB},
        )
        assert [row[0] for row in rules.fetchall()] == ["allow"]


def test_decide_unresolved_reference_denies_with_reason(
    filer_client: TestClient, owner_client: TestClient
) -> None:
    """Approving a bind whose reference no visible credential covers converts
    to a DENY-with-reason (200), closing the agent's --wait loop — not a 422
    that would strand the request pending (#696)."""
    data = _file_request(
        filer_client,
        resource_id=None,
        resource_reference={"vendor": "no-such-vendor", "name": "no-such-api"},
    )
    item_id = data["items"][0]["id"]
    resp = owner_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": item_id, "decision": "approved"}]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "denied"
    item = body["items"][0]
    assert item["status"] == "denied"
    assert "No credential covers API" in item["decision_reason"]


def test_decide_ambiguous_reference_returns_409(
    filer_client: TestClient, owner_client: TestClient, seed_two_covering_credentials: None
) -> None:
    """An ambiguous reference RAISES (409) so the request stays pending while
    the operator amends an explicit resource_id — unlike the unresolved case,
    which denies."""
    data = _file_request(
        filer_client, resource_id=None, resource_reference={"vendor": "webtest-ambig"}
    )
    item_id = data["items"][0]["id"]
    resp = owner_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": item_id, "decision": "approved"}]},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["type"] == "access_request_credential_ambiguous"
    # The request is still pending — the raise rolled the decision back.
    got = owner_client.get(f"/access-requests/{data['id']}").json()
    assert got["status"] == "pending"


@pytest.fixture()
async def seed_two_covering_credentials(web_context: Context) -> AsyncGenerator[None, None]:
    """Two credentials covering the same vendor, both visible to the owner."""
    async with web_context.control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO credentials (id, type, name, api_vendor, created_by) VALUES "
                "('cred_ambig_1', 'token_value', 'ambig-1', 'webtest-ambig', :owner), "
                "('cred_ambig_2', 'token_value', 'ambig-2', 'webtest-ambig', :owner) "
                "ON CONFLICT DO NOTHING"
            ),
            {"owner": OWNER_SUB},
        )
        await session.commit()
    yield
    async with web_context.control_db.session() as session:
        await session.execute(
            text("DELETE FROM credentials WHERE id IN ('cred_ambig_1', 'cred_ambig_2')")
        )
        await session.commit()


async def test_decide_rules_less_stored_item_returns_422(
    filer_client: TestClient, owner_client: TestClient, web_context: Context
) -> None:
    """A stored bind whose policy was stripped (legacy row) RAISES at decide —
    422 access_request_rules_required_for_bind — keeping the request pending
    while the operator amends rules back on. Filing always substitutes a
    default, so only direct DB state can produce this."""
    data = _file_request(filer_client)
    item_id = data["items"][0]["id"]
    async with web_context.control_db.session() as session:
        await session.execute(
            text("UPDATE access_request_items SET rules = NULL WHERE id = :id"),
            {"id": item_id},
        )
        await session.commit()
    resp = owner_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": item_id, "decision": "approved"}]},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["type"] == "access_request_rules_required_for_bind"


def test_decide_missing_rule_set_returns_422(
    filer_client: TestClient, owner_client: TestClient
) -> None:
    """A rule_set_id must exist in permission_rule_sets at decide time — a
    dangling pointer would approve into a live default-deny binding."""
    data = _file_request(filer_client, rule_set_id="prs_nonexistent")
    item_id = data["items"][0]["id"]
    resp = owner_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": item_id, "decision": "approved"}]},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["type"] == "access_request_rule_set_not_found"


@pytest.fixture()
async def seed_rule_set(web_context: Context) -> AsyncGenerator[str, None]:
    """A real permission_rule_sets row the decide-time validation can resolve."""
    async with web_context.control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO permission_rule_sets (id, name, created_by) "
                "VALUES ('prs_webtest_1', 'webtest-set', :owner) ON CONFLICT DO NOTHING"
            ),
            {"owner": OWNER_SUB},
        )
        await session.commit()
    yield "prs_webtest_1"
    async with web_context.control_db.session() as session:
        await session.execute(text("DELETE FROM permission_rule_sets WHERE id = 'prs_webtest_1'"))
        await session.commit()


async def test_decide_rule_set_bind_applies_pointer(
    filer_client: TestClient,
    owner_client: TestClient,
    web_context: Context,
    seed_rule_set: str,
) -> None:
    """A rule_set_id bind approves with the pointer on the admin binding row and
    NO inline control rules — the set's ordered list is the effective policy."""
    data = _file_request(filer_client, rule_set_id=seed_rule_set)
    item_id = data["items"][0]["id"]
    resp = owner_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": item_id, "decision": "approved"}]},
    )
    assert resp.status_code == 200, resp.text
    effects = resp.json()["items"][0]["applied_effects"]
    assert effects["rule_set_id"] == seed_rule_set
    assert effects["rules_applied"] == 0

    async with web_context.admin_db.session() as session:
        row = await session.execute(
            text(
                "SELECT rule_set_id FROM agent_credential_bindings "
                "WHERE agent_id = :agent AND credential_id = 'cred_001'"
            ),
            {"agent": FILER_SUB},
        )
        assert row.scalar_one() == seed_rule_set
    async with web_context.control_db.session() as session:
        rules = await session.execute(
            text(
                "SELECT count(*) FROM agent_permission_rules "
                "WHERE agent_id = :agent AND credential_id = 'cred_001'"
            ),
            {"agent": FILER_SUB},
        )
        assert rules.scalar_one() == 0


async def test_decide_stored_legacy_toolkit_item_returns_422(
    filer_client: TestClient, owner_client: TestClient, web_context: Context
) -> None:
    """A stored pre-Phase-3 toolkit item (one the auto-withdraw migration
    missed, or a raced filing) hard-fails decide with 422
    access_request_unsupported_item — never the old silent skip."""
    data = _file_request(filer_client)
    async with web_context.control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO access_request_items "
                "(id, access_request_id, actor_id, resource_type, action, resource_id, status) "
                "VALUES ('arqi_legacy_tk', :req, :actor, 'toolkit', 'bind', 'tk_target', "
                "'pending')"
            ),
            {"req": data["id"], "actor": FILER_SUB},
        )
        await session.commit()
    resp = owner_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": "arqi_legacy_tk", "decision": "approved"}]},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["type"] == "access_request_unsupported_item"
    # The directive names the surviving verb so the caller can re-file.
    assert "credential" in body["detail"]


def test_decide_non_reviewer_returns_403(filer_client: TestClient) -> None:
    data = _file_request(filer_client)
    item_id = data["items"][0]["id"]
    resp = filer_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": item_id, "decision": "approved"}]},
    )
    assert resp.status_code == 403
    assert resp.json()["type"] == "access_request_not_reviewer"


def test_decide_not_pending_returns_409(filer_client: TestClient, owner_client: TestClient) -> None:
    data = _file_request(filer_client)
    item_id = data["items"][0]["id"]
    owner_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": item_id, "decision": "approved"}]},
    )
    resp = owner_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": item_id, "decision": "denied"}]},
    )
    assert resp.status_code == 409
    assert resp.json()["type"] == "access_request_item_not_pending"


def test_decide_item_not_on_request_returns_422(
    filer_client: TestClient, owner_client: TestClient
) -> None:
    data = _file_request(filer_client)
    resp = owner_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": "arqi_nonexistent", "decision": "approved"}]},
    )
    assert resp.status_code == 422
    assert resp.json()["type"] == "access_request_item_not_on_request"


# --- Amend ---


def test_amend_returns_200(filer_client: TestClient) -> None:
    data = _file_request(filer_client)
    item_id = data["items"][0]["id"]
    new_rules = [{"effect": "allow", "methods": ["GET", "POST"]}]
    resp = filer_client.post(
        f"/access-requests/{data['id']}:amend",
        json={"items": [{"item_id": item_id, "rules": new_rules}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    amended_item = next(i for i in body["items"] if i["id"] == item_id)
    assert amended_item["rules"] == [
        {"effect": "allow", "methods": ["GET", "POST"], "match_mode": "regex"}
    ]


def test_amend_policy_carriers_are_mutually_exclusive(
    filer_client: TestClient, owner_client: TestClient
) -> None:
    """Amending one policy carrier detaches the other: a stored item never
    carries both inline rules and a shared-set pointer."""
    data = _file_request(filer_client)  # default rules stamped
    item_id = data["items"][0]["id"]

    resp = filer_client.post(
        f"/access-requests/{data['id']}:amend",
        json={"items": [{"item_id": item_id, "rule_set_id": "prs_amended"}]},
    )
    assert resp.status_code == 200, resp.text
    item = next(i for i in resp.json()["items"] if i["id"] == item_id)
    assert item["rule_set_id"] == "prs_amended"
    assert item["rules"] is None

    resp = filer_client.post(
        f"/access-requests/{data['id']}:amend",
        json={"items": [{"item_id": item_id, "rules": [{"effect": "allow", "methods": ["GET"]}]}]},
    )
    assert resp.status_code == 200, resp.text
    item = next(i for i in resp.json()["items"] if i["id"] == item_id)
    assert item["rule_set_id"] is None
    assert item["rules"] is not None


def test_amend_resource_id_then_approve(filer_client: TestClient, owner_client: TestClient) -> None:
    """The wizard flow at the HTTP surface: a reference-only bind is amended
    with the concrete credential id, and the decide then resolves by id."""
    data = _file_request(
        filer_client, resource_id=None, resource_reference={"vendor": "not-covered-yet"}
    )
    item_id = data["items"][0]["id"]
    resp = filer_client.post(
        f"/access-requests/{data['id']}:amend",
        json={"items": [{"item_id": item_id, "resource_id": "cred_001"}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["resource_id"] == "cred_001"

    decided = owner_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": item_id, "decision": "approved"}]},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "approved"
    assert decided.json()["items"][0]["applied_effects"]["credential_id"] == "cred_001"


# --- Provisioning plan (2-item chain) at the HTTP surface ---


def test_plain_approve_of_unfulfilled_plan_denies_bind(
    filer_client: TestClient, owner_client: TestClient
) -> None:
    """A plan (credential:provision + credential:bind) approved WITHOUT the
    wizard's fulfilment denies the bind with the plan-aware reason; the intent
    approves as an audited no-op (SkippedEffect)."""
    resp = filer_client.post(
        "/access-requests",
        json={
            "items": [
                {
                    "resource_type": "credential",
                    "action": "provision",
                    "resource_reference": {"vendor": "brandnew", "name": "widgets"},
                },
                {
                    "resource_type": "credential",
                    "action": "bind",
                    "resource_reference": {"vendor": "brandnew", "name": "widgets"},
                },
            ],
        },
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()
    by_key = {(i["resource_type"], i["action"]): i for i in data["items"]}

    decided = owner_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": i["id"], "decision": "approved"} for i in data["items"]]},
    )
    assert decided.status_code == 200, decided.text
    decided_by_key = {(i["resource_type"], i["action"]): i for i in decided.json()["items"]}

    intent = decided_by_key[("credential", "provision")]
    assert intent["status"] == "approved"
    assert intent["applied_effects"]["skipped"] is True

    bind = decided_by_key[("credential", "bind")]
    assert bind["status"] == "denied"
    assert "provisioning plan" in bind["decision_reason"]
    # The denial names the awaiting intent so the operator can find it.
    assert by_key[("credential", "provision")]["id"] in bind["decision_reason"]


# --- Withdraw ---


def test_withdraw_returns_200(filer_client: TestClient) -> None:
    data = _file_request(filer_client)
    resp = filer_client.post(f"/access-requests/{data['id']}:withdraw")
    assert resp.status_code == 200
    assert resp.json()["status"] == "withdrawn"


def test_withdraw_not_pending_returns_409(filer_client: TestClient) -> None:
    data = _file_request(filer_client)
    filer_client.post(f"/access-requests/{data['id']}:withdraw")
    resp = filer_client.post(f"/access-requests/{data['id']}:withdraw")
    assert resp.status_code == 409
    assert resp.json()["type"] == "access_request_not_pending"


# --- Auth & Visibility ---


def test_missing_token_returns_401(unauthed_client: TestClient) -> None:
    resp = unauthed_client.get("/access-requests")
    assert resp.status_code == 401


def test_cross_user_get_returns_404(filer_client: TestClient, unrelated_client: TestClient) -> None:
    data = _file_request(filer_client)
    resp = unrelated_client.get(f"/access-requests/{data['id']}")
    assert resp.status_code == 404
    assert resp.json()["type"] == "access_request_not_found"


def test_admin_sees_all(filer_client: TestClient, admin_client: TestClient) -> None:
    data = _file_request(filer_client)
    resp = admin_client.get(f"/access-requests/{data['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == data["id"]
