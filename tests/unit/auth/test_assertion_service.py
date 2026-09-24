"""Unit tests for AssertionService."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm

from jentic_one.auth.services.assertion_service import AssertionService
from jentic_one.auth.services.errors import InvalidGrantError


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    ctx.config.auth.canonical_base_url = "https://auth.example.com"
    ctx.config.auth.assertion_max_ttl_seconds = 300
    ctx.config.auth.access_ttl_seconds = 3600
    ctx.config.auth.refresh_ttl_seconds = 604800
    return ctx


def _generate_keypair() -> tuple[Ed25519PrivateKey, dict[str, Any]]:
    """Generate an Ed25519 key pair and return (private_key, jwks_dict)."""
    private_key = Ed25519PrivateKey.generate()
    algo = OKPAlgorithm()
    jwk_dict = algo.to_jwk(private_key.public_key())
    if isinstance(jwk_dict, str):
        jwk_dict = json.loads(jwk_dict)
    jwk_dict["kid"] = "test-key-1"
    jwks = {"keys": [jwk_dict]}
    return private_key, jwks


def _make_assertion(
    private_key: Ed25519PrivateKey,
    *,
    iss: str = "agnt_test123",
    aud: str = "https://auth.example.com/oauth/token",
    exp: float | None = None,
    jti: str = "unique-jti-1",
    kid: str = "test-key-1",
) -> str:
    now = time.time()
    payload = {
        "iss": iss,
        "aud": aud,
        "exp": exp if exp is not None else now + 60,
        "iat": now,
        "jti": jti,
    }
    return jwt.encode(payload, private_key, algorithm="EdDSA", headers={"kid": kid})


def _make_active_agent(jwks: dict[str, Any]) -> MagicMock:
    agent = MagicMock()
    agent.id = "agnt_test123"
    agent.status = "active"
    agent.jwks = jwks
    return agent


@patch("jentic_one.auth.services.assertion_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.assertion_service.AgentRepository")
@patch("jentic_one.auth.services.assertion_service.TokenService")
async def test_verify_and_exchange_happy_path(
    mock_token_svc_cls: MagicMock,
    mock_agent_repo: MagicMock,
    mock_scope_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    grant = MagicMock()
    grant.scope = "read"
    mock_scope_repo.list_for_actor = AsyncMock(return_value=[grant])

    token_svc_instance = MagicMock()
    token_svc_instance.issue_pair = AsyncMock(return_value=("at_new", "rt_new"))
    mock_token_svc_cls.return_value = token_svc_instance

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key)
    access, refresh, scopes = await svc.verify_and_exchange(assertion)

    assert access == "at_new"
    assert refresh == "rt_new"
    assert scopes == ["read"]


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_pending_agent_gets_distinct_pending_detail(
    mock_agent_repo: MagicMock,
) -> None:
    """A pending agent with a VALID signature gets the distinct pending detail.

    This is what lets the CLI's register/setup/wizard approval wait poll instead
    of aborting: self-hosted backends mint no claim tokens, so the pending
    signal must be distinguishable from a hard assertion failure.
    """
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    agent.status = "pending"
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key)
    with pytest.raises(InvalidGrantError, match="pending approval"):
        await svc.verify_and_exchange(assertion)


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_pending_agent_bad_signature_stays_generic(
    mock_agent_repo: MagicMock,
) -> None:
    """Without proof of key possession the status is NOT revealed: a bad
    signature gets the generic rejection regardless of the agent's status."""
    ctx = _make_ctx()
    _, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    agent.status = "pending"
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    other_private_key = Ed25519PrivateKey.generate()
    svc = AssertionService(ctx)
    assertion = _make_assertion(other_private_key)
    with pytest.raises(InvalidGrantError, match="Assertion is invalid"):
        await svc.verify_and_exchange(assertion)


@pytest.mark.parametrize("status", ["rejected", "disabled", "archived"])
@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_terminal_statuses_stay_generic(
    mock_agent_repo: MagicMock, status: str
) -> None:
    """Terminal (non-waitable) statuses keep the generic rejection even with a
    valid signature — only PENDING gets a probe signal, because only PENDING
    is worth polling on."""
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    agent.status = status
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key)
    with pytest.raises(InvalidGrantError, match="Assertion is invalid"):
        await svc.verify_and_exchange(assertion)


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_rejects_bad_signature(mock_agent_repo: MagicMock) -> None:
    ctx = _make_ctx()
    _, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    other_private_key = Ed25519PrivateKey.generate()
    svc = AssertionService(ctx)
    assertion = _make_assertion(other_private_key)
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(assertion)


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_rejects_expired_assertion(mock_agent_repo: MagicMock) -> None:
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key, exp=time.time() - 10)
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(assertion)


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_rejects_wrong_audience(mock_agent_repo: MagicMock) -> None:
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key, aud="https://wrong.example.com/oauth/token")
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(assertion)


@patch("jentic_one.auth.services.assertion_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.assertion_service.AgentRepository")
@patch("jentic_one.auth.services.assertion_service.TokenService")
async def test_verify_rejects_replayed_jti(
    mock_token_svc_cls: MagicMock,
    mock_agent_repo: MagicMock,
    mock_scope_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    private_key, jwks = _generate_keypair()
    agent = _make_active_agent(jwks)
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=agent)
    mock_scope_repo.list_for_actor = AsyncMock(return_value=[])

    token_svc_instance = MagicMock()
    token_svc_instance.issue_pair = AsyncMock(return_value=("at_1", "rt_1"))
    mock_token_svc_cls.return_value = token_svc_instance

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key, jti="same-jti")
    await svc.verify_and_exchange(assertion)

    assertion2 = _make_assertion(private_key, jti="same-jti")
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(assertion2)


async def test_verify_rejects_non_eddsa_algorithm() -> None:
    ctx = _make_ctx()
    svc = AssertionService(ctx)
    fake_token = jwt.encode({"iss": "test"}, "secret", algorithm="HS256")
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(fake_token)


@patch("jentic_one.auth.services.assertion_service.AgentRepository")
async def test_verify_rejects_unknown_agent(mock_agent_repo: MagicMock) -> None:
    ctx = _make_ctx()
    private_key, _ = _generate_keypair()
    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=None)

    svc = AssertionService(ctx)
    assertion = _make_assertion(private_key)
    with pytest.raises(InvalidGrantError, match="invalid"):
        await svc.verify_and_exchange(assertion)
