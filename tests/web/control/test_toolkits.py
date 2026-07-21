"""Web tests for the toolkits HTTP surface.

Verifies that all toolkit CRUD endpoints serialize responses correctly without
DetachedInstanceError — the fix from issue #362.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


# --- Helpers ---


def _create_toolkit(client: TestClient, name: str = "test-toolkit") -> dict[str, Any]:
    resp = client.post(
        "/toolkits",
        json={"name": name, "description": "A test toolkit"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


# --- Create ---


def test_create_toolkit(tk_owner_client: TestClient) -> None:
    data = _create_toolkit(tk_owner_client, name="create-test")
    assert data["toolkit"]["toolkit_id"].startswith("tk_")
    assert data["toolkit"]["name"] == "create-test"
    assert data["toolkit"]["active"] is True
    assert data["toolkit"]["key_count"] == 1
    assert data["toolkit"]["credential_count"] == 0
    assert data["api_key"].startswith("jntc_live_")


def test_create_returns_key_and_credential_counts(tk_owner_client: TestClient) -> None:
    data = _create_toolkit(tk_owner_client, name="counts-test")
    toolkit = data["toolkit"]
    assert toolkit["key_count"] == 1
    assert toolkit["credential_count"] == 0


# --- Get ---


def test_get_toolkit(tk_owner_client: TestClient) -> None:
    created = _create_toolkit(tk_owner_client, name="get-test")
    toolkit_id = created["toolkit"]["toolkit_id"]

    resp = tk_owner_client.get(f"/toolkits/{toolkit_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["toolkit_id"] == toolkit_id
    assert data["name"] == "get-test"
    assert data["key_count"] == 1


def test_get_toolkit_not_found(tk_owner_client: TestClient) -> None:
    resp = tk_owner_client.get("/toolkits/tk_nonexistent")
    assert resp.status_code == 404


# --- List ---


def test_list_toolkits(tk_owner_client: TestClient) -> None:
    _create_toolkit(tk_owner_client, name="list-test-a")
    _create_toolkit(tk_owner_client, name="list-test-b")

    resp = tk_owner_client.get("/toolkits")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_more"] is False or isinstance(data["has_more"], bool)
    assert len(data["data"]) >= 2
    for item in data["data"]:
        assert "toolkit_id" in item
        assert "key_count" in item
        assert "credential_count" in item


# --- Update ---


def test_update_toolkit(tk_owner_client: TestClient) -> None:
    created = _create_toolkit(tk_owner_client, name="update-test")
    toolkit_id = created["toolkit"]["toolkit_id"]

    resp = tk_owner_client.patch(
        f"/toolkits/{toolkit_id}",
        json={"name": "update-test-renamed", "active": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "update-test-renamed"
    assert data["active"] is False
    assert data["key_count"] == 1


def test_update_toolkit_not_found(tk_owner_client: TestClient) -> None:
    resp = tk_owner_client.patch(
        "/toolkits/tk_nonexistent",
        json={"name": "nope"},
    )
    assert resp.status_code == 404


# --- Delete ---


def test_delete_toolkit(tk_admin_client: TestClient) -> None:
    created = _create_toolkit(tk_admin_client, name="delete-test")
    toolkit_id = created["toolkit"]["toolkit_id"]

    resp = tk_admin_client.delete(f"/toolkits/{toolkit_id}")
    assert resp.status_code == 204

    resp = tk_admin_client.get(f"/toolkits/{toolkit_id}")
    assert resp.status_code == 404


# --- Bound-but-orphaned agent visibility (issues #665 / #682) ---


def test_orphaned_agent_reads_bound_toolkit(bound_orphan_client: TestClient) -> None:
    """A bound agent that owns nothing gets 200 (not 404) on its bound toolkit.

    Reproduces #665/#682: previously owner-only scoping returned 404 for an
    orphaned agent even though it is actively bound to the toolkit.
    """
    resp = bound_orphan_client.get("/toolkits/tk_target")
    assert resp.status_code == 200, resp.text
    assert resp.json()["toolkit_id"] == "tk_target"


def test_orphaned_agent_lists_bound_toolkit(bound_orphan_client: TestClient) -> None:
    """The bound toolkit appears in the orphaned agent's list, not an empty page."""
    resp = bound_orphan_client.get("/toolkits")
    assert resp.status_code == 200, resp.text
    ids = [t["toolkit_id"] for t in resp.json()["data"]]
    assert "tk_target" in ids


