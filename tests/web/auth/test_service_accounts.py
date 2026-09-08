"""Web tests for the auth service accounts router."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.actor_scope_grants import ActorScopeGrant
from jentic_one.admin.core.schema.service_accounts import ServiceAccount
from jentic_one.admin.repos import ActorScopeGrantRepository, ServiceAccountRepository
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.shared.context import Context
from tests.web.auth.conftest import _build_app

pytestmark = pytest.mark.integration


def test_create_service_account(owner_client: TestClient) -> None:
    resp = owner_client.post(
        "/service-accounts",
        json={"name": "my-sa", "description": "Test service account"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "my-sa"
    assert data["description"] == "Test service account"
    assert data["status"] == "active"
    assert data["approved_by"] is not None
    assert data["approved_at"] is not None


def test_create_service_account_is_immediately_active(owner_client: TestClient) -> None:
    """Actor-created service accounts are active immediately — no approval step needed."""
    resp = owner_client.post(
        "/service-accounts",
        json={"name": "immediate-sa"},
    )
    assert resp.status_code == 201
    sa_id = resp.json()["id"]

    resp = owner_client.post(f"/service-accounts/{sa_id}:approve")
    assert resp.status_code == 409
    assert resp.json()["type"] == "invalid_transition"


def test_list_service_accounts_admin(
    admin_client: TestClient, test_service_account_id: str
) -> None:
    resp = admin_client.get("/service-accounts")
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert "has_more" in data
    assert "next_cursor" in data
    ids = [sa["id"] for sa in data["data"]]
    assert test_service_account_id in ids


def test_list_service_accounts_unauthenticated(unauthed_client: TestClient) -> None:
    resp = unauthed_client.get("/service-accounts")
    assert resp.status_code == 401


def test_get_service_account(admin_client: TestClient, test_service_account_id: str) -> None:
    resp = admin_client.get(f"/service-accounts/{test_service_account_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == test_service_account_id
    assert data["status"] == "pending"


def test_get_service_account_not_found(admin_client: TestClient) -> None:
    resp = admin_client.get("/service-accounts/nonexistent")
    assert resp.status_code == 404


def test_approve_service_account(admin_client: TestClient, test_service_account_id: str) -> None:
    resp = admin_client.post(f"/service-accounts/{test_service_account_id}:approve")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"
    assert data["approved_by"] is not None


def test_approve_invalid_state(admin_client: TestClient, test_service_account_id: str) -> None:
    admin_client.post(f"/service-accounts/{test_service_account_id}:approve")
    resp = admin_client.post(f"/service-accounts/{test_service_account_id}:approve")
    assert resp.status_code == 409
    assert resp.json()["type"] == "invalid_transition"


@pytest.fixture()
async def deny_target_sa_id(web_context: Context, owner_user_id: str) -> AsyncGenerator[str, None]:
    async with web_context.admin_db.transaction() as session:
        sa = await ServiceAccountRepository.create(
            session,
            name="deny-sa",
            owner_id=owner_user_id,
            registered_by=owner_user_id,
            created_by="usr_test",
        )
    yield sa.id

    async with web_context.admin_db.session() as session:
        await session.execute(delete(ServiceAccount).where(ServiceAccount.id == sa.id))
        await session.commit()


def test_deny_service_account(admin_client: TestClient, deny_target_sa_id: str) -> None:
    resp = admin_client.post(
        f"/service-accounts/{deny_target_sa_id}:deny", json={"reason": "Rejected"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"
    assert data["denial_reason"] == "Rejected"


def test_disable_service_account(admin_client: TestClient, test_service_account_id: str) -> None:
    admin_client.post(f"/service-accounts/{test_service_account_id}:approve")
    resp = admin_client.post(f"/service-accounts/{test_service_account_id}:disable")
    assert resp.status_code == 204


def test_enable_service_account(admin_client: TestClient, test_service_account_id: str) -> None:
    admin_client.post(f"/service-accounts/{test_service_account_id}:approve")
    admin_client.post(f"/service-accounts/{test_service_account_id}:disable")
    resp = admin_client.post(f"/service-accounts/{test_service_account_id}:enable")
    assert resp.status_code == 204


@pytest.fixture()
async def archive_target_sa_id(
    web_context: Context, owner_user_id: str
) -> AsyncGenerator[str, None]:
    async with web_context.admin_db.transaction() as session:
        sa = await ServiceAccountRepository.create(
            session,
            name="archive-sa",
            owner_id=owner_user_id,
            registered_by=owner_user_id,
            created_by="usr_test",
        )
        await ActorScopeGrantRepository.grant(
            session,
            actor_id=sa.id,
            actor_type="service_account",
            scope="test:scope",
            created_by="usr_test",
        )
    yield sa.id

    async with web_context.admin_db.session() as session:
        await session.execute(delete(ActorScopeGrant).where(ActorScopeGrant.actor_id == sa.id))
        await session.execute(delete(ServiceAccount).where(ServiceAccount.id == sa.id))
        await session.commit()


async def test_archive_service_account(
    admin_client: TestClient, web_context: Context, archive_target_sa_id: str
) -> None:
    resp = admin_client.delete(f"/service-accounts/{archive_target_sa_id}")
    assert resp.status_code == 204

    async with web_context.admin_db.session() as session:
        sa = await ServiceAccountRepository.get_by_id(session, archive_target_sa_id)
        assert sa is not None
        assert sa.status == "archived"
        grants = await ActorScopeGrantRepository.list_for_actor(session, archive_target_sa_id)
        assert grants == []


def test_archive_already_archived(admin_client: TestClient, test_service_account_id: str) -> None:
    admin_client.delete(f"/service-accounts/{test_service_account_id}")
    resp = admin_client.delete(f"/service-accounts/{test_service_account_id}")
    assert resp.status_code == 409


def test_verbs_on_archived(admin_client: TestClient, test_service_account_id: str) -> None:
    admin_client.delete(f"/service-accounts/{test_service_account_id}")
    for verb in ("approve", "deny", "disable", "enable"):
        if verb == "deny":
            resp = admin_client.post(
                f"/service-accounts/{test_service_account_id}:{verb}",
                json={"reason": "test"},
            )
        else:
            resp = admin_client.post(f"/service-accounts/{test_service_account_id}:{verb}")
        assert resp.status_code == 409, f"Expected 409 for {verb} on archived SA"


def test_password_rotation_required(web_context: Context) -> None:
    """Tokens with must_change_password=True get 403 on permission-gated endpoints."""
    config = web_context.config.admin.auth
    claims = {
        "sub": "user-needs-rotation",
        "email": "rotation@test.local",
        "actor_type": "user",
        "permissions": ["org:admin"],
        "must_change_password": True,
    }
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)

    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.get("/service-accounts")
        assert resp.status_code == 403
        assert resp.json()["type"] == "password_rotation_required"
