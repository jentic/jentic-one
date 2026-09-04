"""Unit tests for TokenService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.services.token_service import (
    ACCESS_TOKEN_PREFIX,
    REFRESH_TOKEN_PREFIX,
    TokenService,
    _hash_token,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    ctx.admin_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.session.return_value.__aexit__ = AsyncMock(return_value=False)

    # run_in_transaction wraps a callable; mirror the real helper by invoking it
    # with the mock session so the write block still runs under test.
    async def _run_in_transaction(fn: object, **_kwargs: object) -> object:
        return await fn(mock_session)  # type: ignore[operator]

    ctx.admin_db.run_in_transaction = AsyncMock(side_effect=_run_in_transaction)
    ctx.config.auth.access_ttl_seconds = 3600
    ctx.config.auth.refresh_ttl_seconds = 604800
    return ctx


def _make_access_token_row(
    *,
    token_hash: str = "abc",
    actor_id: str = "usr_test123",
    actor_type: str = "user",
    scopes: list[str] | None = None,
    token_family_id: str = "tfam_test123",
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    is_ephemeral: bool = False,
) -> MagicMock:
    row = MagicMock()
    row.id = "at_test123"
    row.token_hash = token_hash
    row.actor_id = actor_id
    row.actor_type = actor_type
    row.scopes = scopes or ["read", "write"]
    row.token_family_id = token_family_id
    row.expires_at = expires_at or (datetime.now(UTC) + timedelta(hours=1))
    row.created_at = datetime.now(UTC)
    row.revoked_at = revoked_at
    row.is_ephemeral = is_ephemeral
    row.oauth_client_id = None
    row.oauth_grant_id = None
    return row


def _make_refresh_token_row(
    *,
    token_hash: str = "def",
    actor_id: str = "usr_test123",
    actor_type: str = "user",
    scopes: list[str] | None = None,
    token_family_id: str = "tfam_test123",
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    consumed_at: datetime | None = None,
    replaced_by_id: str | None = None,
) -> MagicMock:
    row = MagicMock()
    row.id = "rt_test123"
    row.token_hash = token_hash
    row.actor_id = actor_id
    row.actor_type = actor_type
    row.scopes = scopes or ["read", "write"]
    row.token_family_id = token_family_id
    row.expires_at = expires_at or (datetime.now(UTC) + timedelta(days=7))
    row.created_at = datetime.now(UTC)
    row.revoked_at = revoked_at
    row.consumed_at = consumed_at
    row.replaced_by_id = replaced_by_id
    row.oauth_client_id = None
    row.oauth_grant_id = None
    return row


def _make_agent_row(*, status: str = "active", owner_id: str = "usr_owner") -> MagicMock:
    row = MagicMock()
    row.id = "agnt_x"
    row.status = status
    row.owner_id = owner_id
    return row


def _make_user_row(*, active: bool = True) -> MagicMock:
    row = MagicMock()
    row.id = "usr_test123"
    row.active = active
    return row


def _make_service_account_row(*, status: str = "active") -> MagicMock:
    row = MagicMock()
    row.id = "sva_x"
    row.status = status
    return row


@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_issue_pair_returns_prefixed_tokens(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    mock_at_repo.create = AsyncMock(return_value=_make_access_token_row())
    mock_rt_repo.create = AsyncMock(return_value=_make_refresh_token_row())

    svc = TokenService(ctx)
    access, refresh = await svc.issue_pair("usr_abc", ActorType.USER, ["read"])

    assert access.startswith(ACCESS_TOKEN_PREFIX)
    assert refresh.startswith(REFRESH_TOKEN_PREFIX)


@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_issue_pair_stores_hashed_tokens(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    mock_at_repo.create = AsyncMock(return_value=_make_access_token_row())
    mock_rt_repo.create = AsyncMock(return_value=_make_refresh_token_row())

    svc = TokenService(ctx)
    access, refresh = await svc.issue_pair("usr_abc", ActorType.USER, ["read"])

    at_call_kwargs = mock_at_repo.create.call_args[1]
    rt_call_kwargs = mock_rt_repo.create.call_args[1]

    assert at_call_kwargs["token_hash"] == _hash_token(access)
    assert rt_call_kwargs["token_hash"] == _hash_token(refresh)
    assert at_call_kwargs["actor_id"] == "usr_abc"
    assert at_call_kwargs["actor_type"] == "user"
    assert at_call_kwargs["scopes"] == ["read"]


@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_refresh_rotation_returns_new_pair(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock, mock_user_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    rt_row = _make_refresh_token_row()
    mock_rt_repo.get_by_hash = AsyncMock(return_value=rt_row)
    mock_rt_repo.create = AsyncMock(return_value=_make_refresh_token_row())
    mock_rt_repo.consume = AsyncMock()
    mock_at_repo.create = AsyncMock(return_value=_make_access_token_row())
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row())

    svc = TokenService(ctx)
    access, refresh, _scopes = await svc.refresh("rt_oldtoken")

    assert access.startswith(ACCESS_TOKEN_PREFIX)
    assert refresh.startswith(REFRESH_TOKEN_PREFIX)
    mock_rt_repo.consume.assert_called_once()


@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_refresh_consumed_token_triggers_reuse_detection(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    rt_row = _make_refresh_token_row(consumed_at=datetime.now(UTC))
    mock_rt_repo.get_by_hash = AsyncMock(return_value=rt_row)
    mock_rt_repo.revoke_family = AsyncMock()
    mock_at_repo.revoke_family = AsyncMock()

    svc = TokenService(ctx)
    with pytest.raises(InvalidGrantError, match="reuse detected"):
        await svc.refresh("rt_consumed")

    mock_rt_repo.revoke_family.assert_called_once()
    mock_at_repo.revoke_family.assert_called_once()


@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_refresh_expired_token_raises_invalid_grant(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    rt_row = _make_refresh_token_row(expires_at=datetime.now(UTC) - timedelta(hours=1))
    mock_rt_repo.get_by_hash = AsyncMock(return_value=rt_row)

    svc = TokenService(ctx)
    with pytest.raises(InvalidGrantError, match="expired"):
        await svc.refresh("rt_expired")


@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_refresh_not_found_raises_invalid_grant(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    mock_rt_repo.get_by_hash = AsyncMock(return_value=None)

    svc = TokenService(ctx)
    with pytest.raises(InvalidGrantError, match="not found"):
        await svc.refresh("rt_bogus")


@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_revoke_access_token(mock_at_repo: MagicMock, mock_rt_repo: MagicMock) -> None:
    ctx = _make_ctx()
    at_row = _make_access_token_row()
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_at_repo.revoke = AsyncMock()

    svc = TokenService(ctx)
    await svc.revoke("at_sometoken", identity=Identity(sub="usr_test123", email="test@local"))

    mock_at_repo.revoke.assert_called_once()


@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_revoke_refresh_token_revokes_family(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    rt_row = _make_refresh_token_row()
    mock_rt_repo.get_by_hash = AsyncMock(return_value=rt_row)
    mock_rt_repo.revoke_family = AsyncMock()
    mock_at_repo.revoke_family = AsyncMock()

    svc = TokenService(ctx)
    await svc.revoke("rt_sometoken", identity=Identity(sub="usr_test123", email="test@local"))

    mock_rt_repo.revoke_family.assert_called_once()
    mock_at_repo.revoke_family.assert_called_once()


@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_revoke_not_found_is_noop(mock_at_repo: MagicMock, mock_rt_repo: MagicMock) -> None:
    ctx = _make_ctx()
    mock_at_repo.get_by_hash = AsyncMock(return_value=None)

    svc = TokenService(ctx)
    await svc.revoke("at_nonexistent", identity=Identity(sub="usr_test123", email="test@local"))

    mock_at_repo.revoke.assert_not_called()


@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_revoke_wrong_owner_is_noop(mock_at_repo: MagicMock, mock_rt_repo: MagicMock) -> None:
    ctx = _make_ctx()
    at_row = _make_access_token_row(actor_id="usr_owner")
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_at_repo.revoke = AsyncMock()

    svc = TokenService(ctx)
    await svc.revoke("at_sometoken", identity=Identity(sub="usr_other", email="test@local"))

    mock_at_repo.revoke.assert_not_called()


@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_revoke_correct_owner_succeeds(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    at_row = _make_access_token_row(actor_id="usr_owner")
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_at_repo.revoke = AsyncMock()

    svc = TokenService(ctx)
    await svc.revoke("at_sometoken", identity=Identity(sub="usr_owner", email="test@local"))

    mock_at_repo.revoke.assert_called_once()


@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_introspect_active_access_token(
    mock_at_repo: MagicMock, mock_user_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    at_row = _make_access_token_row()
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row())

    svc = TokenService(ctx)
    result = await svc.introspect("at_validtoken")

    assert result["active"] is True
    assert result["sub"] == at_row.actor_id
    assert result["token_type"] == "access_token"


@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_introspect_revoked_access_token(mock_at_repo: MagicMock) -> None:
    ctx = _make_ctx()
    at_row = _make_access_token_row(revoked_at=datetime.now(UTC))
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)

    svc = TokenService(ctx)
    result = await svc.introspect("at_revokedtoken")

    assert result["active"] is False


@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_introspect_expired_access_token(mock_at_repo: MagicMock) -> None:
    ctx = _make_ctx()
    at_row = _make_access_token_row(expires_at=datetime.now(UTC) - timedelta(hours=1))
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)

    svc = TokenService(ctx)
    result = await svc.introspect("at_expiredtoken")

    assert result["active"] is False


@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_introspect_not_found_returns_inactive(mock_at_repo: MagicMock) -> None:
    ctx = _make_ctx()
    mock_at_repo.get_by_hash = AsyncMock(return_value=None)

    svc = TokenService(ctx)
    result = await svc.introspect("at_bogus")

    assert result["active"] is False


@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_valid_token_returns_identity(
    mock_at_repo: MagicMock, mock_user_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    at_row = _make_access_token_row()
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row())

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_validtoken")

    assert resolved is not None
    assert resolved.active is True
    assert resolved.sub == at_row.actor_id
    assert resolved.permissions == at_row.scopes


@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_expired_token_returns_inactive(
    mock_at_repo: MagicMock, mock_user_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    at_row = _make_access_token_row(expires_at=datetime.now(UTC) - timedelta(hours=1))
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row())

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_expired")

    assert resolved is not None
    assert resolved.active is False


@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_not_found_returns_none(mock_at_repo: MagicMock) -> None:
    ctx = _make_ctx()
    mock_at_repo.get_by_hash = AsyncMock(return_value=None)

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_bogus")

    assert resolved is None


async def test_resolve_non_access_token_prefix_returns_none() -> None:
    ctx = _make_ctx()
    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("jwt_token_here")

    assert resolved is None


@patch("jentic_one.auth.services.token_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.token_service.AgentRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_long_lived_agent_token_uses_live_grants(
    mock_at_repo: MagicMock,
    mock_agent_repo: MagicMock,
    mock_grant_repo: MagicMock,
) -> None:
    """A long-lived agent token (is_ephemeral=False) resolves live grants,
    not the frozen snapshot — so scope edits take effect immediately."""
    ctx = _make_ctx()
    at_row = _make_access_token_row(
        actor_id="agnt_x", actor_type="agent", scopes=["apis:read"], is_ephemeral=False
    )
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_agent_repo.get_by_id = AsyncMock(return_value=_make_agent_row())
    mock_grant_repo.list_for_actor = AsyncMock(
        return_value=[MagicMock(scope="apis:read"), MagicMock(scope="apis:write")]
    )

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_agenttoken")

    assert resolved is not None
    assert resolved.active is True
    assert resolved.parent_actor_id == "usr_owner"
    assert resolved.permissions == ["apis:read", "apis:write"]
    mock_grant_repo.list_for_actor.assert_awaited_once()


@patch("jentic_one.auth.services.token_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.token_service.AgentRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_ephemeral_minted_token_keeps_snapshot(
    mock_at_repo: MagicMock,
    mock_agent_repo: MagicMock,
    mock_grant_repo: MagicMock,
) -> None:
    """An ephemeral minted agent token (is_ephemeral=True) keeps its downscoped
    snapshot and must NOT be re-broadened to the actor's full grants."""
    ctx = _make_ctx()
    at_row = _make_access_token_row(
        actor_id="agnt_x",
        actor_type="agent",
        scopes=["capabilities:execute"],
        is_ephemeral=True,
    )
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_agent_repo.get_by_id = AsyncMock(return_value=_make_agent_row())
    mock_grant_repo.list_for_actor = AsyncMock()

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_minted")

    assert resolved is not None
    assert resolved.permissions == ["capabilities:execute"]
    mock_grant_repo.list_for_actor.assert_not_awaited()


