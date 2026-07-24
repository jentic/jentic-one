"""Smoke tests for the access request flow (file, list, get, decide, withdraw).

The items use ``scope:grant`` for ``apis:write`` — the one scope that is in
``GRANTABLE_SCOPES`` but deliberately not in ``DEFAULT_AGENT_SCOPES``, so it is
exactly what a real agent would file for and needs no toolkit/credential
prerequisites (a ``credential:bind`` item with a ``to_id`` requires the agent
to already be bound to that toolkit).
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.smoke.conftest import SmokeAgent, authed_request

_SCOPE_GRANT_ITEM = {
    "resource_type": "scope",
    "action": "grant",
    "resource_id": "apis:write",
}


def _file_scope_request(base_url: str, agent: SmokeAgent, reason: str) -> dict[str, Any]:
    """Helper: file a scope:grant access request as the agent, return the body."""
    body, status = authed_request(
        f"{base_url}/access-requests",
        method="POST",
        token=agent.access_token,
        body={"reason": reason, "items": [_SCOPE_GRANT_ITEM]},
    )
    assert status == 202, f"Filing access request failed: {status} {body}"
    assert isinstance(body, dict)
    return body


@pytest.mark.smoke
def test_file_access_request(base_url: str, test_agent: SmokeAgent) -> None:
    """POST /access-requests creates a pending request."""
    body = _file_scope_request(base_url, test_agent, "Smoke test access request")
    assert "id" in body
    assert body["status"] == "pending"
    assert body["items"][0]["resource_id"] == "apis:write"


@pytest.mark.smoke
def test_list_access_requests(base_url: str, test_agent: SmokeAgent) -> None:
    """GET /access-requests lists the filed request."""
    request_id = _file_scope_request(base_url, test_agent, "List test")["id"]

    body, status = authed_request(
        f"{base_url}/access-requests",
        token=test_agent.access_token,
    )
    assert status == 200
    assert isinstance(body, dict)
    request_ids = [r["id"] for r in body["data"]]
    assert request_id in request_ids


@pytest.mark.smoke
def test_get_access_request(base_url: str, test_agent: SmokeAgent) -> None:
    """GET /access-requests/{id} returns the request with items."""
    request_id = _file_scope_request(base_url, test_agent, "Get test")["id"]

    body, status = authed_request(
        f"{base_url}/access-requests/{request_id}",
        token=test_agent.access_token,
    )
    assert status == 200
    assert isinstance(body, dict)
    assert body["id"] == request_id
    assert "items" in body
    assert len(body["items"]) >= 1


@pytest.mark.smoke
def test_decide_access_request_approve(base_url: str, test_agent: SmokeAgent) -> None:
    """POST /access-requests/{id}:decide approves the request and applies the grant."""
    file_body = _file_scope_request(base_url, test_agent, "Decide test")
    request_id = file_body["id"]
    item_id = file_body["items"][0]["id"]

    decide_body, decide_status = authed_request(
        f"{base_url}/access-requests/{request_id}:decide",
        method="POST",
        token=test_agent.owner_token,
        body={
            "items": [
                {
                    "item_id": item_id,
                    "decision": "approved",
                    "decision_reason": "Smoke test approval",
                }
            ],
        },
    )
    assert decide_status == 200, f"Decide failed: {decide_status} {decide_body}"
    assert isinstance(decide_body, dict)
    assert decide_body["status"] == "approved"


@pytest.mark.smoke
def test_withdraw_access_request(base_url: str, test_agent: SmokeAgent) -> None:
    """POST /access-requests/{id}:withdraw moves to withdrawn."""
    request_id = _file_scope_request(base_url, test_agent, "Withdraw test")["id"]

    body, status = authed_request(
        f"{base_url}/access-requests/{request_id}:withdraw",
        method="POST",
        token=test_agent.access_token,
    )
    assert status == 200
    assert isinstance(body, dict)
    assert body["status"] == "withdrawn"
