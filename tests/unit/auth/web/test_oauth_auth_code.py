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
_PUBLIC_CLIENT_ID = "oc_public_mcp_client"


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
    mock_authorize_svc.precheck_auth_code = AsyncMock(return_value=None)
    mock_authorize_svc.exchange_code = AsyncMock(
        return_value=("at_platform", "rt_platform", "id_token_platform", ["openid"])
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


@patch("jentic_one.auth.web.routers.oauth.OAuthClientService")
@patch("jentic_one.auth.web.routers.oauth.AuthorizeService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_third_party_client_sets_oauth_client_id(
    mock_token_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_oauth_svc_cls: MagicMock,
    client: TestClient,
) -> None:
    """A third-party registered client exchange passes its client_id as oauth_client_id."""
    mock_oauth_svc = MagicMock()
    mock_oauth_svc.authenticate_for_token_endpoint = AsyncMock(return_value=True)
    mock_oauth_svc_cls.return_value = mock_oauth_svc

    mock_authorize_svc = MagicMock()
    mock_authorize_svc.precheck_auth_code = AsyncMock(return_value=None)
    mock_authorize_svc.exchange_code = AsyncMock(
        return_value=("at_third_party", "rt_third_party", "id_token_third_party", ["openid"])
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

    mock_oauth_svc.authenticate_for_token_endpoint.assert_awaited_once_with(
        _THIRD_PARTY_CLIENT_ID, "plaintext_secret"
    )
    mock_authorize_svc.exchange_code.assert_awaited_once()
    call_kwargs = mock_authorize_svc.exchange_code.call_args[1]
    assert call_kwargs["oauth_client_id"] == _THIRD_PARTY_CLIENT_ID


@patch("jentic_one.auth.web.routers.oauth.OAuthClientService")
@patch("jentic_one.auth.web.routers.oauth.AuthorizeService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_third_party_client_without_secret_is_rejected(
    mock_token_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_oauth_svc_cls: MagicMock,
    client: TestClient,
) -> None:
    """A confidential client_id without a client_secret is rejected."""
    mock_oauth_svc = MagicMock()
    mock_oauth_svc.authenticate_for_token_endpoint = AsyncMock(return_value=False)
    mock_oauth_svc_cls.return_value = mock_oauth_svc
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)
    mock_authorize_svc = MagicMock()
    mock_authorize_svc.precheck_auth_code = AsyncMock(return_value=None)
    mock_authorize_cls.return_value = mock_authorize_svc

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
    mock_oauth_svc.authenticate_for_token_endpoint.assert_awaited_once_with(
        _THIRD_PARTY_CLIENT_ID, None
    )


@patch("jentic_one.auth.web.routers.oauth.OAuthClientService")
@patch("jentic_one.auth.web.routers.oauth.AuthorizeService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_public_client_exchanges_without_secret(
    mock_token_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_oauth_svc_cls: MagicMock,
    client: TestClient,
) -> None:
    """A public (token_endpoint_auth_method=none) client exchanges code + PKCE
    with no secret and still gets oauth_client_id lineage (D5)."""
    mock_oauth_svc = MagicMock()
    mock_oauth_svc.authenticate_for_token_endpoint = AsyncMock(return_value=True)
    mock_oauth_svc_cls.return_value = mock_oauth_svc

    mock_authorize_svc = MagicMock()
    mock_authorize_svc.precheck_auth_code = AsyncMock(return_value=None)
    mock_authorize_svc.exchange_code = AsyncMock(
        return_value=("at_public", "rt_public", "id_token_public", ["openid"])
    )
    mock_authorize_cls.return_value = mock_authorize_svc
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "authorization_code",
            "code": "auth_code_public",
            "code_verifier": "verifier_public",
            "redirect_uri": "http://localhost:33418/callback",
            "client_id": _PUBLIC_CLIENT_ID,
        },
    )

    assert resp.status_code == 200
    assert resp.json()["access_token"] == "at_public"

    # No secret was supplied and none was invented for the auth check.
    mock_oauth_svc.authenticate_for_token_endpoint.assert_awaited_once_with(_PUBLIC_CLIENT_ID, None)
    call_kwargs = mock_authorize_svc.exchange_code.call_args[1]
    assert call_kwargs["oauth_client_id"] == _PUBLIC_CLIENT_ID


