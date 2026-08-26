"""Unit tests for the authorization_code grant on the /oauth/token endpoint.

Verifies that platform (first-party) clients do NOT propagate an oauth_client_id
to issued tokens, while third-party registered clients DO.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import oauth
from jentic_one.shared.config import AuthConfig, PlatformClientConfig


_PLATFORM_CLIENT_ID = "jentic-one-spa"
_THIRD_PARTY_CLIENT_ID = "oc_external_app_123"


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(oauth.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)

    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(
        canonical_base_url="https://auth.example.com",
        platform_clients=[
            PlatformClientConfig(
                client_id=_PLATFORM_CLIENT_ID,
                redirect_uris=["https://app.example.com/auth/callback"],
            )
        ],
    )

    mock_session = AsyncMock()
    mock_ctx.admin_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.admin_db.session.return_value.__aexit__ = AsyncMock(return_value=False)

    app.state.ctx = mock_ctx
    return TestClient(app)


@patch("jentic_one.auth.web.routers.oauth.AuthorizeService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_platform_client_does_not_set_oauth_client_id(
    mock_token_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    client: TestClient,
) -> None:
    """A platform (first-party) client exchange passes oauth_client_id=None."""
    mock_authorize_svc = MagicMock()
    mock_authorize_svc.exchange_code = AsyncMock(
        return_value=("at_platform", "rt_platform", "id_token_platform")
    )
    mock_authorize_cls.return_value = mock_authorize_svc
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "authorization_code",
            "code": "auth_code_123",
            "code_verifier": "verifier_abc",
            "redirect_uri": "https://app.example.com/auth/callback",
            "client_id": _PLATFORM_CLIENT_ID,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "at_platform"

    mock_authorize_svc.exchange_code.assert_awaited_once()
    call_kwargs = mock_authorize_svc.exchange_code.call_args[1]
    assert call_kwargs["oauth_client_id"] is None


@patch("jentic_one.auth.web.routers.oauth.OAuthClientRepository")
@patch("jentic_one.auth.web.routers.oauth.verify_password", return_value=True)
@patch("jentic_one.auth.web.routers.oauth.AuthorizeService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_third_party_client_sets_oauth_client_id(
    mock_token_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_verify_pw: MagicMock,
    mock_oauth_repo: MagicMock,
    client: TestClient,
) -> None:
    """A third-party registered client exchange passes its client_id as oauth_client_id."""
    mock_db_client = MagicMock()
    mock_db_client.client_secret_hash = "hashed_secret"
    mock_db_client.active = True
    mock_oauth_repo.get_by_client_id = AsyncMock(return_value=mock_db_client)

    mock_authorize_svc = MagicMock()
    mock_authorize_svc.exchange_code = AsyncMock(
        return_value=("at_third_party", "rt_third_party", "id_token_third_party")
    )
    mock_authorize_cls.return_value = mock_authorize_svc
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "authorization_code",
            "code": "auth_code_456",
            "code_verifier": "verifier_def",
            "redirect_uri": "https://external.example.com/callback",
            "client_id": _THIRD_PARTY_CLIENT_ID,
            "client_secret": "plaintext_secret",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "at_third_party"

    mock_authorize_svc.exchange_code.assert_awaited_once()
    call_kwargs = mock_authorize_svc.exchange_code.call_args[1]
    assert call_kwargs["oauth_client_id"] == _THIRD_PARTY_CLIENT_ID


@patch("jentic_one.auth.web.routers.oauth.AuthorizeService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_third_party_client_without_secret_is_rejected(
    mock_token_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    client: TestClient,
) -> None:
    """A non-platform client_id without a client_secret is rejected."""
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)
    mock_authorize_cls.return_value = MagicMock()

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "authorization_code",
            "code": "auth_code_789",
            "code_verifier": "verifier_ghi",
            "redirect_uri": "https://external.example.com/callback",
            "client_id": _THIRD_PARTY_CLIENT_ID,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["type"] == "invalid_grant"