@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_user_token_keeps_snapshot(
    mock_at_repo: MagicMock,
    mock_grant_repo: MagicMock,
    mock_user_repo: MagicMock,
) -> None:
    """User tokens do not draw scopes from actor_scope_grants."""
    ctx = _make_ctx()
    at_row = _make_access_token_row(actor_id="usr_x", actor_type="user", scopes=["openid"])
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_grant_repo.list_for_actor = AsyncMock()
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row())

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_usertoken")

    assert resolved is not None
    assert resolved.permissions == ["openid"]
    mock_grant_repo.list_for_actor.assert_not_awaited()


# --- actor-status checks (#1136): disable must kill outstanding tokens ---


@pytest.mark.parametrize("status", ["pending", "rejected", "disabled", "archived"])
@patch("jentic_one.auth.services.token_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.token_service.AgentRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_non_active_agent_token_is_inactive(
    mock_at_repo: MagicMock,
    mock_agent_repo: MagicMock,
    mock_grant_repo: MagicMock,
    status: str,
) -> None:
    """A valid, unexpired token resolves as inactive once its agent leaves ACTIVE."""
    ctx = _make_ctx()
    at_row = _make_access_token_row(actor_id="agnt_x", actor_type="agent")
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_agent_repo.get_by_id = AsyncMock(return_value=_make_agent_row(status=status))
    mock_grant_repo.list_for_actor = AsyncMock(return_value=[])

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_agenttoken")

    assert resolved is not None
    assert resolved.active is False


