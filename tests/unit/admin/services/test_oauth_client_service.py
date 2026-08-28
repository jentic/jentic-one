"""Unit tests for OAuth client service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.admin.services.errors import InvalidInputError
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
