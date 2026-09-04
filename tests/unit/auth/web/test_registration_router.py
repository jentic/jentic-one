"""Unit tests for the registration router."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.auth.services.errors import (
    AuthServiceError,
    RegistrationAccessDeniedError,
)
from jentic_one.auth.services.registration_service import PollResult, RegisterResult
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import registration
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig
from jentic_one.shared.web.deps import resolve_identity


def _fake_identity() -> Identity:
    return Identity(sub="usr_test", email="test@example.com", permissions=[])


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(registration.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.dependency_overrides[resolve_identity] = _fake_identity

    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    app.state.ctx = mock_ctx
    return TestClient(app)


@patch("jentic_one.auth.web.routers.registration.RegistrationService")
def test_post_register_returns_201(mock_svc_cls: MagicMock, client: TestClient) -> None:
    mock_instance = MagicMock()
    mock_instance.register = AsyncMock(
        return_value=RegisterResult(
            client_id="agnt_abc123",
            registration_access_token="rat_secret",
            registration_client_uri="https://auth.example.com/register/agnt_abc123",
            status="pending",
        )
    )
    mock_svc_cls.return_value = mock_instance

    resp = client.post(
        "/register",
        json={
            "client_name": "my-agent",
            "jwks": {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": "dGVzdA", "kid": "k1"}]},
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["client_id"] == "agnt_abc123"
    assert data["registration_access_token"] == "rat_secret"
    assert data["status"] == "pending"
    assert data["grant_types"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    assert data["token_endpoint_auth_method"] == "private_key_jwt"


@patch("jentic_one.auth.web.routers.registration.RegistrationService")
def test_post_register_omits_unset_claim_token_not_null(
    mock_svc_cls: MagicMock, client: TestClient
) -> None:
    """RFC 7591 §3.2.1: on the OSS single-user default no claim-token minter
    is installed, so ``claim_token`` is OMITTED from the response body —
    never serialized as JSON ``null`` (the DCR-door/#1250 posture)."""
    mock_instance = MagicMock()
    mock_instance.register = AsyncMock(
        return_value=RegisterResult(
            client_id="agnt_abc123",
            registration_access_token="rat_secret",
            registration_client_uri="https://auth.example.com/register/agnt_abc123",
            status="pending",
        )
    )
    mock_svc_cls.return_value = mock_instance

    resp = client.post(
        "/register",
        json={
            "client_name": "my-agent",
            "jwks": {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": "dGVzdA", "kid": "k1"}]},
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "claim_token" not in data
    # Belt-and-braces: the raw body carries no null members at all.
    assert b"null" not in resp.content


@patch("jentic_one.auth.web.routers.registration.RegistrationService")
def test_post_register_keeps_set_claim_token(mock_svc_cls: MagicMock, client: TestClient) -> None:
    """exclude_none must not drop a claim_token that IS minted (multi-user
    deployments with a claim-token minter installed)."""
    mock_instance = MagicMock()
    mock_instance.register = AsyncMock(
        return_value=RegisterResult(
            client_id="agnt_abc123",
            registration_access_token="rat_secret",
            registration_client_uri="https://auth.example.com/register/agnt_abc123",
            status="pending",
            claim_token="clm_secret",
        )
    )
    mock_svc_cls.return_value = mock_instance

    resp = client.post(
        "/register",
        json={
            "client_name": "my-agent",
            "jwks": {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": "dGVzdA", "kid": "k1"}]},
        },
    )
    assert resp.status_code == 201
    assert resp.json()["claim_token"] == "clm_secret"


@patch("jentic_one.auth.web.routers.registration.RegistrationService")
def test_get_register_poll_returns_200(mock_svc_cls: MagicMock, client: TestClient) -> None:
    mock_instance = MagicMock()
    mock_instance.poll_status = AsyncMock(
        return_value=PollResult(client_id="agnt_abc123", status="active")
    )
    mock_svc_cls.return_value = mock_instance

    resp = client.get(
        "/register/agnt_abc123",
        headers={"Authorization": "Bearer rat_secret"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["client_id"] == "agnt_abc123"
    assert data["status"] == "active"


@patch("jentic_one.auth.web.routers.registration.RegistrationService")
def test_get_register_missing_auth_returns_401(mock_svc_cls: MagicMock, client: TestClient) -> None:
    mock_instance = MagicMock()
    mock_instance.poll_status = AsyncMock(
        side_effect=RegistrationAccessDeniedError("missing or invalid authorization header")
    )
    mock_svc_cls.return_value = mock_instance

    resp = client.get("/register/agnt_abc123")
    assert resp.status_code == 401


def test_put_register_returns_403(client: TestClient) -> None:
    resp = client.put("/register/agnt_abc123", json={})
    assert resp.status_code == 403
    data = resp.json()
    assert data["type"] == "operation_not_supported"


def test_delete_register_returns_403(client: TestClient) -> None:
    resp = client.delete("/register/agnt_abc123")
    assert resp.status_code == 403
    data = resp.json()
    assert data["type"] == "operation_not_supported"