@patch("jentic_one.auth.services.token_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.token_service.AgentRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_missing_agent_row_fails_closed(
    mock_at_repo: MagicMock,
    mock_agent_repo: MagicMock,
    mock_grant_repo: MagicMock,
) -> None:
    """An agent token whose agent row no longer exists must not stay active."""
    ctx = _make_ctx()
    at_row = _make_access_token_row(actor_id="agnt_gone", actor_type="agent")
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_agent_repo.get_by_id = AsyncMock(return_value=None)
    mock_grant_repo.list_for_actor = AsyncMock(return_value=[])

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_orphan")

    assert resolved is not None
    assert resolved.active is False


@patch("jentic_one.auth.services.token_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.token_service.ServiceAccountRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_disabled_service_account_token_is_inactive(
    mock_at_repo: MagicMock,
    mock_sa_repo: MagicMock,
    mock_grant_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    at_row = _make_access_token_row(actor_id="sva_x", actor_type="service_account")
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_sa_repo.get_by_id = AsyncMock(return_value=_make_service_account_row(status="disabled"))
    mock_grant_repo.list_for_actor = AsyncMock(return_value=[])

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_satoken")

    assert resolved is not None
    assert resolved.active is False


@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_deactivated_user_token_is_inactive(
    mock_at_repo: MagicMock, mock_user_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    at_row = _make_access_token_row(actor_id="usr_x", actor_type="user")
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row(active=False))

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_usertoken")

    assert resolved is not None
    assert resolved.active is False


@patch("jentic_one.auth.services.token_service.AgentRepository")
@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_refresh_disabled_agent_rejected_without_minting(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock, mock_agent_repo: MagicMock
) -> None:
    """A disabled agent must not rotate its refresh token into fresh access tokens."""
    ctx = _make_ctx()
    rt_row = _make_refresh_token_row(actor_id="agnt_x", actor_type="agent")
    mock_rt_repo.get_by_hash = AsyncMock(return_value=rt_row)
    mock_rt_repo.create = AsyncMock()
    mock_rt_repo.consume = AsyncMock()
    mock_at_repo.create = AsyncMock()
    mock_agent_repo.get_by_id = AsyncMock(return_value=_make_agent_row(status="disabled"))

    svc = TokenService(ctx)
    with pytest.raises(InvalidGrantError, match="not active"):
        await svc.refresh("rt_disabledagent")

    mock_at_repo.create.assert_not_called()
    mock_rt_repo.create.assert_not_called()
    mock_rt_repo.consume.assert_not_called()


@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_refresh_deactivated_user_rejected(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock, mock_user_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    rt_row = _make_refresh_token_row()
    mock_rt_repo.get_by_hash = AsyncMock(return_value=rt_row)
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row(active=False))

    svc = TokenService(ctx)
    with pytest.raises(InvalidGrantError, match="not active"):
        await svc.refresh("rt_deactivateduser")


@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_refresh_unapproved_issuing_client_rejected(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock, mock_client_repo: MagicMock
) -> None:
    """The client approval gate at refresh: a pending issuing client — even if force-set
    active — must not rotate tokens."""
    ctx = _make_ctx()
    rt_row = _make_refresh_token_row()
    rt_row.oauth_client_id = "oc_pending"
    mock_rt_repo.get_by_hash = AsyncMock(return_value=rt_row)
    mock_at_repo.create = AsyncMock()
    mock_rt_repo.create = AsyncMock()

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "pending"
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)

    svc = TokenService(ctx)
    with pytest.raises(InvalidGrantError, match="deactivated"):
        await svc.refresh("rt_pendingclient", client_id="oc_pending")

    mock_at_repo.create.assert_not_called()
    mock_rt_repo.create.assert_not_called()