def test_orphaned_agent_denied_unbound_toolkit(bound_orphan_client: TestClient) -> None:
    """Visibility is scoped to bindings — an unbound/unknown toolkit is still 404."""
    resp = bound_orphan_client.get("/toolkits/tk_not_bound")
    assert resp.status_code == 404


# --- Keys ---


def test_create_and_list_keys(tk_owner_client: TestClient) -> None:
    created = _create_toolkit(tk_owner_client, name="keys-test")
    toolkit_id = created["toolkit"]["toolkit_id"]

    resp = tk_owner_client.post(
        f"/toolkits/{toolkit_id}/keys",
        json={"label": "secondary-key"},
    )
    assert resp.status_code == 201
    key_data = resp.json()
    assert key_data["key"]["toolkit_id"] == toolkit_id
    assert key_data["key"]["label"] == "secondary-key"
    assert key_data["api_key"].startswith("jntc_live_")

    resp = tk_owner_client.get(f"/toolkits/{toolkit_id}/keys")
    assert resp.status_code == 200
    keys = resp.json()
    assert len(keys["data"]) == 2


def test_update_key(tk_owner_client: TestClient) -> None:
    created = _create_toolkit(tk_owner_client, name="key-update-test")
    toolkit_id = created["toolkit"]["toolkit_id"]

    resp = tk_owner_client.post(
        f"/toolkits/{toolkit_id}/keys",
        json={"label": "to-revoke"},
    )
    key_id = resp.json()["key"]["key_id"]

    resp = tk_owner_client.patch(
        f"/toolkits/{toolkit_id}/keys/{key_id}",
        json={"revoked": True},
    )
    assert resp.status_code == 200
    assert resp.json()["revoked"] is True


def test_delete_key(tk_owner_client: TestClient) -> None:
    created = _create_toolkit(tk_owner_client, name="key-delete-test")
    toolkit_id = created["toolkit"]["toolkit_id"]

    resp = tk_owner_client.post(
        f"/toolkits/{toolkit_id}/keys",
        json={"label": "ephemeral"},
    )
    key_id = resp.json()["key"]["key_id"]

    resp = tk_owner_client.delete(f"/toolkits/{toolkit_id}/keys/{key_id}")
    assert resp.status_code == 204


# --- Bound agents (reverse lookup) ---


def test_list_toolkit_agents_empty(tk_owner_client: TestClient) -> None:
    """A freshly created toolkit has no bound agents yet."""
    created = _create_toolkit(tk_owner_client, name="agents-empty-test")
    toolkit_id = created["toolkit"]["toolkit_id"]

    resp = tk_owner_client.get(f"/toolkits/{toolkit_id}/agents")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["data"] == []
    assert data["has_more"] is False
    assert data["next_cursor"] is None


def test_list_toolkit_agents_not_found(tk_owner_client: TestClient) -> None:
    resp = tk_owner_client.get("/toolkits/tk_nonexistent/agents")
    assert resp.status_code == 404


def test_list_toolkit_agents_respects_limit_bounds(tk_owner_client: TestClient) -> None:
    """`limit` is validated (1..200) like the other paginated toolkit reads."""
    created = _create_toolkit(tk_owner_client, name="agents-limit-test")
    toolkit_id = created["toolkit"]["toolkit_id"]

    assert tk_owner_client.get(f"/toolkits/{toolkit_id}/agents?limit=0").status_code == 422
    assert tk_owner_client.get(f"/toolkits/{toolkit_id}/agents?limit=201").status_code == 422
