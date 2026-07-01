"""Unit tests for RegistrationService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.auth.services.errors import InvalidGrantError, RegistrationAccessDeniedError
from jentic_one.auth.services.registration_service import RegistrationService, _hash_rat


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    ctx.admin_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.session.return_value.__aexit__ = AsyncMock(return_value=False)

    # register() routes its write through run_in_transaction; mirror the real
    # helper by invoking the passed callback against the mock session so tests
    # exercise the actual _write body (and its return value).
    async def _run_in_transaction(fn: Any, **_kwargs: Any) -> Any:
        return await fn(mock_session)

    ctx.admin_db.run_in_transaction = AsyncMock(side_effect=_run_in_transaction)
    ctx.config.auth.rat_ttl_seconds = 900
    ctx.config.auth.canonical_base_url = "https://auth.example.com"
    return ctx


def _valid_jwks() -> dict[str, Any]:
    return {
        "keys": [
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "x": "dGVzdC1wdWJsaWMta2V5LWJhc2U2NA",
                "kid": "key-1",
            }
        ]
    }


@patch("jentic_one.auth.services.registration_service.AgentRepository")
async def test_register_happy_path(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    agent = MagicMock()
    agent.id = "agnt_test123"
    agent.status = "pending"
    mock_repo.create_dcr = AsyncMock(return_value=agent)

    svc = RegistrationService(ctx)
    result = await svc.register("my-agent", _valid_jwks())

    assert result.client_id == "agnt_test123"
    assert result.registration_access_token.startswith("rat_")
    assert result.status == "pending"
    assert result.registration_client_uri == "https://auth.example.com/register/agnt_test123"


@patch("jentic_one.auth.services.registration_service.AgentRepository")
async def test_register_rejects_invalid_jwks_no_ed25519(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    svc = RegistrationService(ctx)

    jwks = {"keys": [{"kty": "RSA", "n": "abc", "e": "AQAB"}]}
    with pytest.raises(InvalidGrantError, match="Ed25519"):
        await svc.register("agent", jwks)


@patch("jentic_one.auth.services.registration_service.AgentRepository")
async def test_register_rejects_private_key_material(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    svc = RegistrationService(ctx)

    jwks = {
        "keys": [
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "x": "dGVzdA",
                "d": "cHJpdmF0ZQ",
            }
        ]
    }
    with pytest.raises(InvalidGrantError, match="private key material"):
        await svc.register("agent", jwks)


@patch("jentic_one.auth.services.registration_service.AgentRepository")
async def test_register_rejects_empty_keys(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    svc = RegistrationService(ctx)

    with pytest.raises(InvalidGrantError, match="at least one key"):
        await svc.register("agent", {"keys": []})


@patch("jentic_one.auth.services.registration_service.AgentRepository")
async def test_dcr_agent_remains_pending(mock_repo: MagicMock) -> None:
    """DCR-created agents must remain pending — never auto-approved."""
    ctx = _make_ctx()
    agent = MagicMock()
    agent.id = "agnt_dcr1"
    agent.status = "pending"
    agent.approved_by = None
    agent.approved_at = None
    mock_repo.create_dcr = AsyncMock(return_value=agent)

    svc = RegistrationService(ctx)
    result = await svc.register("dcr-agent", _valid_jwks())

    assert result.status == "pending"
    assert agent.approved_by is None
    assert agent.approved_at is None


@patch("jentic_one.auth.services.registration_service.AgentRepository")
async def test_poll_status_valid_rat(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    rat_plain = "rat_test-token"
    rat_hash = _hash_rat(rat_plain)

    agent = MagicMock()
    agent.id = "agnt_test123"
    agent.status = "pending"
    agent.registration_access_token_hash = rat_hash
    agent.rat_expires_at = datetime.now(UTC) + timedelta(minutes=10)
    mock_repo.get_by_id = AsyncMock(return_value=agent)

    svc = RegistrationService(ctx)
    result = await svc.poll_status("agnt_test123", rat_plain)

    assert result.client_id == "agnt_test123"
    assert result.status == "pending"


@patch("jentic_one.auth.services.registration_service.AgentRepository")
async def test_poll_status_wrong_rat(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    agent = MagicMock()
    agent.id = "agnt_test123"
    agent.registration_access_token_hash = "wrong_hash"
    agent.rat_expires_at = datetime.now(UTC) + timedelta(minutes=10)
    mock_repo.get_by_id = AsyncMock(return_value=agent)

    svc = RegistrationService(ctx)
    with pytest.raises(RegistrationAccessDeniedError):
        await svc.poll_status("agnt_test123", "rat_invalid")


@patch("jentic_one.auth.services.registration_service.AgentRepository")
async def test_poll_status_expired_rat(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    rat_plain = "rat_test-token"
    rat_hash = _hash_rat(rat_plain)

    agent = MagicMock()
    agent.id = "agnt_test123"
    agent.registration_access_token_hash = rat_hash
    agent.rat_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    mock_repo.get_by_id = AsyncMock(return_value=agent)

    svc = RegistrationService(ctx)
    with pytest.raises(RegistrationAccessDeniedError, match="expired"):
        await svc.poll_status("agnt_test123", rat_plain)


@patch("jentic_one.auth.services.registration_service.AgentRepository")
async def test_poll_status_unknown_agent(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    mock_repo.get_by_id = AsyncMock(return_value=None)

    svc = RegistrationService(ctx)
    with pytest.raises(RegistrationAccessDeniedError):
        await svc.poll_status("agnt_unknown", "rat_any")
