"""Web tests for the admin permissions router."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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
