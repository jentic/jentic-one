"""Unit tests for OAuth client service."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.admin.services.errors import InvalidInputError, OAuthClientNotFoundError
from jentic_one.admin.services.oauth_client_service import (
    OAuthClientService,
    _validate_redirect_uris,
)


def test_accepts_valid_https_uris() -> None:
    _validate_redirect_uris(["https://example.com/callback"])


def test_accepts_multiple_https_uris() -> None:
    _validate_redirect_uris(
        [
            "https://example.com/callback",
            "https://app.example.org/oauth/redirect",
        ]
    )


def test_accepts_http_localhost() -> None:
    _validate_redirect_uris(["http://localhost:3000/callback"])


def test_accepts_http_127_0_0_1() -> None:
    _validate_redirect_uris(["http://127.0.0.1:8080/cb"])


def test_rejects_http_non_localhost() -> None:
    with pytest.raises(InvalidInputError, match="http redirect_uri only allowed for localhost"):
        _validate_redirect_uris(["http://example.com/callback"])


def test_rejects_http_remote_host() -> None:
    with pytest.raises(InvalidInputError, match="http redirect_uri only allowed for localhost"):
        _validate_redirect_uris(["http://192.168.1.1/callback"])


def test_rejects_empty_list() -> None:
    with pytest.raises(InvalidInputError, match="at least one redirect_uri is required"):
        _validate_redirect_uris([])


def test_rejects_uri_without_scheme() -> None:
    with pytest.raises(InvalidInputError, match="invalid redirect_uri"):
        _validate_redirect_uris(["example.com/callback"])


def test_rejects_uri_without_netloc() -> None:
    with pytest.raises(InvalidInputError, match="invalid redirect_uri"):
        _validate_redirect_uris(["https://"])


def test_rejects_ftp_scheme() -> None:
    with pytest.raises(InvalidInputError, match="redirect_uri must use https or http"):
        _validate_redirect_uris(["ftp://example.com/callback"])


def test_rejects_javascript_scheme() -> None:
    with pytest.raises(InvalidInputError, match="invalid redirect_uri"):
        _validate_redirect_uris(["javascript:alert(1)"])


# ---------- verify_client_secret ----------


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.admin_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


@patch("jentic_one.admin.services.oauth_client_service._verify_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_verify_client_secret_rejects_inactive_client(
    mock_repo: MagicMock,
    mock_verify: MagicMock,
) -> None:
    """An inactive client is rejected even if the secret is correct."""
    ctx = _make_ctx()
    client_row = MagicMock()
    client_row.active = False
    client_row.client_secret_hash = "hash"
    mock_repo.get_by_client_id = AsyncMock(return_value=client_row)

    svc = OAuthClientService(ctx)
    result = await svc.verify_client_secret("oc_test", "secret")

    assert result is False
    mock_verify.assert_awaited_once()


@patch("jentic_one.admin.services.oauth_client_service._verify_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_verify_client_secret_accepts_active_client(
    mock_repo: MagicMock,
    mock_verify: MagicMock,
) -> None:
    """An active client with correct secret is accepted."""
    ctx = _make_ctx()
    client_row = MagicMock()
    client_row.active = True
    client_row.client_secret_hash = "hash"
    mock_repo.get_by_client_id = AsyncMock(return_value=client_row)
    mock_verify.return_value = True

    svc = OAuthClientService(ctx)
    result = await svc.verify_client_secret("oc_test", "secret")

    assert result is True


# ---------- rotate_secret ----------


def _make_transactional_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_identity() -> MagicMock:
    identity = MagicMock()
    identity.sub = "usr_admin"
    identity.actor_type = "user"
    identity.origin.value = "api"
    return identity


@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service._hash_password_async")
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_rotate_secret_returns_new_plaintext_and_audits(
    mock_repo: MagicMock,
    mock_hash: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_transactional_ctx()
    mock_hash.return_value = "new_hash"
    mock_repo.update_secret_hash = AsyncMock(return_value=MagicMock(id="oac_1"))

    svc = OAuthClientService(ctx)
    new_secret = await svc.rotate_secret("oac_1", identity=_make_identity())

    assert isinstance(new_secret, str) and len(new_secret) > 0
    mock_repo.update_secret_hash.assert_awaited_once()
    mock_audit.assert_awaited_once()
    # Audit records the update action; secret plaintext must never appear.
    assert mock_audit.await_args is not None
    kwargs = mock_audit.await_args.kwargs
    assert kwargs["target_id"] == "oac_1"
    assert kwargs["after"] == {"secret_rotated": True}
    assert new_secret not in str(kwargs)


@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_rotate_secret_raises_when_not_found(mock_repo: MagicMock) -> None:
    ctx = _make_transactional_ctx()
    mock_repo.update_secret_hash = AsyncMock(return_value=None)

    svc = OAuthClientService(ctx)
    with pytest.raises(OAuthClientNotFoundError):
        await svc.rotate_secret("oac_missing", identity=_make_identity())


# ---------- deactivate ----------


@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_deactivate_records_before_snapshot(
    mock_repo: MagicMock, mock_audit: AsyncMock
) -> None:
    ctx = _make_transactional_ctx()
    existing = MagicMock()
    existing.name = "before"
    existing.description = "d"
    existing.redirect_uris = ["https://x.example.com/cb"]
    existing.active = True
    existing.require_consent = True
    existing.allowed_scopes = ["capabilities:read"]
    mock_repo.get_by_id = AsyncMock(return_value=existing)
    mock_repo.deactivate = AsyncMock(return_value=True)

    svc = OAuthClientService(ctx)
    await svc.deactivate("oac_1", identity=_make_identity())

    assert mock_audit.await_args is not None
    kwargs = mock_audit.await_args.kwargs
    assert kwargs["before"]["active"] is True
    assert kwargs["after"] == {"active": False}


@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_deactivate_raises_when_repo_reports_no_update(
    mock_repo: MagicMock,
) -> None:
    ctx = _make_transactional_ctx()
    mock_repo.get_by_id = AsyncMock(return_value=MagicMock())
    mock_repo.deactivate = AsyncMock(return_value=False)

    svc = OAuthClientService(ctx)
    with pytest.raises(OAuthClientNotFoundError):
        await svc.deactivate("oac_1", identity=_make_identity())


# ---------- update: allowed_scopes reset sentinel ----------


@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_update_wildcard_scope_triggers_reset(
    mock_repo: MagicMock, _mock_audit: AsyncMock
) -> None:
    ctx = _make_transactional_ctx()
    existing = MagicMock()
    existing.name = "before"
    existing.description = None
    existing.redirect_uris = ["https://x.example.com/cb"]
    existing.active = True
    existing.require_consent = True
    existing.allowed_scopes = ["capabilities:read"]
    updated = MagicMock()
    updated.name = "before"
    updated.description = None
    updated.redirect_uris = ["https://x.example.com/cb"]
    updated.active = True
    updated.require_consent = True
    updated.allowed_scopes = None
    updated.id = "oac_1"
    updated.client_id = "oc_1"
    updated.created_at = datetime.now(UTC)
    updated.updated_at = None
    updated.created_by = "usr"
    mock_repo.get_by_id = AsyncMock(return_value=existing)
    mock_repo.update = AsyncMock(return_value=updated)

    svc = OAuthClientService(ctx)
    await svc.update("oac_1", allowed_scopes=["*"], identity=_make_identity())

    assert mock_repo.update.await_args is not None
    kwargs = mock_repo.update.await_args.kwargs
    assert kwargs["reset_allowed_scopes"] is True
    assert kwargs["allowed_scopes"] is None


@patch("jentic_one.admin.services.oauth_client_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.admin.services.oauth_client_service.OAuthClientRepository")
async def test_update_empty_scopes_is_deny_all(
    mock_repo: MagicMock, _mock_audit: AsyncMock
) -> None:
    """allowed_scopes=[] means deny-all, not reset."""
    ctx = _make_transactional_ctx()
    existing = MagicMock()
    existing.allowed_scopes = ["capabilities:read"]
    existing.name = "n"
    existing.description = None
    existing.redirect_uris = ["https://x.example.com/cb"]
    existing.active = True
    existing.require_consent = True
    existing.id = "oac_1"
    existing.client_id = "oc_1"
    existing.created_at = datetime.now(UTC)
    existing.updated_at = None
    existing.created_by = "usr"
    mock_repo.get_by_id = AsyncMock(return_value=existing)
    mock_repo.update = AsyncMock(return_value=existing)

    svc = OAuthClientService(ctx)
    await svc.update("oac_1", allowed_scopes=[], identity=_make_identity())

    assert mock_repo.update.await_args is not None
    kwargs = mock_repo.update.await_args.kwargs
    assert kwargs["reset_allowed_scopes"] is False
    assert kwargs["allowed_scopes"] == []
