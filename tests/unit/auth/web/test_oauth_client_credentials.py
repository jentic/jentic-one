"""Unit tests for the client_credentials grant and /oauth/mint endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.auth.services.errors import AuthServiceError, InvalidGrantError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import oauth
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig
from jentic_one.shared.models import ActorType
from jentic_one.shared.web.deps import resolve_identity


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(oauth.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)

    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(
        canonical_base_url="https://auth.example.com",
        assertion_max_ttl_seconds=300,
    )
    app.state.ctx = mock_ctx
    return TestClient(app)


@pytest.fixture()
def authed_client() -> TestClient:
    """Client with identity override for mint endpoint tests."""
    app = FastAPI()
    app.include_router(oauth.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)

    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(
        canonical_base_url="https://auth.example.com",
        assertion_max_ttl_seconds=300,
    )
    app.state.ctx = mock_ctx

    async def _override_identity() -> Identity:
        return Identity(
            sub="sva_host123",
            email="",
            permissions=["capabilities:execute", "capabilities:read"],
            actor_type=ActorType.SERVICE_ACCOUNT,
        )

    app.dependency_overrides[resolve_identity] = _override_identity
    return TestClient(app)


@patch("jentic_one.auth.web.routers.oauth.ServiceAccountAuthService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_client_credentials_success(
    mock_token_cls: MagicMock,
    mock_sa_auth_cls: MagicMock,
    client: TestClient,
) -> None:
    mock_sa_auth = MagicMock()
    mock_sa_auth.authenticate_client_credentials = AsyncMock(
        return_value=("at_sa_token", "rt_sa_token")
    )
    mock_sa_auth.access_ttl_seconds = 3600
    mock_sa_auth_cls.return_value = mock_sa_auth

    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "sva_test123",
            "client_secret": "jcs_secret_value",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "at_sa_token"
    assert data["refresh_token"] == "rt_sa_token"
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 3600


@patch("jentic_one.auth.web.routers.oauth.ServiceAccountAuthService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_client_credentials_missing_client_id(
    mock_token_cls: MagicMock,
    mock_sa_auth_cls: MagicMock,
    client: TestClient,
) -> None:
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)
    mock_sa_auth_cls.return_value = MagicMock()

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_secret": "jcs_secret_value",
        },
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["type"] == "invalid_grant"


@patch("jentic_one.auth.web.routers.oauth.ServiceAccountAuthService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_client_credentials_invalid_secret(
    mock_token_cls: MagicMock,
    mock_sa_auth_cls: MagicMock,
    client: TestClient,
) -> None:
    mock_sa_auth = MagicMock()
    mock_sa_auth.authenticate_client_credentials = AsyncMock(
        side_effect=InvalidGrantError("invalid_client")
    )
    mock_sa_auth_cls.return_value = mock_sa_auth

    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "sva_test123",
            "client_secret": "jcs_wrong_secret",
        },
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["type"] == "invalid_grant"


@patch("jentic_one.auth.web.routers.oauth.ServiceAccountAuthService")
def test_mint_endpoint_success(
    mock_sa_auth_cls: MagicMock,
    authed_client: TestClient,
) -> None:
    mock_sa_auth = MagicMock()
    mock_sa_auth.mint_task_token = AsyncMock(return_value="at_ephemeral_token")
    mock_sa_auth_cls.return_value = mock_sa_auth

    resp = authed_client.post(
        "/oauth/mint",
        json={
            "scope": "capabilities:execute",
            "target_agent_id": "agnt_task1",
            "ttl_seconds": 120,
        },
        headers={"Authorization": "Bearer at_valid_sa_token"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "at_ephemeral_token"
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 120


@patch("jentic_one.auth.web.routers.oauth.ServiceAccountAuthService")
def test_mint_endpoint_superset_scope_rejected(
    mock_sa_auth_cls: MagicMock,
    authed_client: TestClient,
) -> None:
    mock_sa_auth = MagicMock()
    mock_sa_auth.mint_task_token = AsyncMock(
        side_effect=InvalidGrantError("invalid_scope: requested scopes exceed host SA grants")
    )
    mock_sa_auth_cls.return_value = mock_sa_auth

    resp = authed_client.post(
        "/oauth/mint",
        json={
            "scope": "capabilities:execute capabilities:read",
            "target_agent_id": "agnt_task1",
        },
        headers={"Authorization": "Bearer at_valid_sa_token"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["type"] == "invalid_grant"


@patch("jentic_one.auth.web.routers.oauth.ServiceAccountAuthService")
def test_mint_endpoint_default_ttl(
    mock_sa_auth_cls: MagicMock,
    authed_client: TestClient,
) -> None:
    mock_sa_auth = MagicMock()
    mock_sa_auth.mint_task_token = AsyncMock(return_value="at_ephemeral")
    mock_sa_auth_cls.return_value = mock_sa_auth

    resp = authed_client.post(
        "/oauth/mint",
        json={
            "scope": "capabilities:execute",
            "target_agent_id": "agnt_task1",
        },
        headers={"Authorization": "Bearer at_valid_sa_token"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["expires_in"] == 300

    mock_sa_auth.mint_task_token.assert_called_once_with(
        host_sa_id="sva_host123",
        host_sa_scopes=["capabilities:execute", "capabilities:read"],
        requested_scopes=["capabilities:execute"],
        target_agent_id="agnt_task1",
        ttl_seconds=300,
    )
