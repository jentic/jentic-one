"""Unit tests verifying ownership enforcement on AgentService.update_jwks.

Only the agent's owner or an org:admin may update the JWKS. A non-owner caller
with agents:write but not org:admin must be rejected with AgentWriteAccessDeniedError.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.errors import AgentWriteAccessDeniedError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType

_VALID_JWKS: dict[str, object] = {
    "keys": [
        {
            "kty": "OKP",
            "crv": "Ed25519",
            "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo",
        }
    ]
}


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_agent(owner_id: str = "usr_owner", status: str = "active") -> MagicMock:
    agent = MagicMock()
    agent.id = "agnt_test"
    agent.name = "test-agent"
    agent.description = None
    agent.owner_id = owner_id
    agent.registered_by = "self"
    agent.parent_agent_id = None
    agent.approved_by = None
    agent.status = status
    agent.denial_reason = None
    agent.denied_by = None
    agent.created_at = datetime(2026, 8, 1, tzinfo=UTC)
    agent.approved_at = None
    agent.jwks = None
    return agent


@patch("jentic_one.auth.services.agent_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_owner_can_update_jwks(
    mock_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    agent = _make_agent(owner_id="usr_owner")
    mock_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    svc = AgentService(ctx)
    identity = Identity(
        sub="usr_owner",
        email="owner@example.com",
        permissions=["agents:write"],
        actor_type=ActorType.USER,
    )

    await svc.update_jwks("agnt_test", jwks=_VALID_JWKS, identity=identity)

    assert agent.jwks == _VALID_JWKS


@patch("jentic_one.auth.services.agent_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_admin_can_update_jwks_for_any_agent(
    mock_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    agent = _make_agent(owner_id="usr_someone_else")
    mock_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    svc = AgentService(ctx)
    identity = Identity(
        sub="usr_admin",
        email="admin@example.com",
        permissions=["agents:write", "org:admin"],
        actor_type=ActorType.USER,
    )

    await svc.update_jwks("agnt_test", jwks=_VALID_JWKS, identity=identity)

    assert agent.jwks == _VALID_JWKS


@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_non_owner_without_admin_cannot_update_jwks(
    mock_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    agent = _make_agent(owner_id="usr_owner")
    mock_repo.get_by_id_for_update = AsyncMock(return_value=agent)

    svc = AgentService(ctx)
    identity = Identity(
        sub="usr_other",
        email="other@example.com",
        permissions=["agents:write"],
        actor_type=ActorType.USER,
    )

    with pytest.raises(AgentWriteAccessDeniedError):
        await svc.update_jwks("agnt_test", jwks=_VALID_JWKS, identity=identity)
