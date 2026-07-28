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