@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_refresh_approved_client_still_rotates(
    mock_at_repo: MagicMock,
    mock_rt_repo: MagicMock,
    mock_client_repo: MagicMock,
    mock_user_repo: MagicMock,
) -> None:
    """An approved+active issuing client refreshes exactly as before."""
    ctx = _make_ctx()
    rt_row = _make_refresh_token_row()
    rt_row.oauth_client_id = "oc_approved"
    mock_rt_repo.get_by_hash = AsyncMock(return_value=rt_row)
    mock_rt_repo.create = AsyncMock(return_value=MagicMock(id="rt_next"))
    mock_rt_repo.consume = AsyncMock()
    mock_at_repo.create = AsyncMock()
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row())

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "approved"
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)

    svc = TokenService(ctx)
    access, refresh, _scopes = await svc.refresh("rt_ok", client_id="oc_approved")

    assert access.startswith(ACCESS_TOKEN_PREFIX)
    assert refresh.startswith(REFRESH_TOKEN_PREFIX)


@patch("jentic_one.auth.services.token_service.AgentRepository")
@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_refresh_reuse_detection_wins_over_disabled_actor(
    mock_at_repo: MagicMock, mock_rt_repo: MagicMock, mock_agent_repo: MagicMock
) -> None:
    """Replaying a consumed refresh token still revokes the family even when the
    actor is disabled — reuse detection must not be short-circuited."""
    ctx = _make_ctx()
    rt_row = _make_refresh_token_row(
        actor_id="agnt_x", actor_type="agent", consumed_at=datetime.now(UTC)
    )
    mock_rt_repo.get_by_hash = AsyncMock(return_value=rt_row)
    mock_rt_repo.revoke_family = AsyncMock()
    mock_at_repo.revoke_family = AsyncMock()
    mock_agent_repo.get_by_id = AsyncMock(return_value=_make_agent_row(status="disabled"))

    svc = TokenService(ctx)
    with pytest.raises(InvalidGrantError, match="reuse detected"):
        await svc.refresh("rt_replayed")

    mock_rt_repo.revoke_family.assert_called_once()
    mock_at_repo.revoke_family.assert_called_once()


