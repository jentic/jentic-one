"""Smoke tests for toolkit creation, credential management, and toolkit-credential binding."""

from __future__ import annotations

import uuid

import pytest

from tests.smoke.conftest import SmokeAgent, authed_request, unique_vendor


@pytest.mark.smoke
def test_create_toolkit(base_url: str, test_agent: SmokeAgent) -> None:
    """POST /toolkits returns 201 with a toolkit_id."""
    name = f"smoke-tk-{uuid.uuid4().hex[:12]}"
    body, status = authed_request(
        f"{base_url}/toolkits",
        method="POST",
        token=test_agent.owner_token,
        body={"name": name},
    )
    assert status == 201
    assert isinstance(body, dict)
    assert "toolkit" in body
    assert "toolkit_id" in body["toolkit"]


@pytest.mark.smoke
def test_bind_toolkit_to_agent(base_url: str, test_agent: SmokeAgent) -> None:
    """POST /agents/{id}/toolkits returns 201 with binding details."""
    toolkit_name = f"smoke-bind-{uuid.uuid4().hex[:12]}"
    create_body, _ = authed_request(
        f"{base_url}/toolkits",
        method="POST",
        token=test_agent.owner_token,
        body={"name": toolkit_name},
    )
    assert isinstance(create_body, dict)
    toolkit_id = create_body["toolkit"]["toolkit_id"]

    bind_body, bind_status = authed_request(
        f"{base_url}/agents/{test_agent.agent_id}/toolkits",
        method="POST",
        token=test_agent.owner_token,
        body={"toolkit_id": toolkit_id},
    )
    assert bind_status == 201
    assert isinstance(bind_body, dict)
    assert bind_body["toolkit_id"] == toolkit_id
    assert bind_body["agent_id"] == test_agent.agent_id


@pytest.mark.smoke
def test_list_agent_toolkits(base_url: str, agent_with_toolkit: tuple[SmokeAgent, str]) -> None:
    """GET /agents/{id}/toolkits lists the bound toolkit."""
    agent, toolkit_id = agent_with_toolkit
    body, status = authed_request(
        f"{base_url}/agents/{agent.agent_id}/toolkits",
        token=agent.owner_token,
    )
    assert status == 200
    assert isinstance(body, dict)
    toolkit_ids = [t["toolkit_id"] for t in body["data"]]
    assert toolkit_id in toolkit_ids


@pytest.mark.smoke
def test_create_credential(base_url: str, test_agent: SmokeAgent) -> None:
    """POST /credentials with bearer_token type returns 201 with secret."""
    vendor = unique_vendor("cred")
    body, status = authed_request(
        f"{base_url}/credentials",
        method="POST",
        token=test_agent.owner_token,
        body={
            "type": "bearer_token",
            "name": f"smoke-cred-{uuid.uuid4().hex[:8]}",
            "api": {"vendor": vendor, "name": "petstore", "version": "3.0.0"},
            "provider": "static",
            "token": "sk-test-secret-value",
        },
    )
    assert status == 201
    assert isinstance(body, dict)
    assert "credential" in body
    assert "credential_id" in body["credential"]
    assert "secret" in body


@pytest.mark.smoke
def test_list_credentials_redacted(base_url: str, test_agent: SmokeAgent) -> None:
    """GET /credentials returns credentials without exposing secrets."""
    vendor = unique_vendor("cred-list")
    authed_request(
        f"{base_url}/credentials",
        method="POST",
        token=test_agent.owner_token,
        body={
            "type": "bearer_token",
            "name": f"smoke-cred-{uuid.uuid4().hex[:8]}",
            "api": {"vendor": vendor, "name": "petstore", "version": "3.0.0"},
            "provider": "static",
            "token": "sk-hidden-value",
        },
    )

    body, status = authed_request(
        f"{base_url}/credentials",
        token=test_agent.owner_token,
    )
    assert status == 200
    assert isinstance(body, dict)
    assert "data" in body
    assert len(body["data"]) >= 1
    for cred in body["data"]:
        assert "sk-hidden-value" not in str(cred)


@pytest.mark.smoke
def test_bind_credential_to_toolkit(
    base_url: str, agent_with_toolkit: tuple[SmokeAgent, str]
) -> None:
    """POST /toolkits/{id}/credentials binds a credential."""
    agent, toolkit_id = agent_with_toolkit
    vendor = unique_vendor("bind-cred")

    cred_body, cred_status = authed_request(
        f"{base_url}/credentials",
        method="POST",
        token=agent.owner_token,
        body={
            "type": "bearer_token",
            "name": f"smoke-cred-{uuid.uuid4().hex[:8]}",
            "api": {"vendor": vendor, "name": "petstore", "version": "3.0.0"},
            "provider": "static",
            "token": "sk-binding-test",
        },
    )
    assert cred_status == 201
    assert isinstance(cred_body, dict)
    credential_id = cred_body["credential"]["credential_id"]

    bind_body, bind_status = authed_request(
        f"{base_url}/toolkits/{toolkit_id}/credentials",
        method="POST",
        token=agent.owner_token,
        body={"credential_id": credential_id},
    )
    assert bind_status == 201
    assert isinstance(bind_body, dict)


@pytest.mark.smoke
def test_list_toolkit_credentials(
    base_url: str, agent_with_toolkit: tuple[SmokeAgent, str]
) -> None:
    """GET /toolkits/{id}/credentials lists bound credentials."""
    agent, toolkit_id = agent_with_toolkit
    vendor = unique_vendor("list-tk-cred")

    cred_body, _ = authed_request(
        f"{base_url}/credentials",
        method="POST",
        token=agent.owner_token,
        body={
            "type": "bearer_token",
            "name": f"smoke-cred-{uuid.uuid4().hex[:8]}",
            "api": {"vendor": vendor, "name": "petstore", "version": "3.0.0"},
            "provider": "static",
            "token": "sk-list-test",
        },
    )
    assert isinstance(cred_body, dict)
    credential_id = cred_body["credential"]["credential_id"]

    authed_request(
        f"{base_url}/toolkits/{toolkit_id}/credentials",
        method="POST",
        token=agent.owner_token,
        body={"credential_id": credential_id},
    )

    body, status = authed_request(
        f"{base_url}/toolkits/{toolkit_id}/credentials",
        token=agent.owner_token,
    )
    assert status == 200
    assert isinstance(body, dict)
    assert "data" in body
    cred_ids = [b["credential_id"] for b in body["data"]]
    assert credential_id in cred_ids


@pytest.mark.smoke
def test_create_api_key_for_toolkit_is_retired(
    base_url: str, agent_with_toolkit: tuple[SmokeAgent, str]
) -> None:
    """POST /toolkits/{id}/keys returns 410 — toolkit keys are retired."""
    agent, toolkit_id = agent_with_toolkit
    body, status = authed_request(
        f"{base_url}/toolkits/{toolkit_id}/keys",
        method="POST",
        token=agent.owner_token,
        body={"label": "smoke-key"},
    )
    assert status == 410
    assert isinstance(body, dict)
    assert body["type"] == "toolkit_keys_retired"
