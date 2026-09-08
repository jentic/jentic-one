"""Smoke test for toolkit lifecycle: create → delete → verify gone."""

from __future__ import annotations

import uuid

import pytest

from tests.smoke.conftest import SmokeAgent, authed_request


@pytest.mark.smoke
def test_toolkit_delete_lifecycle(base_url: str, test_agent: SmokeAgent) -> None:
    """Create a toolkit, delete it, confirm GET returns 404."""
    name = f"smoke-del-{uuid.uuid4().hex[:12]}"
    create_body, create_status = authed_request(
        f"{base_url}/toolkits",
        method="POST",
        token=test_agent.owner_token,
        body={"name": name},
    )
    assert create_status == 201
    assert isinstance(create_body, dict)
    toolkit_id: str = create_body["toolkit"]["toolkit_id"]

    _, delete_status = authed_request(
        f"{base_url}/toolkits/{toolkit_id}",
        method="DELETE",
        token=test_agent.owner_token,
    )
    assert delete_status == 204

    _, get_status = authed_request(
        f"{base_url}/toolkits/{toolkit_id}",
        token=test_agent.owner_token,
    )
    assert get_status == 404


@pytest.mark.smoke
def test_toolkit_delete_nonexistent_returns_404(base_url: str, test_agent: SmokeAgent) -> None:
    """DELETE on a non-existent toolkit_id returns 404."""
    _, status = authed_request(
        f"{base_url}/toolkits/tk_nonexistent_000",
        method="DELETE",
        token=test_agent.owner_token,
    )
    assert status == 404


@pytest.mark.smoke
def test_toolkit_create_and_read_relations(base_url: str, test_agent: SmokeAgent) -> None:
    """POST/GET/PATCH /toolkits returns populated key_count and credential_count."""
    name = f"smoke-rel-{uuid.uuid4().hex[:12]}"
    create_body, create_status = authed_request(
        f"{base_url}/toolkits",
        method="POST",
        token=test_agent.owner_token,
        body={"name": name},
    )
    assert create_status == 201
    assert isinstance(create_body, dict)
    toolkit = create_body["toolkit"]
    toolkit_id: str = toolkit["toolkit_id"]
    assert toolkit["key_count"] >= 1
    assert toolkit["credential_count"] == 0

    get_body, get_status = authed_request(
        f"{base_url}/toolkits/{toolkit_id}",
        token=test_agent.owner_token,
    )
    assert get_status == 200
    assert isinstance(get_body, dict)
    assert get_body["key_count"] >= 1

    patch_body, patch_status = authed_request(
        f"{base_url}/toolkits/{toolkit_id}",
        method="PATCH",
        token=test_agent.owner_token,
        body={"active": False},
    )
    assert patch_status == 200
    assert isinstance(patch_body, dict)
    assert patch_body["active"] is False
    assert patch_body["key_count"] >= 1

    authed_request(
        f"{base_url}/toolkits/{toolkit_id}",
        method="DELETE",
        token=test_agent.owner_token,
    )


@pytest.mark.smoke
def test_toolkit_bind_credential_relations(base_url: str, test_agent: SmokeAgent) -> None:
    """POST/GET /toolkits/{id}/credentials returns credential metadata."""
    toolkit_name = f"smoke-bind-rel-{uuid.uuid4().hex[:12]}"
    create_body, _ = authed_request(
        f"{base_url}/toolkits",
        method="POST",
        token=test_agent.owner_token,
        body={"name": toolkit_name},
    )
    assert isinstance(create_body, dict)
    toolkit_id: str = create_body["toolkit"]["toolkit_id"]

    cred_body, cred_status = authed_request(
        f"{base_url}/credentials",
        method="POST",
        token=test_agent.owner_token,
        body={
            "type": "bearer_token",
            "name": f"smoke-cred-{uuid.uuid4().hex[:8]}",
            "api": {"vendor": f"smoke-{uuid.uuid4().hex[:8]}", "name": "test", "version": "1.0"},
            "provider": "static",
            "token": "sk-test-value",
        },
    )
    if cred_status != 201:
        pytest.skip("credential creation not available")
    assert isinstance(cred_body, dict)
    credential_id: str = cred_body["credential"]["credential_id"]

    bind_body, bind_status = authed_request(
        f"{base_url}/toolkits/{toolkit_id}/credentials",
        method="POST",
        token=test_agent.owner_token,
        body={"credential_id": credential_id},
    )
    assert bind_status == 201
    assert isinstance(bind_body, dict)
    assert bind_body["credential_id"] == credential_id
    assert bind_body.get("label") is not None or bind_body.get("api_vendor") is not None

    list_body, list_status = authed_request(
        f"{base_url}/toolkits/{toolkit_id}/credentials",
        token=test_agent.owner_token,
    )
    assert list_status == 200
    assert isinstance(list_body, dict)
    assert len(list_body["data"]) >= 1
    assert list_body["data"][0]["credential_id"] == credential_id

    authed_request(
        f"{base_url}/toolkits/{toolkit_id}",
        method="DELETE",
        token=test_agent.owner_token,
    )


@pytest.mark.smoke
def test_toolkit_delete_with_credential_binding(base_url: str, test_agent: SmokeAgent) -> None:
    """Deleting a toolkit with bound credentials succeeds; credential still exists."""
    toolkit_name = f"smoke-del-cred-{uuid.uuid4().hex[:12]}"
    create_body, _ = authed_request(
        f"{base_url}/toolkits",
        method="POST",
        token=test_agent.owner_token,
        body={"name": toolkit_name},
    )
    assert isinstance(create_body, dict)
    toolkit_id: str = create_body["toolkit"]["toolkit_id"]

    cred_body, cred_status = authed_request(
        f"{base_url}/credentials",
        method="POST",
        token=test_agent.owner_token,
        body={
            "type": "bearer_token",
            "name": f"smoke-cred-{uuid.uuid4().hex[:8]}",
            "api": {"vendor": f"smoke-{uuid.uuid4().hex[:8]}", "name": "test", "version": "1.0"},
            "provider": "static",
            "token": "sk-test-value",
        },
    )
    if cred_status != 201:
        pytest.skip("credential creation not available")
    assert isinstance(cred_body, dict)
    credential_id: str = cred_body["credential"]["credential_id"]

    authed_request(
        f"{base_url}/toolkits/{toolkit_id}/credentials",
        method="POST",
        token=test_agent.owner_token,
        body={"credential_id": credential_id},
    )

    _, delete_status = authed_request(
        f"{base_url}/toolkits/{toolkit_id}",
        method="DELETE",
        token=test_agent.owner_token,
    )
    assert delete_status == 204

    _, cred_get_status = authed_request(
        f"{base_url}/credentials/{credential_id}",
        token=test_agent.owner_token,
    )
    assert cred_get_status == 200
