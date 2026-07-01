"""Unit tests for agent scope management (create with scopes, get, replace)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.errors import ActorNotFoundError, InvalidTransitionError
from jentic_one.auth.services.schemas.agents import AgentCreatePayload
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.scopes import DEFAULT_AGENT_SCOPES


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    ctx.admin_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _identity() -> Identity:
    return Identity(
        sub="usr_owner1",
        email="owner@example.com",
        permissions=["agents:write", "agents:read", "org:admin"],
    )


def _mock_agent(agent_id: str = "agnt_test1", status: str = "active") -> MagicMock:
    agent = MagicMock()
    agent.id = agent_id
    agent.name = "test-agent"
    agent.description = None
    agent.owner_id = "usr_owner1"
    agent.registered_by = "usr_owner1"
    agent.parent_agent_id = None
    agent.approved_by = None
    agent.status = status
    agent.denial_reason = None
    agent.denied_by = None
    agent.created_at = datetime(2026, 6, 23, tzinfo=UTC)
    agent.approved_at = None
    return agent


@patch("jentic_one.auth.services.agent_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.agent_service.AgentRepository")
@patch("jentic_one.auth.services.agent_service.AgentCredentialRepository")
async def test_create_agent_with_scopes(
    mock_cred_repo: MagicMock, mock_agent_repo: MagicMock, mock_scope_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    agent = _mock_agent()
    mock_agent_repo.create = AsyncMock(return_value=agent)
    mock_scope_repo.grant = AsyncMock()

    svc = AgentService(ctx)
    payload = AgentCreatePayload(name="test-agent", scopes=["capabilities:execute", "agents:write"])
    await svc.create(payload, owner_id="usr_owner1", identity=_identity())

    assert mock_scope_repo.grant.call_count == 2
    calls = mock_scope_repo.grant.call_args_list
    assert calls[0].kwargs["scope"] == "capabilities:execute"
    assert calls[0].kwargs["actor_type"] == "agent"
    assert calls[0].kwargs["actor_id"] == "agnt_test1"
    assert calls[1].kwargs["scope"] == "agents:write"


@patch("jentic_one.auth.services.agent_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.agent_service.AgentRepository")
@patch("jentic_one.auth.services.agent_service.AgentCredentialRepository")
async def test_create_grants_default_scopes_when_none_specified(
    mock_cred_repo: MagicMock, mock_agent_repo: MagicMock, mock_scope_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    agent = _mock_agent()
    mock_agent_repo.create = AsyncMock(return_value=agent)
    mock_scope_repo.grant = AsyncMock()

    svc = AgentService(ctx)
    payload = AgentCreatePayload(name="test-agent")
    await svc.create(payload, owner_id="usr_owner1", identity=_identity())

    assert mock_scope_repo.grant.call_count == len(DEFAULT_AGENT_SCOPES)
    granted = [call.kwargs["scope"] for call in mock_scope_repo.grant.call_args_list]
    assert granted == list(DEFAULT_AGENT_SCOPES)
    assert "catalog:import" in granted


@patch("jentic_one.auth.services.agent_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.agent_service.AgentRepository")
@patch("jentic_one.auth.services.agent_service.AgentCredentialRepository")
async def test_get_scopes(
    mock_cred_repo: MagicMock, mock_agent_repo: MagicMock, mock_scope_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    agent = _mock_agent()
    mock_agent_repo.get_by_id = AsyncMock(return_value=agent)
    mock_cred_repo.has_api_key = AsyncMock(return_value=False)

    grant1 = MagicMock()
    grant1.scope = "capabilities:execute"
    grant2 = MagicMock()
    grant2.scope = "agents:read"
    mock_scope_repo.list_for_actor = AsyncMock(return_value=[grant1, grant2])

    svc = AgentService(ctx)
    scopes = await svc.get_scopes("agnt_test1", identity=_identity())

    assert scopes == ["capabilities:execute", "agents:read"]
    mock_scope_repo.list_for_actor.assert_called_once()


@patch("jentic_one.auth.services.agent_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_replace_scopes(mock_agent_repo: MagicMock, mock_scope_repo: MagicMock) -> None:
    ctx = _make_ctx()
    agent = _mock_agent()
    mock_agent_repo.get_by_id = AsyncMock(return_value=agent)
    mock_scope_repo.revoke_all = AsyncMock(return_value=2)
    mock_scope_repo.grant = AsyncMock()

    svc = AgentService(ctx)
    result = await svc.replace_scopes("agnt_test1", ["new:scope"], identity=_identity())

    assert result == ["new:scope"]
    mock_scope_repo.revoke_all.assert_called_once()
    mock_scope_repo.grant.assert_called_once()
    assert mock_scope_repo.grant.call_args.kwargs["scope"] == "new:scope"


@patch("jentic_one.auth.services.agent_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_replace_scopes_empty_clears_all(
    mock_agent_repo: MagicMock, mock_scope_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    agent = _mock_agent()
    mock_agent_repo.get_by_id = AsyncMock(return_value=agent)
    mock_scope_repo.revoke_all = AsyncMock(return_value=2)

    svc = AgentService(ctx)
    result = await svc.replace_scopes("agnt_test1", [], identity=_identity())

    assert result == []
    mock_scope_repo.revoke_all.assert_called_once()
    mock_scope_repo.grant.assert_not_called()


@patch("jentic_one.auth.services.agent_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_replace_scopes_not_found(
    mock_agent_repo: MagicMock, mock_scope_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    mock_agent_repo.get_by_id = AsyncMock(return_value=None)

    svc = AgentService(ctx)
    with pytest.raises(ActorNotFoundError):
        await svc.replace_scopes("agnt_missing", ["x"], identity=_identity())


@patch("jentic_one.auth.services.agent_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_replace_scopes_archived_raises(
    mock_agent_repo: MagicMock, mock_scope_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    agent = _mock_agent(status="archived")
    mock_agent_repo.get_by_id = AsyncMock(return_value=agent)

    svc = AgentService(ctx)
    with pytest.raises(InvalidTransitionError):
        await svc.replace_scopes("agnt_test1", ["x"], identity=_identity())


@patch("jentic_one.auth.services.agent_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.agent_service.AgentRepository")
@patch("jentic_one.auth.services.agent_service.AgentCredentialRepository")
async def test_create_uses_explicit_scopes_over_defaults(
    mock_cred_repo: MagicMock, mock_agent_repo: MagicMock, mock_scope_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    agent = _mock_agent()
    mock_agent_repo.create = AsyncMock(return_value=agent)
    mock_scope_repo.grant = AsyncMock()

    svc = AgentService(ctx)
    explicit = ["capabilities:execute", "agents:write"]
    payload = AgentCreatePayload(name="test-agent", scopes=explicit)
    await svc.create(payload, owner_id="usr_owner1", identity=_identity())

    assert mock_scope_repo.grant.call_count == 2
    granted = [call.kwargs["scope"] for call in mock_scope_repo.grant.call_args_list]
    assert granted == explicit


@patch("jentic_one.auth.services.agent_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_approve_grants_default_scopes_when_no_existing_grants(
    mock_agent_repo: MagicMock, mock_scope_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    agent = _mock_agent(status="pending")
    mock_agent_repo.get_by_id = AsyncMock(return_value=agent)
    mock_agent_repo.set_approval = AsyncMock(return_value=_mock_agent(status="active"))
    mock_scope_repo.list_for_actor = AsyncMock(return_value=[])
    mock_scope_repo.grant = AsyncMock()

    svc = AgentService(ctx)
    await svc.approve("agnt_test1", identity=_identity())

    assert mock_scope_repo.grant.call_count == len(DEFAULT_AGENT_SCOPES)
    granted = [call.kwargs["scope"] for call in mock_scope_repo.grant.call_args_list]
    assert granted == list(DEFAULT_AGENT_SCOPES)


@patch("jentic_one.auth.services.agent_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_approve_preserves_existing_scopes(
    mock_agent_repo: MagicMock, mock_scope_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    agent = _mock_agent(status="pending")
    mock_agent_repo.get_by_id = AsyncMock(return_value=agent)
    mock_agent_repo.set_approval = AsyncMock(return_value=_mock_agent(status="active"))

    existing_grant = MagicMock()
    existing_grant.scope = "capabilities:execute"
    mock_scope_repo.list_for_actor = AsyncMock(return_value=[existing_grant])
    mock_scope_repo.grant = AsyncMock()

    svc = AgentService(ctx)
    await svc.approve("agnt_test1", identity=_identity())

    mock_scope_repo.grant.assert_not_called()