@patch("jentic_one.auth.services.token_service.AgentRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_introspect_disabled_agent_access_token_inactive(
    mock_at_repo: MagicMock, mock_agent_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    at_row = _make_access_token_row(actor_id="agnt_x", actor_type="agent")
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_agent_repo.get_by_id = AsyncMock(return_value=_make_agent_row(status="disabled"))

    svc = TokenService(ctx)
    result = await svc.introspect("at_disabledagent")

    assert result["active"] is False


@patch("jentic_one.auth.services.token_service.AgentRepository")
@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
async def test_introspect_disabled_agent_refresh_token_inactive(
    mock_rt_repo: MagicMock, mock_agent_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    rt_row = _make_refresh_token_row(actor_id="agnt_x", actor_type="agent")
    mock_rt_repo.get_by_hash = AsyncMock(return_value=rt_row)
    mock_agent_repo.get_by_id = AsyncMock(return_value=_make_agent_row(status="disabled"))

    svc = TokenService(ctx)
    result = await svc.introspect("rt_disabledagent")

    assert result["active"] is False


# ---------- scope intersection for third-party tokens ----------


@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_intersects_scopes_with_client_allowlist(
    mock_at_repo: MagicMock,
    mock_client_repo: MagicMock,
) -> None:
    """Third-party tokens have scopes intersected with the client's allowed_scopes."""
    ctx = _make_ctx()
    at_row = _make_access_token_row(scopes=["agents:read", "agents:write", "openid"])
    at_row.oauth_client_id = "oc_ext_123"
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "approved"
    client_row.allowed_scopes = ["agents:read", "openid"]
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_thirdparty")

    assert resolved is not None
    assert set(resolved.permissions) == {"agents:read", "openid"}


@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_empty_allowed_scopes_denies_all(
    mock_at_repo: MagicMock,
    mock_client_repo: MagicMock,
) -> None:
    """An empty allowed_scopes list means the client has no permitted scopes."""
    ctx = _make_ctx()
    at_row = _make_access_token_row(scopes=["agents:read", "openid"])
    at_row.oauth_client_id = "oc_ext_456"
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "approved"
    client_row.allowed_scopes = []
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_empty_scopes")

    assert resolved is not None
    assert resolved.permissions == []


@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_null_allowed_scopes_is_unrestricted(
    mock_at_repo: MagicMock,
    mock_client_repo: MagicMock,
) -> None:
    """A client with allowed_scopes=None does not restrict the token's scopes."""
    ctx = _make_ctx()
    at_row = _make_access_token_row(scopes=["agents:read", "agents:write"])
    at_row.oauth_client_id = "oc_ext_789"
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "approved"
    client_row.allowed_scopes = None
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_unrestricted")

    assert resolved is not None
    assert set(resolved.permissions) == {"agents:read", "agents:write"}


# ---------- client approval gate at the live resolvers (MAJOR-1, PR #1218 review) ----------


@pytest.mark.parametrize("approval_status", ["pending", "denied"])
@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_unapproved_client_fails_closed_even_if_active(
    mock_at_repo: MagicMock,
    mock_client_repo: MagicMock,
    approval_status: str,
) -> None:
    """A token minted while the client was approved must stop resolving once
    the row is denied/pending — even if ``active`` is somehow force-set true
    (the deny→PATCH-active pincer from the #1218 review)."""
    ctx = _make_ctx()
    at_row = _make_access_token_row()
    at_row.oauth_client_id = "oc_gated"
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = approval_status
    client_row.allowed_scopes = None
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_gated")

    assert resolved is None


@pytest.mark.parametrize("approval_status", ["pending", "denied"])
@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_introspect_unapproved_client_access_token_inactive(
    mock_at_repo: MagicMock,
    mock_client_repo: MagicMock,
    mock_user_repo: MagicMock,
    approval_status: str,
) -> None:
    """Introspection shares the resolver's approval-gate verdict via _resolve_client_gate."""
    ctx = _make_ctx()
    at_row = _make_access_token_row()
    at_row.oauth_client_id = "oc_gated"
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row())

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = approval_status
    client_row.allowed_scopes = None
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)

    svc = TokenService(ctx)
    result = await svc.introspect("at_gated")

    assert result["active"] is False


