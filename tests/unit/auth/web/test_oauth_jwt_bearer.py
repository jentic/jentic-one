"""Unit tests for the JWT Bearer grant through the OAuth token endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.auth.services.errors import AuthServiceError, InvalidGrantError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import oauth
from jentic_one.shared.config import AuthConfig


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


@patch("jentic_one.auth.web.routers.oauth.AssertionService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_jwt_bearer_grant_success(
    mock_token_cls: MagicMock, mock_assertion_cls: MagicMock, client: TestClient
) -> None:
    mock_assertion_instance = MagicMock()
    mock_assertion_instance.verify_and_exchange = AsyncMock(
        return_value=("at_new_token", "rt_new_token")
    )
    mock_assertion_cls.return_value = mock_assertion_instance

    mock_token_instance = MagicMock()
    mock_token_instance.access_ttl_seconds = 3600
    mock_token_cls.return_value = mock_token_instance

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": "eyJ.test.assertion",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "at_new_token"
    assert data["refresh_token"] == "rt_new_token"
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 3600


@patch("jentic_one.auth.web.routers.oauth.AssertionService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_jwt_bearer_grant_missing_assertion(
    mock_token_cls: MagicMock, mock_assertion_cls: MagicMock, client: TestClient
) -> None:
    mock_token_instance = MagicMock()
    mock_token_instance.access_ttl_seconds = 3600
    mock_token_cls.return_value = mock_token_instance

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        },
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["type"] == "invalid_grant"


@patch("jentic_one.auth.web.routers.oauth.AssertionService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_jwt_bearer_grant_invalid_assertion(
    mock_token_cls: MagicMock, mock_assertion_cls: MagicMock, client: TestClient
) -> None:
    mock_assertion_instance = MagicMock()
    mock_assertion_instance.verify_and_exchange = AsyncMock(
        side_effect=InvalidGrantError("Assertion is invalid")
    )
    mock_assertion_cls.return_value = mock_assertion_instance

    mock_token_instance = MagicMock()
    mock_token_instance.access_ttl_seconds = 3600
    mock_token_cls.return_value = mock_token_instance

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": "eyJ.invalid.token",
        },
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["type"] == "invalid_grant"


@patch("jentic_one.auth.web.routers.oauth.AssertionService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_unsupported_grant_type_returns_400(
    mock_token_cls: MagicMock, mock_assertion_cls: MagicMock, client: TestClient
) -> None:
    mock_token_instance = MagicMock()
    mock_token_instance.access_ttl_seconds = 3600
    mock_token_cls.return_value = mock_token_instance

    resp = client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["type"] == "invalid_grant"
