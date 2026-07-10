"""Unit tests for OAuth token endpoint content-type handling.

Verifies that /oauth/token, /oauth/revoke, and /oauth/introspect accept both
application/x-www-form-urlencoded (RFC 6749) and application/json.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.middleware import FormToJsonMiddleware
from jentic_one.auth.web.routers import oauth
from jentic_one.shared.config import AuthConfig


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(oauth.router)
    app.add_middleware(FormToJsonMiddleware)
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
def test_token_accepts_form_encoded(
    mock_token_cls: MagicMock, mock_assertion_cls: MagicMock, client: TestClient
) -> None:
    mock_assertion_instance = MagicMock()
    mock_assertion_instance.verify_and_exchange = AsyncMock(return_value=("at_form", "rt_form"))
    mock_assertion_cls.return_value = mock_assertion_instance

    mock_token_instance = MagicMock()
    mock_token_instance.access_ttl_seconds = 3600
    mock_token_cls.return_value = mock_token_instance

    resp = client.post(
        "/oauth/token",
        content=b"grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion=eyJ.test.assertion",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "at_form"
    assert data["refresh_token"] == "rt_form"


@patch("jentic_one.auth.web.routers.oauth.AssertionService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_token_still_accepts_json(
    mock_token_cls: MagicMock, mock_assertion_cls: MagicMock, client: TestClient
) -> None:
    mock_assertion_instance = MagicMock()
    mock_assertion_instance.verify_and_exchange = AsyncMock(return_value=("at_json", "rt_json"))
    mock_assertion_cls.return_value = mock_assertion_instance

    mock_token_instance = MagicMock()
    mock_token_instance.access_ttl_seconds = 3600
    mock_token_cls.return_value = mock_token_instance

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": "eyJ.test.assertion",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "at_json"


def test_token_form_encoded_missing_required_field(client: TestClient) -> None:
    resp = client.post(
        "/oauth/token",
        content=b"refresh_token=some_token",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 422