@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
async def test_introspect_unapproved_client_refresh_token_inactive(
    mock_rt_repo: MagicMock,
    mock_client_repo: MagicMock,
    mock_user_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    rt_row = _make_refresh_token_row()
    rt_row.oauth_client_id = "oc_gated"
    mock_rt_repo.get_by_hash = AsyncMock(return_value=rt_row)
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row())

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "denied"
    client_row.allowed_scopes = None
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)

    svc = TokenService(ctx)
    result = await svc.introspect("rt_gated")

    assert result["active"] is False


@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_approved_active_client_still_resolves(
    mock_at_repo: MagicMock,
    mock_client_repo: MagicMock,
    mock_user_repo: MagicMock,
) -> None:
    """The gate change must not break the approved+active happy path."""
    ctx = _make_ctx()
    at_row = _make_access_token_row()
    at_row.oauth_client_id = "oc_ok"
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row())

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "approved"
    client_row.allowed_scopes = None
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_ok")

    assert resolved is not None
    assert resolved.active is True


# --- grant-channel gates ----------------------------------------------------


def _make_grant_row(*, status: str = "active", scopes: list[str] | None = None) -> MagicMock:
    row = MagicMock()
    row.id = "ocg_test123"
    row.status = status
    row.scopes = scopes if scopes is not None else ["apis:read"]
    return row


