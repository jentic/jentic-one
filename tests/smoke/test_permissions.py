"""Smoke tests for the permissions catalogue and effective permission assignment."""

from __future__ import annotations

import uuid

import pytest

from tests.smoke.conftest import authed_request


@pytest.mark.smoke
def test_list_permission_catalogue(base_url: str, user_token: str) -> None:
    """Authenticated user can list the permission catalogue."""
    body, status = authed_request(f"{base_url}/permissions", token=user_token)
    assert status == 200
    assert isinstance(body, dict)
    permissions = body["data"]
    names = [p["name"] for p in permissions]
    assert "apis:read" in names
    assert "capabilities:execute" in names


@pytest.mark.smoke
def test_set_permissions_updates_effective(base_url: str, admin_token: str) -> None:
    """Granting a permission updates the user's effective permission set."""
    email = f"smoke-perm-{uuid.uuid4().hex[:8]}@test.local"
    password = "SmokePerms123!"

    create_body, _ = authed_request(
        f"{base_url}/users",
        method="POST",
        token=admin_token,
        body={"email": email, "first_name": "Perm", "last_name": "Test"},
    )
    assert isinstance(create_body, dict)
    user_id = create_body["user"]["id"]
    invite_token = create_body["invite_token"]

    try:
        authed_request(
            f"{base_url}/users:redeem-invite",
            method="POST",
            body={"invite_token": invite_token, "password": password},
        )

        _, perm_status = authed_request(
            f"{base_url}/users/{user_id}/permissions",
            method="PUT",
            token=admin_token,
            body={"permissions": ["credentials:write"]},
        )
        assert perm_status == 200

        user_body, user_status = authed_request(f"{base_url}/users/{user_id}", token=admin_token)
        assert user_status == 200
        assert isinstance(user_body, dict)
        effective_names = [p["name"] for p in user_body["permissions"]["effective"]]
        assert "credentials:write" in effective_names
    finally:
        authed_request(f"{base_url}/users/{user_id}", method="DELETE", token=admin_token)
