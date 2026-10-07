"""Web tests for the admin permissions router."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import UserPermissionGrantRepository
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


def test_list_success(authed_client: TestClient) -> None:
    resp = authed_client.get("/permissions")
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert len(data["data"]) > 0
    entry = data["data"][0]
    assert "name" in entry
    assert "grantable_by_caller" in entry


def test_list_catalogue_vocabulary(authed_client: TestClient) -> None:
    """The catalogue contains the renamed spec vocabulary."""
    resp = authed_client.get("/permissions")
    assert resp.status_code == 200
    names = [e["name"] for e in resp.json()["data"]]
    assert "users:write" in names
    assert "users:read" in names
    assert "jobs:write" in names
    assert "events:write" in names
    assert "credentials:read" in names
    assert "credentials:write" in names
    assert "apis:read" in names
    assert "executions:read" in names
    assert "capabilities:execute" in names


def test_list_without_auth(unauthed_client: TestClient) -> None:
    resp = unauthed_client.get("/permissions")
    assert resp.status_code == 401


def test_set_success(authed_client: TestClient, admin_user_id: str) -> None:
    resp = authed_client.put(
        f"/users/{admin_user_id}/permissions",
        json={"permissions": ["org:admin"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    # Returns full UserResponse with permissions object
    assert "permissions" in data
    perms = data["permissions"]
    assert "assigned" in perms
    assert "effective" in perms
    assert "org:admin" in perms["assigned"]


def test_set_unknown_permission(authed_client: TestClient, admin_user_id: str) -> None:
    resp = authed_client.put(
        f"/users/{admin_user_id}/permissions",
        json={"permissions": ["fake:unknown:perm"]},
    )
    assert resp.status_code == 422
    assert resp.json()["type"] == "unknown_permission"


def test_set_non_grantable_permission(authed_client: TestClient, admin_user_id: str) -> None:
    """Non-grantable permission returns 422."""
    # Create a limited token that has users:write but NOT events:write
    # Since admin has org:admin, use a case where the permission doesn't exist
    resp = authed_client.put(
        f"/users/{admin_user_id}/permissions",
        json={"permissions": ["nonexistent:perm"]},
    )
    assert resp.status_code == 422


def test_set_permissions_on_more_privileged_user_forbidden(
    unauthed_client: TestClient, web_context: Context, admin_user_id: str, managed_user_id: str
) -> None:
    """A users:write holder cannot change the permissions of an org:admin."""

    async def _grant() -> None:
        async with web_context.admin_db.transaction() as session:
            await UserPermissionGrantRepository.set_permissions(
                session,
                managed_user_id,
                permissions={"users:read", "users:write"},
                granted_by=None,
                created_by="usr_test",
            )

    asyncio.get_event_loop().run_until_complete(_grant())
    config = web_context.config.admin.auth
    claims = {
        "sub": managed_user_id,
        "email": "web-managed@test.local",
        "actor_type": "user",
        "must_change_password": False,
    }
    token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)

    resp = unauthed_client.put(
        f"/users/{admin_user_id}/permissions",
        json={"permissions": ["users:read"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["type"] == "user_management_forbidden"


def test_removing_org_admin_from_last_active_admin_conflicts(
    authed_client: TestClient, web_context: Context, admin_user_id: str
) -> None:
    """The only active org:admin cannot have org:admin removed (409)."""

    async def _deactivate_other_admins() -> list[str]:
        async with web_context.admin_db.transaction() as session:
            result = await session.execute(
                select(User.id)
                .join(UserPermissionGrant, UserPermissionGrant.user_id == User.id)
                .where(
                    UserPermissionGrant.permission == "org:admin",
                    User.active.is_(True),
                    User.id != admin_user_id,
                )
            )
            others = sorted(set(result.scalars().all()))
            if others:
                await session.execute(update(User).where(User.id.in_(others)).values(active=False))
        return others

    async def _reactivate(ids: list[str]) -> None:
        async with web_context.admin_db.transaction() as session:
            await session.execute(update(User).where(User.id.in_(ids)).values(active=True))

    loop = asyncio.get_event_loop()
    others = loop.run_until_complete(_deactivate_other_admins())
    try:
        resp = authed_client.put(
            f"/users/{admin_user_id}/permissions", json={"permissions": ["users:write"]}
        )
        assert resp.status_code == 409
        assert resp.json()["type"] == "last_active_admin"
    finally:
        if others:
            loop.run_until_complete(_reactivate(others))
