"""Web tests for the admin users router."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.invite_tokens import InviteToken
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import UserPermissionGrantRepository
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.shared.context import Context

from .conftest import ADMIN_EMAIL

pytestmark = pytest.mark.integration


def test_list_success(authed_client: TestClient) -> None:
    resp = authed_client.get("/users")
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert "has_more" in data


def test_list_without_auth(unauthed_client: TestClient) -> None:
    resp = unauthed_client.get("/users")
    assert resp.status_code == 401


def test_create_success(authed_client: TestClient, web_context: Context) -> None:
    resp = authed_client.post(
        "/users",
        json={
            "email": "web-created@example.com",
            "first_name": "New",
            "last_name": "User",
            "permissions": [],
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "user" in data
    assert "invite_token" in data
    assert "invite_expires_at" in data
    user = data["user"]
    assert user["name"] == "New User"
    assert "external_subject_id" in user
    assert "permissions" in user
    perms = user["permissions"]
    assert "assigned" in perms
    assert "effective" in perms
    user_id = user["id"]

    # Cleanup
    async def _cleanup() -> None:
        async with web_context.admin_db.session() as session:
            await session.execute(delete(InviteToken).where(InviteToken.user_id == user_id))
            await session.execute(
                delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
            )
            await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()

    asyncio.get_event_loop().run_until_complete(_cleanup())


def test_create_duplicate_email(authed_client: TestClient, admin_user_id: str) -> None:
    resp = authed_client.post(
        "/users",
        json={
            "email": ADMIN_EMAIL,
            "first_name": "Dup",
            "last_name": "User",
            "permissions": [],
        },
    )
    assert resp.status_code == 409
    assert resp.json()["type"] == "email_in_use"


def test_get_success(authed_client: TestClient, admin_user_id: str) -> None:
    resp = authed_client.get(f"/users/{admin_user_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == admin_user_id
    assert "name" in data
    assert "external_subject_id" in data
    perms = data["permissions"]
    assert "assigned" in perms
    assert "effective" in perms
    assert isinstance(perms["effective"], list)


def test_get_not_found(authed_client: TestClient) -> None:
    resp = authed_client.get("/users/nonexistent-user-id")
    assert resp.status_code == 404
    assert resp.json()["type"] == "user_not_found"


def test_update_success(authed_client: TestClient, admin_user_id: str) -> None:
    resp = authed_client.patch(f"/users/{admin_user_id}", json={"first_name": "Updated"})
    assert resp.status_code == 200
    assert resp.json()["first_name"] == "Updated"


def test_disable_and_enable(authed_client: TestClient, admin_user_id: str) -> None:
    resp = authed_client.post(f"/users/{admin_user_id}:disable")
    assert resp.status_code == 204

    resp = authed_client.post(f"/users/{admin_user_id}:enable")
    assert resp.status_code == 204


def test_invite_state_filter(authed_client: TestClient, admin_user_id: str) -> None:
    resp = authed_client.get("/users?invite_state=redeemed")
    assert resp.status_code == 200
    data = resp.json()
    for user in data["data"]:
        assert user["invite_state"] == "redeemed"


@pytest.fixture
async def reader_user_permissions(web_context: Context) -> None:
    # Create the user via the API first so it exists in the DB
    # Then we can grant permissions safely

    # Using the repo directly to avoid needing HTTP clients in setup
    async with web_context.admin_db.transaction() as session:
        system = User(
            id="usr_admin",
            first_name="Sys",
            last_name="tem",
            email="system@jentic.com",
            auth_provider="internal",
            active=True,
        )
        reader = User(
            id="reader-user",
            first_name="Reader",
            last_name="Test",
            email="reader@test.com",
            auth_provider="internal",
            active=True,
        )
        # UPSERT / Avoid integrity error

        existing_system = await session.scalar(select(User).where(User.id == "usr_admin"))
        if not existing_system:
            session.add(system)
        existing_reader = await session.scalar(select(User).where(User.id == "reader-user"))
        if not existing_reader:
            session.add(reader)
        await session.flush()

    async with web_context.admin_db.transaction() as grant_session:
        await UserPermissionGrantRepository.set_permissions(
            grant_session,
            "usr_admin",
            permissions={"users:read", "org:admin", "users:write"},
            granted_by=None,
            created_by="usr_admin",
        )
        await UserPermissionGrantRepository.set_permissions(
            grant_session,
            "reader-user",
            permissions={"users:read"},
            granted_by="usr_admin",
            created_by="usr_admin",
        )


def test_users_read_can_list(
    unauthed_client: TestClient, web_context: Context, reader_user_permissions: None
) -> None:
    """A caller with only users:read can GET /users."""
    config = web_context.config.admin.auth
    claims = {
        "sub": "reader-user",
        "email": "reader@test.com",
        "actor_type": "user",
        "must_change_password": False,
    }
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)
    resp = unauthed_client.get("/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_token_without_actor_type_is_401(
    unauthed_client: TestClient, web_context: Context, reader_user_permissions: None
) -> None:
    """Fail closed (#862): a validly-signed JWT with no actor_type claim gets 401."""
    config = web_context.config.admin.auth
    claims = {
        "sub": "reader-user",
        "email": "reader@test.com",
        "must_change_password": False,
    }
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)
    resp = unauthed_client.get("/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["type"] == "unauthorized"


def test_token_with_unknown_actor_type_is_401_not_500(
    unauthed_client: TestClient, web_context: Context, reader_user_permissions: None
) -> None:
    """An unrecognised actor_type claim is a clean 401, never a ValueError→500."""
    config = web_context.config.admin.auth
    claims = {
        "sub": "reader-user",
        "email": "reader@test.com",
        "actor_type": "definitely-not-a-real-actor",
        "must_change_password": False,
    }
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)
    resp = unauthed_client.get("/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["type"] == "unauthorized"


def test_users_read_cannot_create(
    unauthed_client: TestClient, web_context: Context, reader_user_permissions: None
) -> None:
    """A caller with only users:read is rejected on POST /users."""
    config = web_context.config.admin.auth
    claims = {
        "sub": "reader-user",
        "email": "reader@test.com",
        "actor_type": "user",
        "must_change_password": False,
    }
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)
    resp = unauthed_client.post(
        "/users",
        json={
            "email": "new@example.com",
            "first_name": "X",
            "last_name": "Y",
            "permissions": [],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_password_expired_blocks_access(unauthed_client: TestClient, web_context: Context) -> None:
    """A token with must_change_password=True gets a 403 on protected endpoints."""
    config = web_context.config.admin.auth
    claims = {
        "sub": "expired-pw-user",
        "email": "expired@test.com",
        "actor_type": "user",
        "permissions": ["org:admin"],
        "must_change_password": True,
    }
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)
    resp = unauthed_client.get("/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["type"] == "password_rotation_required"