@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientGrantRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_revoked_grant_fails_closed(
    mock_at_repo: MagicMock,
    mock_client_repo: MagicMock,
    mock_grant_repo: MagicMock,
    mock_user_repo: MagicMock,
) -> None:
    """A grant-channel token whose grant is revoked must not resolve."""
    ctx = _make_ctx()
    at_row = _make_access_token_row()
    at_row.oauth_client_id = "oc_grant_app"
    at_row.oauth_grant_id = "ocg_test123"
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row())

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "approved"
    client_row.allowed_scopes = None
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)
    mock_grant_repo.get_by_id = AsyncMock(return_value=_make_grant_row(status="revoked"))

    svc = TokenService(ctx)
    assert await svc.resolve_access_token("at_grant_revoked") is None


@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientGrantRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_missing_grant_row_fails_closed(
    mock_at_repo: MagicMock,
    mock_client_repo: MagicMock,
    mock_grant_repo: MagicMock,
    mock_user_repo: MagicMock,
) -> None:
    """A grant-channel token whose grant row is gone must not resolve."""
    ctx = _make_ctx()
    at_row = _make_access_token_row()
    at_row.oauth_client_id = "oc_grant_app"
    at_row.oauth_grant_id = "ocg_gone"
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row())

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "approved"
    client_row.allowed_scopes = None
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)
    mock_grant_repo.get_by_id = AsyncMock(return_value=None)

    svc = TokenService(ctx)
    assert await svc.resolve_access_token("at_grant_missing") is None


@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientGrantRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_resolve_active_grant_intersects_scopes(
    mock_at_repo: MagicMock,
    mock_client_repo: MagicMock,
    mock_grant_repo: MagicMock,
    mock_user_repo: MagicMock,
) -> None:
    """Grant scopes are the fourth intersection leg: token scopes outside the
    grant set are dropped, and the Identity carries oauth_grant_id."""
    ctx = _make_ctx()
    at_row = _make_access_token_row(scopes=["apis:read", "apis:write"])
    at_row.oauth_client_id = "oc_grant_app"
    at_row.oauth_grant_id = "ocg_test123"
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row())

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "approved"
    client_row.allowed_scopes = None
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)
    mock_grant_repo.get_by_id = AsyncMock(return_value=_make_grant_row(scopes=["apis:read"]))

    svc = TokenService(ctx)
    resolved = await svc.resolve_access_token("at_grant_ok")

    assert resolved is not None
    assert resolved.active is True
    assert resolved.permissions == ["apis:read"]
    assert resolved.oauth_grant_id == "ocg_test123"


@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientGrantRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_introspect_folds_grant_gate_into_active(
    mock_at_repo: MagicMock,
    mock_client_repo: MagicMock,
    mock_grant_repo: MagicMock,
    mock_user_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    at_row = _make_access_token_row()
    at_row.oauth_client_id = "oc_grant_app"
    at_row.oauth_grant_id = "ocg_test123"
    mock_at_repo.get_by_hash = AsyncMock(return_value=at_row)
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row())

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "approved"
    client_row.allowed_scopes = None
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)
    mock_grant_repo.get_by_id = AsyncMock(return_value=_make_grant_row(status="revoked"))

    svc = TokenService(ctx)
    result = await svc.introspect("at_grant_introspect")
    assert result["active"] is False


