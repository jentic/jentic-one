"""Unit tests for AgentAuthService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.auth.services.agent_auth_service import AgentAuthService
from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    InvalidTransitionError,
    NoApiKeyError,
)
from jentic_one.shared.auth.identity import Identity


def _admin_identity() -> Identity:
    return Identity(
        sub="usr_admin",
        email="admin@example.com",
        permissions=["agents:write", "org:admin"],
    )


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_agent_row(*, status: str = "active", owner_id: str = "usr_admin") -> MagicMock:
    row = MagicMock()
    row.id = "agnt_test123"
    row.status = status
    row.owner_id = owner_id
    return row


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_auth_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.auth.services.agent_auth_service.AgentCredentialRepository")
@patch("jentic_one.auth.services.agent_auth_service.AgentRepository")
async def test_register_api_key_success(
    mock_agent_repo: MagicMock,
    mock_cred_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    svc = AgentAuthService(ctx)

    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=_make_agent_row(status="active"))
    mock_cred_repo.set_api_key_hash = AsyncMock()

    key = await svc.register_api_key("agnt_test123", identity=_admin_identity())

    assert key.startswith("jak_")
    mock_cred_repo.set_api_key_hash.assert_called_once()
    mock_audit.assert_called_once()


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_auth_service.AgentRepository")
async def test_register_api_key_agent_not_found(
    mock_agent_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = AgentAuthService(ctx)

    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=None)

    with pytest.raises(ActorNotFoundError):
        await svc.register_api_key("agnt_nonexist", identity=_admin_identity())


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_auth_service.AgentRepository")
async def test_register_api_key_agent_not_active(
    mock_agent_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = AgentAuthService(ctx)

    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=_make_agent_row(status="pending"))

    with pytest.raises(InvalidTransitionError):
        await svc.register_api_key("agnt_test123", identity=_admin_identity())


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_auth_service.AgentRepository")
async def test_register_api_key_agent_disabled(
    mock_agent_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = AgentAuthService(ctx)

    mock_agent_repo.get_by_id_for_update = AsyncMock(
        return_value=_make_agent_row(status="disabled")
    )

    with pytest.raises(InvalidTransitionError):
        await svc.register_api_key("agnt_test123", identity=_admin_identity())


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_auth_service.AgentRepository")
async def test_register_api_key_not_owner(
    mock_agent_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = AgentAuthService(ctx)

    mock_agent_repo.get_by_id_for_update = AsyncMock(
        return_value=_make_agent_row(status="active", owner_id="usr_other")
    )
    non_admin = Identity(
        sub="usr_nonadmin",
        email="user@example.com",
        permissions=["agents:write"],
    )

    with pytest.raises(ActorNotFoundError):
        await svc.register_api_key("agnt_test123", identity=non_admin)


# ---------------------------------------------------------------------------
# revoke_api_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_auth_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.auth.services.agent_auth_service.AgentCredentialRepository")
@patch("jentic_one.auth.services.agent_auth_service.AgentRepository")
async def test_revoke_api_key_success(
    mock_agent_repo: MagicMock,
    mock_cred_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    svc = AgentAuthService(ctx)

    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=_make_agent_row(status="active"))
    mock_cred_repo.clear_api_key_hash = AsyncMock(return_value=True)

    await svc.revoke_api_key("agnt_test123", identity=_admin_identity())

    mock_cred_repo.clear_api_key_hash.assert_called_once()
    mock_audit.assert_called_once()


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_auth_service.AgentCredentialRepository")
@patch("jentic_one.auth.services.agent_auth_service.AgentRepository")
async def test_revoke_api_key_no_key_exists(
    mock_agent_repo: MagicMock,
    mock_cred_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = AgentAuthService(ctx)

    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=_make_agent_row(status="active"))
    mock_cred_repo.clear_api_key_hash = AsyncMock(return_value=False)

    with pytest.raises(NoApiKeyError):
        await svc.revoke_api_key("agnt_test123", identity=_admin_identity())


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_auth_service.AgentRepository")
async def test_revoke_api_key_agent_not_found(
    mock_agent_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = AgentAuthService(ctx)

    mock_agent_repo.get_by_id_for_update = AsyncMock(return_value=None)

    with pytest.raises(ActorNotFoundError):
        await svc.revoke_api_key("agnt_nonexist", identity=_admin_identity())


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_auth_service.AgentRepository")
async def test_revoke_api_key_not_owner(
    mock_agent_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = AgentAuthService(ctx)

    mock_agent_repo.get_by_id_for_update = AsyncMock(
        return_value=_make_agent_row(status="active", owner_id="usr_other")
    )
    non_admin = Identity(
        sub="usr_nonadmin",
        email="user@example.com",
        permissions=["agents:write"],
    )

    with pytest.raises(ActorNotFoundError):
        await svc.revoke_api_key("agnt_test123", identity=non_admin)


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_auth_service.AgentRepository")
async def test_revoke_api_key_agent_not_active(
    mock_agent_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = AgentAuthService(ctx)

    mock_agent_repo.get_by_id_for_update = AsyncMock(
        return_value=_make_agent_row(status="disabled")
    )

    with pytest.raises(InvalidTransitionError):
        await svc.revoke_api_key("agnt_test123", identity=_admin_identity())
