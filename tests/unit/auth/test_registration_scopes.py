"""Unit tests for DCR scope assignment."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from jentic_one.auth.services.registration_service import (
    RegistrationService,
    _registrable_scopes,
)


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)

    # register() routes its write through run_in_transaction; mirror the real
    # helper by invoking the passed callback against the mock session so the
    # _write body (create_dcr + scope grants) actually runs.
    async def _run_in_transaction(fn: Any, **_kwargs: Any) -> Any:
        return await fn(mock_session)

    ctx.admin_db.run_in_transaction = AsyncMock(side_effect=_run_in_transaction)
    ctx.config.auth.rat_ttl_seconds = 900
    ctx.config.auth.claim_ttl_seconds = 900
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


def test_registrable_scopes_drops_admin_only_retired_and_unknown() -> None:
    scope = (
        "org:admin agents:write capabilities:execute toolkits:read "
        "not:a:perm capabilities:read capabilities:execute"
    )
    assert _registrable_scopes(scope) == ["capabilities:execute", "capabilities:read"]


def test_registrable_scopes_empty_or_none() -> None:
    assert _registrable_scopes(None) == []
    assert _registrable_scopes("") == []
    assert _registrable_scopes("   ") == []


@patch("jentic_one.auth.services.registration_service.ActorPermissionGrantRepository")
@patch("jentic_one.auth.services.registration_service.AgentRepository")
async def test_dcr_with_scope_creates_grants(
    mock_repo: MagicMock, mock_permission_repo: MagicMock, stock_claim_minter: None
) -> None:
    ctx = _make_ctx()
    agent = MagicMock()
    agent.id = "agnt_dcr1"
    agent.status = "pending"
    mock_repo.create_dcr = AsyncMock(return_value=agent)
    mock_permission_repo.grant = AsyncMock()

    svc = RegistrationService(ctx)
    await svc.register("my-agent", _valid_jwks(), scope="capabilities:execute agents:write")

    assert mock_permission_repo.grant.call_count == 1
    calls = mock_permission_repo.grant.call_args_list
    assert calls[0].kwargs["permission"] == "capabilities:execute"
    assert calls[0].kwargs["actor_id"] == "agnt_dcr1"
    assert calls[0].kwargs["actor_type"] == "agent"
    assert calls[0].kwargs["granted_by"] is None
    assert calls[0].kwargs["created_by"] == "dcr"


@patch("jentic_one.auth.services.registration_service.ActorPermissionGrantRepository")
@patch("jentic_one.auth.services.registration_service.AgentRepository")
async def test_dcr_without_scope_no_grants(
    mock_repo: MagicMock, mock_permission_repo: MagicMock, stock_claim_minter: None
) -> None:
    ctx = _make_ctx()
    agent = MagicMock()
    agent.id = "agnt_dcr2"
    agent.status = "pending"
    mock_repo.create_dcr = AsyncMock(return_value=agent)

    svc = RegistrationService(ctx)
    await svc.register("my-agent", _valid_jwks())

    mock_permission_repo.grant.assert_not_called()


@patch("jentic_one.auth.services.registration_service.ActorPermissionGrantRepository")
@patch("jentic_one.auth.services.registration_service.AgentRepository")
async def test_dcr_with_empty_scope_no_grants(
    mock_repo: MagicMock, mock_permission_repo: MagicMock, stock_claim_minter: None
) -> None:
    ctx = _make_ctx()
    agent = MagicMock()
    agent.id = "agnt_dcr3"
    agent.status = "pending"
    mock_repo.create_dcr = AsyncMock(return_value=agent)

    svc = RegistrationService(ctx)
    await svc.register("my-agent", _valid_jwks(), scope="")

    mock_permission_repo.grant.assert_not_called()
