"""Unit tests for service account scope management (create with scopes, get, replace)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.auth.services.errors import ActorNotFoundError, InvalidTransitionError
from jentic_one.auth.services.schemas.service_accounts import ServiceAccountCreatePayload
from jentic_one.auth.services.service_account_service import ServiceAccountService
from jentic_one.shared.auth.identity import Identity


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
        permissions=["service-accounts:write", "service-accounts:read", "org:admin"],
    )


def _mock_sa(sa_id: str = "sa_test1", status: str = "active") -> MagicMock:
    sa = MagicMock()
    sa.id = sa_id
    sa.name = "test-sa"
    sa.description = None
    sa.owner_id = "usr_owner1"
    sa.registered_by = "usr_owner1"
    sa.approved_by = "usr_owner1"
    sa.status = status
    sa.denial_reason = None
    sa.denied_by = None
    # Theme-8 Phase 1: unstamped by default — the stamp guard must not trip.
    sa.migrated_to_actor_id = None
    sa.migrated_at = None
    sa.created_at = datetime(2026, 6, 23, tzinfo=UTC)
    sa.approved_at = datetime(2026, 6, 23, tzinfo=UTC)
    return sa


@patch("jentic_one.auth.services.service_account_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.service_account_service.ServiceAccountRepository")
async def test_create_sa_with_scopes(mock_sa_repo: MagicMock, mock_scope_repo: MagicMock) -> None:
    ctx = _make_ctx()
    sa = _mock_sa()
    mock_sa_repo.create = AsyncMock(return_value=sa)
    mock_sa_repo.set_approval = AsyncMock(return_value=sa)
    mock_scope_repo.grant = AsyncMock()

    svc = ServiceAccountService(ctx)
    payload = ServiceAccountCreatePayload(
        name="test-sa", scopes=["capabilities:execute", "agents:read"]
    )
    await svc.create(payload, owner_id="usr_owner1", identity=_identity())

    assert mock_scope_repo.grant.call_count == 2
    calls = mock_scope_repo.grant.call_args_list
    assert calls[0].kwargs["scope"] == "capabilities:execute"
    assert calls[0].kwargs["actor_type"] == "service_account"
    assert calls[0].kwargs["actor_id"] == "sa_test1"
    assert calls[1].kwargs["scope"] == "agents:read"


@patch("jentic_one.auth.services.service_account_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.service_account_service.ServiceAccountRepository")
async def test_create_sa_without_scopes(
    mock_sa_repo: MagicMock, mock_scope_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    sa = _mock_sa()
    mock_sa_repo.create = AsyncMock(return_value=sa)
    mock_sa_repo.set_approval = AsyncMock(return_value=sa)

    svc = ServiceAccountService(ctx)
    payload = ServiceAccountCreatePayload(name="test-sa")
    await svc.create(payload, owner_id="usr_owner1", identity=_identity())

    mock_scope_repo.grant.assert_not_called()


@patch("jentic_one.auth.services.service_account_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.service_account_service.ServiceAccountRepository")
async def test_get_scopes(mock_sa_repo: MagicMock, mock_scope_repo: MagicMock) -> None:
    ctx = _make_ctx()
    sa = _mock_sa()
    mock_sa_repo.get_by_id = AsyncMock(return_value=sa)

    grant1 = MagicMock()
    grant1.scope = "capabilities:execute"
    grant2 = MagicMock()
    grant2.scope = "agents:read"
    mock_scope_repo.list_for_actor = AsyncMock(return_value=[grant1, grant2])

    svc = ServiceAccountService(ctx)
    scopes = await svc.get_scopes("sa_test1", identity=_identity())

    assert scopes == ["capabilities:execute", "agents:read"]
    mock_scope_repo.list_for_actor.assert_called_once()


@patch("jentic_one.auth.services.service_account_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.service_account_service.ServiceAccountRepository")
async def test_replace_scopes(mock_sa_repo: MagicMock, mock_scope_repo: MagicMock) -> None:
    ctx = _make_ctx()
    sa = _mock_sa()
    mock_sa_repo.get_by_id_for_update = AsyncMock(return_value=sa)
    mock_scope_repo.revoke_all = AsyncMock(return_value=2)
    mock_scope_repo.grant = AsyncMock()

    svc = ServiceAccountService(ctx)
    result = await svc.replace_scopes("sa_test1", ["new:scope"], identity=_identity())

    assert result == ["new:scope"]
    mock_scope_repo.revoke_all.assert_called_once()
    mock_scope_repo.grant.assert_called_once()
    assert mock_scope_repo.grant.call_args.kwargs["scope"] == "new:scope"


@patch("jentic_one.auth.services.service_account_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.service_account_service.ServiceAccountRepository")
async def test_replace_scopes_empty_clears_all(
    mock_sa_repo: MagicMock, mock_scope_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    sa = _mock_sa()
    mock_sa_repo.get_by_id_for_update = AsyncMock(return_value=sa)
    mock_scope_repo.revoke_all = AsyncMock(return_value=2)

    svc = ServiceAccountService(ctx)
    result = await svc.replace_scopes("sa_test1", [], identity=_identity())

    assert result == []
    mock_scope_repo.revoke_all.assert_called_once()
    mock_scope_repo.grant.assert_not_called()


@patch("jentic_one.auth.services.service_account_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.service_account_service.ServiceAccountRepository")
async def test_replace_scopes_not_found(
    mock_sa_repo: MagicMock, mock_scope_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    mock_sa_repo.get_by_id_for_update = AsyncMock(return_value=None)

    svc = ServiceAccountService(ctx)
    with pytest.raises(ActorNotFoundError):
        await svc.replace_scopes("sa_missing", ["x"], identity=_identity())


@patch("jentic_one.auth.services.service_account_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.service_account_service.ServiceAccountRepository")
async def test_replace_scopes_archived_raises(
    mock_sa_repo: MagicMock, mock_scope_repo: MagicMock
) -> None:
    ctx = _make_ctx()
    sa = _mock_sa(status="archived")
    mock_sa_repo.get_by_id_for_update = AsyncMock(return_value=sa)

    svc = ServiceAccountService(ctx)
    with pytest.raises(InvalidTransitionError):
        await svc.replace_scopes("sa_test1", ["x"], identity=_identity())