@patch("jentic_one.auth.services.token_service.UserRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientGrantRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
async def test_introspect_folds_grant_gate_into_active_for_refresh_token(
    mock_rt_repo: MagicMock,
    mock_client_repo: MagicMock,
    mock_grant_repo: MagicMock,
    mock_user_repo: MagicMock,
) -> None:
    """The grant gate applies to refresh-token introspection too (review F-4)."""
    ctx = _make_ctx()
    rt_row = _make_refresh_token_row()
    rt_row.oauth_client_id = "oc_grant_app"
    rt_row.oauth_grant_id = "ocg_test123"
    mock_rt_repo.get_by_hash = AsyncMock(return_value=rt_row)
    mock_user_repo.get_by_id = AsyncMock(return_value=_make_user_row())

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "approved"
    client_row.allowed_scopes = None
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)
    mock_grant_repo.get_by_id = AsyncMock(return_value=_make_grant_row(status="revoked"))

    svc = TokenService(ctx)
    result = await svc.introspect("rt_grant_introspect")
    assert result["active"] is False


@patch("jentic_one.auth.services.token_service.record_audit")
@patch("jentic_one.auth.services.token_service.OAuthClientGrantRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
@patch("jentic_one.auth.services.token_service.AccessTokenRepository")
async def test_refresh_reuse_detection_wins_over_revoked_grant(
    mock_at_repo: MagicMock,
    mock_rt_repo: MagicMock,
    mock_client_repo: MagicMock,
    mock_grant_repo: MagicMock,
    mock_record_audit: AsyncMock,
) -> None:
    """Review A8: a replayed consumed RT under a revoked grant still triggers
    the family sweep and the "reuse detected" audit row — the grant gate must
    not short-circuit the reuse telemetry."""
    ctx = _make_ctx()
    rt_row = _make_refresh_token_row(consumed_at=datetime.now(UTC))
    rt_row.oauth_client_id = "oc_grant_app"
    rt_row.oauth_grant_id = "ocg_test123"
    mock_rt_repo.get_by_hash = AsyncMock(return_value=rt_row)
    mock_rt_repo.revoke_family = AsyncMock()
    mock_at_repo.revoke_family = AsyncMock()

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "approved"
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)
    mock_grant_repo.get_by_id = AsyncMock(return_value=_make_grant_row(status="revoked"))

    svc = TokenService(ctx)
    with pytest.raises(InvalidGrantError, match="reuse detected"):
        await svc.refresh("rt_replayed_revoked_grant", client_id="oc_grant_app")

    mock_rt_repo.revoke_family.assert_called_once()
    mock_at_repo.revoke_family.assert_called_once()
    audit_reasons = [call.kwargs.get("reason") for call in mock_record_audit.await_args_list]
    assert "refresh token reuse detected" in audit_reasons


@patch("jentic_one.auth.services.token_service.OAuthClientGrantRepository")
@patch("jentic_one.auth.services.token_service.OAuthClientRepository")
@patch("jentic_one.auth.services.token_service.RefreshTokenRepository")
async def test_refresh_rechecks_grant_status(
    mock_rt_repo: MagicMock,
    mock_client_repo: MagicMock,
    mock_grant_repo: MagicMock,
) -> None:
    """Rotation re-checks the grant on every refresh — revoked → fail
    closed without consuming the token or minting."""
    ctx = _make_ctx()
    rt_row = _make_refresh_token_row()
    rt_row.oauth_client_id = "oc_grant_app"
    rt_row.oauth_grant_id = "ocg_test123"
    mock_rt_repo.get_by_hash = AsyncMock(return_value=rt_row)
    mock_rt_repo.consume = AsyncMock()

    client_row = MagicMock()
    client_row.active = True
    client_row.approval_status = "approved"
    mock_client_repo.get_by_client_id = AsyncMock(return_value=client_row)
    mock_grant_repo.get_by_id = AsyncMock(return_value=_make_grant_row(status="revoked"))

    svc = TokenService(ctx)
    with pytest.raises(InvalidGrantError, match="revoked"):
        await svc.refresh("rt_grant_revoked", client_id="oc_grant_app")
    mock_rt_repo.consume.assert_not_awaited()
