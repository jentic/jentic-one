"""Web tests for the admin audit router."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jentic_one.admin.repos import AuditRepository
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditAction, AuditTargetType

pytestmark = pytest.mark.integration


def test_list_without_auth(unauthed_client: TestClient) -> None:
    resp = unauthed_client.get("/audit")
    assert resp.status_code == 401


def test_list_without_admin(unauthed_client: TestClient, web_context: Context) -> None:
    config = web_context.config.admin.auth
    claims = {
        "sub": "nonadmin-user",
        "email": "nonadmin@test.local",
        "actor_type": "user",
        "permissions": ["users:read"],
        "must_change_password": False,
    }
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)
    resp = unauthed_client.get("/audit", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


@pytest.fixture()
async def _seed_audit_entries(web_context: Context) -> None:
    """Seed three audit entries for testing."""
    async with web_context.admin_db.transaction() as session:
        await AuditRepository.record(
            session,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.USER,
            target_id="user-1",
            actor_type="user",
            actor_id="actor-a",
        )
        await AuditRepository.record(
            session,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.USER,
            target_id="user-1",
            actor_type="user",
            actor_id="actor-b",
        )
        await AuditRepository.record(
            session,
            action=AuditAction.DELETE,
            target_type=AuditTargetType.JOB,
            target_id="job-99",
            actor_type="agent",
            actor_id="actor-a",
        )


@pytest.mark.usefixtures("_seed_audit_entries")
def test_list_success(authed_client: TestClient) -> None:
    resp = authed_client.get("/audit")
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert "has_more" in data
    assert len(data["data"]) >= 3
    timestamps = [e["occurred_at"] for e in data["data"]]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.usefixtures("_seed_audit_entries")
def test_filter_by_target(authed_client: TestClient) -> None:
    resp = authed_client.get("/audit?target_type=user&target_id=user-1")
    assert resp.status_code == 200
    data = resp.json()
    for entry in data["data"]:
        assert entry["target_type"] == "user"
        assert entry["target_id"] == "user-1"


def test_filter_target_type_without_target_id(authed_client: TestClient) -> None:
    resp = authed_client.get("/audit?target_type=user")
    assert resp.status_code == 400
    assert resp.json()["type"] == "invalid_input"


def test_bad_target_type_enum(authed_client: TestClient) -> None:
    resp = authed_client.get("/audit?target_type=bogus&target_id=x")
    assert resp.status_code == 422


@pytest.mark.usefixtures("_seed_audit_entries")
def test_get_by_id_success(authed_client: TestClient) -> None:
    list_resp = authed_client.get("/audit")
    entries = list_resp.json()["data"]
    assert len(entries) > 0
    entry_id = entries[0]["id"]

    resp = authed_client.get(f"/audit/{entry_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == entry_id


def test_get_by_id_not_found(authed_client: TestClient) -> None:
    resp = authed_client.get("/audit/nonexistent-id")
    assert resp.status_code == 404
    assert resp.json()["type"] == "audit_entry_not_found"
