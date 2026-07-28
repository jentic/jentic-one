"""Smoke tests for agent registration and approval lifecycle (DCR)."""

from __future__ import annotations

import json
import time
import uuid

import jwt as pyjwt
import pytest
from jwt.algorithms import OKPAlgorithm

from tests.smoke.conftest import (
    SmokeAgent,
    _skip_if_no_admin_surface,
    agent_token_exchange,
    approve_agent,
    authed_request,
    generate_ed25519_jwks,
    register_agent,
)


@pytest.mark.smoke
def test_register_agent_returns_pending(base_url: str) -> None:
    """POST /register with valid JWKS returns 201 with pending status."""
    _skip_if_no_admin_surface()
    client_name = f"smoke-reg-{uuid.uuid4().hex[:12]}"
    _, jwks = generate_ed25519_jwks()

    body, status = authed_request(
        f"{base_url}/register",
        method="POST",
        body={"client_name": client_name, "jwks": jwks},
    )
    assert status == 201
    assert isinstance(body, dict)
    assert "client_id" in body
    assert "registration_access_token" in body
    assert body["status"] == "pending"


@pytest.mark.smoke
def test_poll_pending_status(base_url: str) -> None:
    """GET /register/{agent_id} with RAT returns pending status."""
    _skip_if_no_admin_surface()
    client_name = f"smoke-poll-{uuid.uuid4().hex[:12]}"
    _, jwks = generate_ed25519_jwks()

    agent_id, rat = register_agent(base_url, client_name, jwks)

    body, status = authed_request(
        f"{base_url}/register/{agent_id}",
        token=rat,
    )
    assert status == 200
    assert isinstance(body, dict)
    assert body["status"] == "pending"


@pytest.mark.smoke
def test_approve_invalidates_rat(base_url: str, admin_token: str) -> None:
    """Approval consumes the single-use RAT (RFC 7592), so polling it returns 401.

    Approval clears the registration access token (see
    ``AgentRepository.set_approval``), so the RAT can no longer be used to poll
    status. Active status is instead confirmed via token exchange in
    ``test_agent_token_exchange``.
    """
    _skip_if_no_admin_surface()
    client_name = f"smoke-approve-{uuid.uuid4().hex[:12]}"
    private_key, jwks = generate_ed25519_jwks()

    agent_id, rat = register_agent(base_url, client_name, jwks)
    approve_agent(base_url, agent_id, admin_token)

    _, status = authed_request(
        f"{base_url}/register/{agent_id}",
        token=rat,
    )
    assert status == 401

    # The approved agent is active: token exchange succeeds.
    access_token = agent_token_exchange(base_url, agent_id, private_key)
    assert access_token


@pytest.mark.smoke
def test_agent_token_exchange(base_url: str, admin_token: str) -> None:
    """After approval, JWT-bearer token exchange succeeds and the token is usable."""
    _skip_if_no_admin_surface()
    client_name = f"smoke-exchange-{uuid.uuid4().hex[:12]}"
    private_key, jwks = generate_ed25519_jwks()

    agent_id, _ = register_agent(base_url, client_name, jwks)
    approve_agent(base_url, agent_id, admin_token)

    access_token = agent_token_exchange(base_url, agent_id, private_key)
    assert access_token

    body, status = authed_request(f"{base_url}/agents/{agent_id}", token=access_token)
    assert status == 200
    assert isinstance(body, dict)
    assert body["id"] == agent_id


@pytest.mark.smoke
def test_list_agents_includes_registered(base_url: str, test_agent: SmokeAgent) -> None:
    """GET /agents?status=active includes the newly registered agent."""
    _skip_if_no_admin_surface()
    body, status = authed_request(
        f"{base_url}/agents?status=active",
        token=test_agent.owner_token,
    )
    assert status == 200
    assert isinstance(body, dict)
    agent_ids = [a["id"] for a in body["data"]]
    assert test_agent.agent_id in agent_ids


@pytest.mark.smoke
def test_disable_blocks_token_exchange(base_url: str, admin_token: str) -> None:
    """Disabling an agent blocks token exchange; re-enabling restores it."""
    _skip_if_no_admin_surface()
    client_name = f"smoke-disable-{uuid.uuid4().hex[:12]}"
    private_key, jwks = generate_ed25519_jwks()

    agent_id, _ = register_agent(base_url, client_name, jwks)
    approve_agent(base_url, agent_id, admin_token)

    # Verify exchange works before disable
    token = agent_token_exchange(base_url, agent_id, private_key)
    assert token

    # Disable
    _, disable_status = authed_request(
        f"{base_url}/agents/{agent_id}:disable",
        method="POST",
        token=admin_token,
    )
    assert disable_status == 204

    # Token exchange should fail
    algo = OKPAlgorithm()
    priv_jwk: dict[str, object] = json.loads(algo.to_jwk(private_key))
    now = int(time.time())
    payload = {
        "iss": agent_id,
        "aud": f"{base_url}/oauth/token",
        "exp": now + 120,
        "jti": uuid.uuid4().hex,
    }
    assertion = pyjwt.encode(
        payload,
        pyjwt.api_jwk.PyJWK(priv_jwk, "EdDSA").key,
        algorithm="EdDSA",
    )
    _, exchange_status = authed_request(
        f"{base_url}/oauth/token",
        method="POST",
        body={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
    )
    assert exchange_status in (401, 400)

    # Re-enable
    _, enable_status = authed_request(
        f"{base_url}/agents/{agent_id}:enable",
        method="POST",
        token=admin_token,
    )
    assert enable_status == 204

    # Exchange should work again
    token2 = agent_token_exchange(base_url, agent_id, private_key)
    assert token2
