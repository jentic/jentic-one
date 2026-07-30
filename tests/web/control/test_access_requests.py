"""Web tests for the access-requests HTTP surface."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from jentic_one.shared.context import Context

from .conftest import FILER_SUB, OWNER_SUB

pytestmark = pytest.mark.integration


def _file_request(client: TestClient) -> dict[str, Any]:
    """Helper: file an access request and return the JSON response body."""
    resp = client.post(
        "/access-requests",
        json={
            "reason": "Need access",
            "items": [
                {
                    "resource_type": "credential",
                    "action": "bind",
                    "resource_id": "cred_001",
                    "to_type": "toolkit",
                    "to_id": "tk_target",
                }
            ],
        },
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
                    "to_type": "toolkit",
                    "to_id": "tk_target",
                }
            ],
        },
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["type"] == "access_request_duplicate_pending"
    assert body["approve_url"] == first["approve_url"]
    assert body["existing_request_id"] == first["id"]


def test_file_rules_on_toolkit_bind_returns_422(filer_client: TestClient) -> None:
    """Rules attached to a toolkit:bind can't be enforced (no credential key) — reject."""
    resp = filer_client.post(
        "/access-requests",
        json={
            "items": [
                {
                    "resource_type": "toolkit",
                    "action": "bind",
                    "resource_id": "tk_target",
                    "rules": [{"effect": "allow", "methods": ["GET"]}],
                }
            ],
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["type"] == "access_request_rules_not_supported_for_bind"


def test_amend_rules_onto_toolkit_bind_returns_422(filer_client: TestClient) -> None:
    """The amend back door is closed too: rules can't be stitched onto a toolkit:bind."""
    filed = filer_client.post(
        "/access-requests",
        json={
            "items": [
                {
                    "resource_type": "toolkit",
                    "action": "bind",
                    "resource_id": "tk_target",
                }
            ],
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


def test_file_prerequisite_not_met_returns_403(owner_client: TestClient) -> None:
    resp = owner_client.post(
        "/access-requests",
        json={
            "items": [
                {
                    "resource_type": "credential",
                    "action": "bind",
                    "resource_id": "cred_x",
                    "to_type": "toolkit",
                    "to_id": "tk_no_binding",
                }
            ],
        },
    )
    assert resp.status_code == 403
    assert resp.json()["type"] == "access_request_prerequisite_not_met"


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
    # A toolkit bind: user-filed (created_by == OWNER_SUB, a real admin-DB
    # user) and exempt from the credential-bind prerequisite check.
    filed = owner_client.post(
        "/access-requests",
        json={
            "items": [{"resource_type": "toolkit", "action": "bind", "resource_id": "tk_target"}]
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
    filer_client: TestClient, web_context: Context
) -> None:
    """A pending credential:bind flips False → True once the binding exists.

    The manual-fulfilment loop: an operator binds the credential by hand
    (outside the wizard), and the request's GET now reports the item as
    already in effect so the reviewer can approve instead of re-doing it.
    List pages skip the enrichment (null) by design.
    """
    data = _file_request(filer_client)  # credential:bind cred_001 → tk_target
    got = filer_client.get(f"/access-requests/{data['id']}").json()
    assert got["items"][0]["already_satisfied"] is False

    listed = filer_client.get("/access-requests").json()["data"][0]
    assert listed["items"][0]["already_satisfied"] is None

    async with web_context.control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO toolkit_credential_bindings (id, toolkit_id, credential_id) "
                "VALUES ('tcb_webtest_sat', 'tk_target', 'cred_001') ON CONFLICT DO NOTHING"
            )
        )
        await session.commit()
    # seed_binding's teardown removes tk_target's bindings, so no local cleanup.
    got = filer_client.get(f"/access-requests/{data['id']}").json()
    assert got["items"][0]["already_satisfied"] is True


def test_toolkit_bind_already_satisfied_and_null_once_decided(
    filer_client: TestClient, owner_client: TestClient
) -> None:
    """A toolkit:bind whose agent↔toolkit binding already exists reports True;
    once decided the hint is no longer computed (null)."""
    # seed_binding already binds FILER_SUB to tk_target in the admin DB.
    filed = filer_client.post(
        "/access-requests",
        json={
            "items": [{"resource_type": "toolkit", "action": "bind", "resource_id": "tk_target"}]
        },
    )
    assert filed.status_code == 202, filed.text
    request_id = filed.json()["id"]
    item_id = filed.json()["items"][0]["id"]

    got = filer_client.get(f"/access-requests/{request_id}").json()
    assert got["items"][0]["already_satisfied"] is True

    decided = owner_client.post(
        f"/access-requests/{request_id}:decide",
        json={"items": [{"item_id": item_id, "decision": "approved"}]},
    )
    assert decided.status_code == 200, decided.text
    got = filer_client.get(f"/access-requests/{request_id}").json()
    assert got["items"][0]["status"] == "approved"
    assert got["items"][0]["already_satisfied"] is None


async def test_toolkit_bind_by_reference_already_satisfied(
    filer_client: TestClient, owner_client: TestClient, web_context: Context
) -> None:
    """A reference-only toolkit:bind resolves under the viewer's scope and
    reports True when the agent is already bound to a resolved toolkit."""
    # Bind a canonical-vendor credential to tk_target so the reference resolves
    # (seed_binding's cred_001 carries a raw, unslugged vendor on purpose).
    async with web_context.control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO credentials (id, type, name, api_vendor, created_by) "
                "VALUES ('cred_refsat', 'token_value', 'cred-refsat', 'webtest-refsat', :owner) "
                "ON CONFLICT DO NOTHING"
            ),
            {"owner": OWNER_SUB},
        )
        await session.execute(
            text(
                "INSERT INTO toolkit_credential_bindings (id, toolkit_id, credential_id) "
                "VALUES ('tcb_refsat', 'tk_target', 'cred_refsat') ON CONFLICT DO NOTHING"
            )
        )
        await session.commit()
    try:
        filed = filer_client.post(
            "/access-requests",
            json={
                "items": [
                    {
                        "resource_type": "toolkit",
                        "action": "bind",
                        "resource_reference": {"vendor": "webtest-refsat"},
                    }
                ]
            },
        )
        assert filed.status_code == 202, filed.text
        request_id = filed.json()["id"]

        # The owner sees tk_target (they own it): the reference resolves to it
        # and the agent is already bound → True.
        got = owner_client.get(f"/access-requests/{request_id}").json()
        assert got["items"][0]["already_satisfied"] is True

        # The filer agent (no owner:toolkits:read delegation) can't see any
        # toolkit serving the API — determinately unsatisfied under ITS scope.
        got = filer_client.get(f"/access-requests/{request_id}").json()
        assert got["items"][0]["already_satisfied"] is False
    finally:
        async with web_context.control_db.session() as session:
            await session.execute(
                text("DELETE FROM toolkit_credential_bindings WHERE id = 'tcb_refsat'")
            )
            await session.execute(text("DELETE FROM credentials WHERE id = 'cred_refsat'"))
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
        json={
            "items": [
                {
                    "item_id": item_id,
                    "decision": "approved",
                }
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    approved_item = next(i for i in body["items"] if i["id"] == item_id)
    assert approved_item["applied_effects"] is not None
    # credential:bind is a supported effect, so it records a real binding.
    assert "binding_id" in approved_item["applied_effects"]


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


@pytest.fixture()
async def seed_bind_targets(web_context: Context) -> AsyncGenerator[None, None]:
    """Seed a real toolkit + credential so a credential:bind decide actually applies rules."""
    async with web_context.control_db.session() as session:
        await session.execute(
            text("INSERT INTO toolkits (id, name) VALUES (:id, :name) ON CONFLICT DO NOTHING"),
            {"id": "tk_target", "name": "webtest-bind-toolkit"},
        )
        await session.execute(
            text(
                "INSERT INTO credentials (id, type, name, api_vendor, created_by) "
                "VALUES (:id, :type, :name, :vendor, :created_by) ON CONFLICT DO NOTHING"
            ),
            {
                "id": "cred_bind",
                "type": "api_key",
                "name": "webtest-cred",
                "vendor": "acme",
                "created_by": OWNER_SUB,
            },
        )
        await session.commit()
    yield
    async with web_context.control_db.session() as session:
        await session.execute(
            text("DELETE FROM toolkit_permission_rules WHERE credential_id = :cid"),
            {"cid": "cred_bind"},
        )
        await session.execute(
            text("DELETE FROM toolkit_credential_bindings WHERE credential_id = :cid"),
            {"cid": "cred_bind"},
        )
        await session.execute(text("DELETE FROM credentials WHERE id = :id"), {"id": "cred_bind"})
        await session.execute(text("DELETE FROM toolkits WHERE id = :id"), {"id": "tk_target"})
        await session.commit()


def test_decide_credential_bind_with_rules_applies_them(
    filer_client: TestClient, owner_client: TestClient, seed_bind_targets: None
) -> None:
    """Regression guard: a credential:bind with rules still succeeds and the rules apply."""
    filed = filer_client.post(
        "/access-requests",
        json={
            "items": [
                {
                    "resource_type": "credential",
                    "action": "bind",
                    "resource_id": "cred_bind",
                    "to_type": "toolkit",
                    "to_id": "tk_target",
                    "rules": [{"effect": "allow", "methods": ["GET"]}],
                }
            ],
        },
    )
    assert filed.status_code == 202, filed.text
    data = filed.json()
    item_id = data["items"][0]["id"]
    resp = owner_client.post(
        f"/access-requests/{data['id']}:decide",
        json={"items": [{"item_id": item_id, "decision": "approved"}]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "approved"
    approved_item = next(i for i in body["items"] if i["id"] == item_id)
    assert approved_item["applied_effects"]["rules_applied"] == 1


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
