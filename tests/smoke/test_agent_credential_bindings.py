"""Smoke tests for credential management and direct agent↔credential bindings.

The theme-5 Phase 5b replacement for the deleted toolkit-axis suite: the
management surface under test is ``POST /credentials`` plus the direct-binding
routes (``/agents/{id}/credentials``) and the per-binding permission rules
(``/credentials/{cid}/agents/{aid}/permissions``).
"""

from __future__ import annotations

import uuid

import pytest

from tests.smoke.conftest import SmokeAgent, authed_request, unique_vendor


def _credential_body(vendor: str) -> dict[str, object]:
    return {
        "type": "bearer_token",
        "name": f"smoke-cred-{uuid.uuid4().hex[:8]}",
        "api": {"vendor": vendor, "name": "petstore", "version": "3.0.0"},
        "provider": "static",
        "token": "sk-test-secret-value",
    }


def _create_credential(base_url: str, agent: SmokeAgent, vendor: str) -> str:
    body, status = authed_request(
        f"{base_url}/credentials",
        method="POST",
        token=agent.owner_token,
        body=_credential_body(vendor),
    )
    assert status == 201, f"Credential creation failed: {status} {body}"
    assert isinstance(body, dict)
    credential_id: str = body["credential"]["credential_id"]
    return credential_id


@pytest.mark.smoke
def test_create_credential(base_url: str, test_agent: SmokeAgent) -> None:
    """POST /credentials with bearer_token type returns 201 with secret."""
    vendor = unique_vendor("cred")
    body, status = authed_request(
        f"{base_url}/credentials",
        method="POST",
        token=test_agent.owner_token,
        body=_credential_body(vendor),
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
    creation = _credential_body(vendor)
    creation["token"] = "sk-hidden-value"
    authed_request(
        f"{base_url}/credentials",
        method="POST",
        token=test_agent.owner_token,
        body=creation,
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
def test_bind_credential_to_agent(base_url: str, test_agent: SmokeAgent) -> None:
    """POST /agents/{id}/credentials returns 201 with binding details."""
    credential_id = _create_credential(base_url, test_agent, unique_vendor("bind-cred"))

    bind_body, bind_status = authed_request(
        f"{base_url}/agents/{test_agent.agent_id}/credentials",
        method="POST",
        token=test_agent.owner_token,
        body={"credential_id": credential_id},
    )
    assert bind_status == 201
    assert isinstance(bind_body, dict)
    assert bind_body["credential_id"] == credential_id
    assert bind_body["agent_id"] == test_agent.agent_id


@pytest.mark.smoke
def test_list_agent_credentials(base_url: str, test_agent: SmokeAgent) -> None:
    """GET /agents/{id}/credentials lists the direct binding."""
    credential_id = _create_credential(base_url, test_agent, unique_vendor("list-cred"))
    _, bind_status = authed_request(
        f"{base_url}/agents/{test_agent.agent_id}/credentials",
        method="POST",
        token=test_agent.owner_token,
        body={"credential_id": credential_id},
    )
    assert bind_status == 201

    body, status = authed_request(
        f"{base_url}/agents/{test_agent.agent_id}/credentials",
        token=test_agent.owner_token,
    )
    assert status == 200
    assert isinstance(body, dict)
    credential_ids = [b["credential_id"] for b in body["data"]]
    assert credential_id in credential_ids


@pytest.mark.smoke
def test_binding_permission_rules_round_trip(base_url: str, test_agent: SmokeAgent) -> None:
    """PUT then GET the per-binding permission rules for a direct binding."""
    credential_id = _create_credential(base_url, test_agent, unique_vendor("rules-cred"))
    _, bind_status = authed_request(
        f"{base_url}/agents/{test_agent.agent_id}/credentials",
        method="POST",
        token=test_agent.owner_token,
        body={"credential_id": credential_id},
    )
    assert bind_status == 201

    rules_url = f"{base_url}/credentials/{credential_id}/agents/{test_agent.agent_id}/permissions"
    put_body, put_status = authed_request(
        rules_url,
        method="PUT",
        token=test_agent.owner_token,
        body=[{"effect": "allow", "methods": ["GET"], "path": "/v1/.*", "match_mode": "regex"}],
    )
    assert put_status == 200, f"rules PUT failed: {put_status} {put_body}"

    body, status = authed_request(rules_url, token=test_agent.owner_token)
    assert status == 200
    assert isinstance(body, dict)
    assert len(body["data"]) == 1
    rule = body["data"][0]
    assert rule["effect"] == "allow"
    assert rule["path"] == "/v1/.*"


@pytest.mark.smoke
def test_unbind_credential_suspends_then_purges(base_url: str, test_agent: SmokeAgent) -> None:
    """DELETE /agents/{id}/credentials/{cid} suspends; purge=true removes the row."""
    credential_id = _create_credential(base_url, test_agent, unique_vendor("unbind-cred"))
    _, bind_status = authed_request(
        f"{base_url}/agents/{test_agent.agent_id}/credentials",
        method="POST",
        token=test_agent.owner_token,
        body={"credential_id": credential_id},
    )
    assert bind_status == 201

    # Default unbind = suspend (reversible).
    _, status = authed_request(
        f"{base_url}/agents/{test_agent.agent_id}/credentials/{credential_id}",
        method="DELETE",
        token=test_agent.owner_token,
    )
    assert status == 204

    # :resume restores the binding.
    _, status = authed_request(
        f"{base_url}/agents/{test_agent.agent_id}/credentials/{credential_id}:resume",
        method="POST",
        token=test_agent.owner_token,
    )
    assert status in (200, 204)

    # purge=true deletes the row outright.
    _, status = authed_request(
        f"{base_url}/agents/{test_agent.agent_id}/credentials/{credential_id}?purge=true",
        method="DELETE",
        token=test_agent.owner_token,
    )
    assert status == 204

    body, status = authed_request(
        f"{base_url}/agents/{test_agent.agent_id}/credentials",
        token=test_agent.owner_token,
    )
    assert status == 200
    assert isinstance(body, dict)
    assert credential_id not in [b["credential_id"] for b in body["data"]]