@patch("jentic_one.auth.web.routers.oauth.OAuthClientService")
@patch("jentic_one.auth.web.routers.oauth.AuthorizeService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_public_client_missing_pkce_verifier_is_rejected(
    mock_token_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    mock_oauth_svc_cls: MagicMock,
    client: TestClient,
) -> None:
    """PKCE stays mandatory for public clients: no code_verifier → rejected
    before any client authentication or code exchange."""
    mock_oauth_svc = MagicMock()
    mock_oauth_svc.authenticate_for_token_endpoint = AsyncMock(return_value=True)
    mock_oauth_svc_cls.return_value = mock_oauth_svc
    mock_authorize_svc = MagicMock()
    mock_authorize_svc.precheck_auth_code = AsyncMock(return_value=None)
    mock_authorize_svc.exchange_code = AsyncMock()
    mock_authorize_cls.return_value = mock_authorize_svc
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "authorization_code",
            "code": "auth_code_nopkce",
            "redirect_uri": "http://localhost:33418/callback",
            "client_id": _PUBLIC_CLIENT_ID,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["type"] == "invalid_grant"
    mock_oauth_svc.authenticate_for_token_endpoint.assert_not_awaited()
    mock_authorize_svc.exchange_code.assert_not_awaited()


# ---------- refresh grant: public-client identification (RFC 6749 §6) ----------


@patch("jentic_one.auth.web.routers.oauth.OAuthClientService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_refresh_public_client_passes_client_id_without_secret(
    mock_token_cls: MagicMock,
    mock_oauth_svc_cls: MagicMock,
    client: TestClient,
) -> None:
    """Public clients refresh with client_id and no secret; the id is still
    forwarded so token_service enforces the RFC 6749 §6 client_id match."""
    mock_oauth_svc = MagicMock()
    mock_oauth_svc.is_public_client = AsyncMock(return_value=True)
    mock_oauth_svc.verify_client_secret = AsyncMock()
    mock_oauth_svc_cls.return_value = mock_oauth_svc

    mock_token_svc = MagicMock(access_ttl_seconds=3600)
    mock_token_svc.refresh = AsyncMock(return_value=("at_new", "rt_new", ["apis:read"]))
    mock_token_cls.return_value = mock_token_svc

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "refresh_token": "rt_old",
            "client_id": _PUBLIC_CLIENT_ID,
        },
    )

    assert resp.status_code == 200
    mock_token_svc.refresh.assert_awaited_once_with("rt_old", client_id=_PUBLIC_CLIENT_ID)
    mock_oauth_svc.verify_client_secret.assert_not_awaited()


@patch("jentic_one.auth.web.routers.oauth.OAuthClientService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_refresh_non_public_client_without_secret_stays_unverified(
    mock_token_cls: MagicMock,
    mock_oauth_svc_cls: MagicMock,
    client: TestClient,
) -> None:
    """Pre-3a behavior preserved: a client_id without secret that is NOT a
    public client refreshes without a verified client (platform-client path)."""
    mock_oauth_svc = MagicMock()
    mock_oauth_svc.is_public_client = AsyncMock(return_value=False)
    mock_oauth_svc_cls.return_value = mock_oauth_svc

    mock_token_svc = MagicMock(access_ttl_seconds=3600)
    mock_token_svc.refresh = AsyncMock(return_value=("at_new", "rt_new", ["apis:read"]))
    mock_token_cls.return_value = mock_token_svc

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "refresh_token": "rt_old",
            "client_id": _PLATFORM_CLIENT_ID,
        },
    )

    assert resp.status_code == 200
    mock_token_svc.refresh.assert_awaited_once_with("rt_old", client_id=None)
