"""Unit tests for ServiceAccountAuthService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.auth.services.crypto import generate_client_secret, hash_secret
from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    InvalidGrantError,
    InvalidTransitionError,
)
from jentic_one.auth.services.service_account_auth_service import ServiceAccountAuthService
from jentic_one.shared.auth.identity import Identity


def _session_mock() -> AsyncMock:
    """An AsyncSession stand-in whose synchronous ``add`` stays synchronous.

    ``AsyncSession.add`` is a plain (non-coroutine) method; left as the default
    ``AsyncMock`` child, ``session.add(...)`` would return an un-awaited
    coroutine and emit ``RuntimeWarning`` (here via the best-effort audit write).
    """
    session = AsyncMock()
    session.add = MagicMock()
    return session


def _admin_identity() -> Identity:
    return Identity(
        sub="usr_admin",
        email="admin@example.com",
        permissions=["service-accounts:write", "org:admin"],
    )


def _owner_identity(sub: str = "usr_owner") -> Identity:
    return Identity(
        sub=sub,
        email="owner@example.com",
        permissions=["service-accounts:write"],
    )


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = _session_mock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    ctx.admin_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.session.return_value.__aexit__ = AsyncMock(return_value=False)
    ctx.config.auth.access_ttl_seconds = 3600
    ctx.config.auth.refresh_ttl_seconds = 604800
    return ctx


def _make_sa_row(*, status: str = "active", owner_id: str = "usr_owner") -> MagicMock:
    row = MagicMock()
    row.id = "sva_test123"
    row.status = status
    row.owner_id = owner_id
    return row


def _make_credential_row(*, client_secret_hash: str | None = None) -> MagicMock:
    row = MagicMock()
    row.client_secret_hash = client_secret_hash
    row.api_key_hash = None
    return row


def _make_scope_grant(scope: str) -> MagicMock:
    grant = MagicMock()
    grant.scope = scope
    return grant


@pytest.mark.asyncio
@patch("jentic_one.auth.services.service_account_auth_service.ActorScopeGrantRepository")
@patch("jentic_one.auth.services.service_account_auth_service.ServiceAccountCredentialRepository")
@patch("jentic_one.auth.services.service_account_auth_service.ServiceAccountRepository")
async def test_authenticate_client_credentials_success(
    mock_sa_repo: MagicMock,
    mock_cred_repo: MagicMock,
    mock_scope_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)

    secret = generate_client_secret()
    secret_hash = hash_secret(secret)

    mock_sa_repo.get_by_id = AsyncMock(return_value=_make_sa_row(status="active"))
    mock_cred_repo.get_by_service_account_id = AsyncMock(
        return_value=_make_credential_row(client_secret_hash=secret_hash)
    )
    mock_scope_repo.list_for_actor = AsyncMock(
        return_value=[
            _make_scope_grant("capabilities:execute"),
            _make_scope_grant("capabilities:read"),
        ]
    )

    with patch.object(svc._token_svc, "issue_pair", new_callable=AsyncMock) as mock_issue:
        mock_issue.return_value = ("at_new", "rt_new")
        access, refresh, scopes = await svc.authenticate_client_credentials("sva_test123", secret)

    assert access == "at_new"
    assert refresh == "rt_new"
    assert scopes == ["capabilities:execute", "capabilities:read"]
    mock_issue.assert_called_once_with(
        "sva_test123", "service_account", ["capabilities:execute", "capabilities:read"]
    )


@pytest.mark.asyncio
@patch("jentic_one.auth.services.service_account_auth_service.ServiceAccountCredentialRepository")
@patch("jentic_one.auth.services.service_account_auth_service.ServiceAccountRepository")
async def test_authenticate_client_credentials_invalid_secret(
    mock_sa_repo: MagicMock,
    mock_cred_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)

    mock_sa_repo.get_by_id = AsyncMock(return_value=_make_sa_row(status="active"))
    mock_cred_repo.get_by_service_account_id = AsyncMock(
        return_value=_make_credential_row(client_secret_hash="different_hash")
    )

    with pytest.raises(InvalidGrantError, match="invalid_client"):
        await svc.authenticate_client_credentials("sva_test123", "jcs_wrong_secret")


@pytest.mark.asyncio
@patch("jentic_one.auth.services.service_account_auth_service.ServiceAccountRepository")
async def test_authenticate_client_credentials_sa_not_found(
    mock_sa_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)

    mock_sa_repo.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(InvalidGrantError, match="invalid_client"):
        await svc.authenticate_client_credentials("sva_nonexist", "jcs_anything")


@pytest.mark.asyncio
@patch("jentic_one.auth.services.service_account_auth_service.ServiceAccountRepository")
async def test_authenticate_client_credentials_sa_not_active(
    mock_sa_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)

    mock_sa_repo.get_by_id = AsyncMock(return_value=_make_sa_row(status="disabled"))

    with pytest.raises(InvalidGrantError, match="invalid_client"):
        await svc.authenticate_client_credentials("sva_test123", "jcs_anything")


def _make_agent_row(*, status: str = "active") -> MagicMock:
    row = MagicMock()
    row.id = "agnt_task1"
    row.status = status
    return row


@pytest.mark.asyncio
@patch("jentic_one.auth.services.service_account_auth_service.AgentRepository")
async def test_mint_task_token_success(mock_agent_repo: MagicMock) -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)
    mock_agent_repo.get_by_id = AsyncMock(return_value=_make_agent_row())

    with patch.object(svc._token_svc, "issue_access_only", new_callable=AsyncMock) as mock_issue:
        mock_issue.return_value = "at_ephemeral"
        token = await svc.mint_task_token(
            host_sa_id="sva_host",
            host_sa_scopes=["capabilities:execute", "capabilities:read"],
            requested_scopes=["capabilities:execute"],
            target_agent_id="agnt_task1",
            ttl_seconds=120,
        )

    assert token == "at_ephemeral"
    mock_issue.assert_called_once_with(
        "agnt_task1", "agent", ["capabilities:execute"], ttl_seconds=120
    )


@pytest.mark.asyncio
async def test_mint_task_token_rejects_superset_scopes() -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)

    with pytest.raises(InvalidGrantError, match="invalid_scope"):
        await svc.mint_task_token(
            host_sa_id="sva_host",
            host_sa_scopes=["capabilities:read"],
            requested_scopes=["capabilities:execute", "capabilities:read"],
            target_agent_id="agnt_task1",
        )


@pytest.mark.asyncio
async def test_mint_task_token_rejects_empty_scopes() -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)

    with pytest.raises(InvalidGrantError, match="invalid_scope"):
        await svc.mint_task_token(
            host_sa_id="sva_host",
            host_sa_scopes=["capabilities:execute"],
            requested_scopes=[],
            target_agent_id="agnt_task1",
        )


@pytest.mark.asyncio
@patch("jentic_one.auth.services.service_account_auth_service.AgentRepository")
async def test_mint_task_token_default_ttl(mock_agent_repo: MagicMock) -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)
    mock_agent_repo.get_by_id = AsyncMock(return_value=_make_agent_row())

    with patch.object(svc._token_svc, "issue_access_only", new_callable=AsyncMock) as mock_issue:
        mock_issue.return_value = "at_ephemeral"
        await svc.mint_task_token(
            host_sa_id="sva_host",
            host_sa_scopes=["capabilities:execute"],
            requested_scopes=["capabilities:execute"],
            target_agent_id="agnt_task1",
        )

    mock_issue.assert_called_once_with(
        "agnt_task1", "agent", ["capabilities:execute"], ttl_seconds=300
    )


@pytest.mark.asyncio
async def test_mint_task_token_rejects_ttl_too_large() -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)

    with pytest.raises(InvalidGrantError, match="ttl_seconds must be between"):
        await svc.mint_task_token(
            host_sa_id="sva_host",
            host_sa_scopes=["capabilities:execute"],
            requested_scopes=["capabilities:execute"],
            target_agent_id="agnt_task1",
            ttl_seconds=7200,
        )


@pytest.mark.asyncio
async def test_mint_task_token_rejects_ttl_zero() -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)

    with pytest.raises(InvalidGrantError, match="ttl_seconds must be between"):
        await svc.mint_task_token(
            host_sa_id="sva_host",
            host_sa_scopes=["capabilities:execute"],
            requested_scopes=["capabilities:execute"],
            target_agent_id="agnt_task1",
            ttl_seconds=0,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "rejected", "disabled", "archived"])
@patch("jentic_one.auth.services.service_account_auth_service.AgentRepository")
async def test_mint_task_token_rejects_non_active_target_agent(
    mock_agent_repo: MagicMock, status: str
) -> None:
    """Resolvers reject non-active agents' tokens (#1136), so minting one would
    be dead on arrival — the mint must fail fast instead of returning it."""
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)
    mock_agent_repo.get_by_id = AsyncMock(return_value=_make_agent_row(status=status))

    with (
        patch.object(svc._token_svc, "issue_access_only", new_callable=AsyncMock) as mock_issue,
        pytest.raises(InvalidGrantError, match="active agent"),
    ):
        await svc.mint_task_token(
            host_sa_id="sva_host",
            host_sa_scopes=["capabilities:execute"],
            requested_scopes=["capabilities:execute"],
            target_agent_id="agnt_task1",
        )

    mock_issue.assert_not_called()


@pytest.mark.asyncio
@patch("jentic_one.auth.services.service_account_auth_service.AgentRepository")
async def test_mint_task_token_rejects_missing_target_agent(
    mock_agent_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)
    mock_agent_repo.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(InvalidGrantError, match="active agent"):
        await svc.mint_task_token(
            host_sa_id="sva_host",
            host_sa_scopes=["capabilities:execute"],
            requested_scopes=["capabilities:execute"],
            target_agent_id="agnt_never_existed",
        )


# ---------------------------------------------------------------------------
# register_client_secret
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("jentic_one.auth.services.service_account_auth_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.auth.services.service_account_auth_service.ServiceAccountCredentialRepository")
@patch("jentic_one.auth.services.service_account_auth_service.ServiceAccountRepository")
async def test_register_api_key_success(
    mock_sa_repo: MagicMock,
    mock_cred_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)

    mock_sa_repo.get_by_id_for_update = AsyncMock(
        return_value=_make_sa_row(status="active", owner_id="usr_owner")
    )
    mock_cred_repo.set_api_key_hash = AsyncMock()

    key = await svc.register_api_key("sva_test123", identity=_owner_identity())

    assert key.startswith("sak_")
    mock_cred_repo.set_api_key_hash.assert_called_once()
    mock_audit.assert_called_once()


@pytest.mark.asyncio
@patch("jentic_one.auth.services.service_account_auth_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.auth.services.service_account_auth_service.ServiceAccountCredentialRepository")
@patch("jentic_one.auth.services.service_account_auth_service.ServiceAccountRepository")
async def test_register_client_secret_success(
    mock_sa_repo: MagicMock,
    mock_cred_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)

    mock_sa_repo.get_by_id_for_update = AsyncMock(
        return_value=_make_sa_row(status="active", owner_id="usr_owner")
    )
    mock_cred_repo.set_client_secret_hash = AsyncMock()

    secret = await svc.register_client_secret("sva_test123", identity=_owner_identity())

    assert secret.startswith("jcs_")
    mock_cred_repo.set_client_secret_hash.assert_called_once()
    mock_audit.assert_called_once()


@pytest.mark.asyncio
@patch("jentic_one.auth.services.service_account_auth_service.ServiceAccountRepository")
async def test_register_client_secret_not_found(
    mock_sa_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)

    mock_sa_repo.get_by_id_for_update = AsyncMock(return_value=None)

    with pytest.raises(ActorNotFoundError):
        await svc.register_client_secret("sva_nonexist", identity=_admin_identity())


@pytest.mark.asyncio
@patch("jentic_one.auth.services.service_account_auth_service.ServiceAccountRepository")
async def test_register_client_secret_not_active(
    mock_sa_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)

    mock_sa_repo.get_by_id_for_update = AsyncMock(return_value=_make_sa_row(status="disabled"))

    with pytest.raises(InvalidTransitionError):
        await svc.register_client_secret("sva_test123", identity=_admin_identity())


@pytest.mark.asyncio
@patch("jentic_one.auth.services.service_account_auth_service.ServiceAccountRepository")
async def test_register_client_secret_not_owner(
    mock_sa_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)

    mock_sa_repo.get_by_id_for_update = AsyncMock(
        return_value=_make_sa_row(status="active", owner_id="usr_someone_else")
    )

    with pytest.raises(ActorNotFoundError):
        await svc.register_client_secret(
            "sva_test123", identity=_owner_identity(sub="usr_notowner")
        )


@pytest.mark.asyncio
@patch("jentic_one.auth.services.service_account_auth_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.auth.services.service_account_auth_service.ServiceAccountCredentialRepository")
@patch("jentic_one.auth.services.service_account_auth_service.ServiceAccountRepository")
async def test_register_client_secret_admin_bypass(
    mock_sa_repo: MagicMock,
    mock_cred_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    svc = ServiceAccountAuthService(ctx)

    mock_sa_repo.get_by_id_for_update = AsyncMock(
        return_value=_make_sa_row(status="active", owner_id="usr_someone_else")
    )
    mock_cred_repo.set_client_secret_hash = AsyncMock()

    secret = await svc.register_client_secret("sva_test123", identity=_admin_identity())

    assert secret.startswith("jcs_")
    mock_cred_repo.set_client_secret_hash.assert_called_once()
