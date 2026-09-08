"""Unit tests pinning form-encoded body support on the OAuth token plane.

RFC 6749 §4.1.3 (token) and RFC 7662 §2.1 (introspection) prescribe
``application/x-www-form-urlencoded`` request bodies; the platform's JSON
contract stays supported on both endpoints. Route-level content negotiation
only — grant semantics live in the per-grant test modules, and the revocation
endpoint's dual arms are pinned in ``test_oauth_revocation_router.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import oauth
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig
from jentic_one.shared.web.deps import resolve_identity


def _fake_identity() -> Identity:
    return Identity(sub="usr_test", email="test@example.com", permissions=[])


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(oauth.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.dependency_overrides[resolve_identity] = _fake_identity

    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(
        canonical_base_url="https://auth.example.com",
        assertion_max_ttl_seconds=300,
    )
    app.state.ctx = mock_ctx
    return TestClient(app)


# ---------- /oauth/token: RFC 6749 §4.1.3 form encoding ----------


@patch("jentic_one.auth.web.routers.oauth.AssertionService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_token_accepts_form_encoded(
    mock_token_cls: MagicMock, mock_assertion_cls: MagicMock, client: TestClient
) -> None:
    mock_assertion_instance = MagicMock()
    mock_assertion_instance.verify_and_exchange = AsyncMock(
        return_value=("at_form", "rt_form", ["apis:read"])
    )
    mock_assertion_cls.return_value = mock_assertion_instance
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": "eyJ.test.assertion",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "at_form"
    assert data["refresh_token"] == "rt_form"


def test_token_form_encoded_missing_grant_type_is_400_invalid_grant(
    client: TestClient,
) -> None:
    """A malformed form body answers the RFC 6749 §5.2 400, not a framework 422."""
    resp = client.post("/oauth/token", data={"refresh_token": "some_token"})
    assert resp.status_code == 400
    assert resp.json()["type"] == "invalid_grant"


# ---------- /oauth/introspect: RFC 7662 §2.1 form encoding ----------


@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_introspect_accepts_form_encoded(mock_token_cls: MagicMock, client: TestClient) -> None:
    mock_token_svc = MagicMock()
    mock_token_svc.introspect = AsyncMock(
        return_value={"active": True, "sub": "usr_test", "scope": "read"}
    )
    mock_token_cls.return_value = mock_token_svc

    resp = client.post(
        "/oauth/introspect",
        data={"token": "at_known", "token_type_hint": "access_token"},
        headers={"Authorization": "Bearer platform-token"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] is True
    assert data["sub"] == "usr_test"
    mock_token_svc.introspect.assert_awaited_once_with("at_known")


@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_introspect_still_accepts_json(mock_token_cls: MagicMock, client: TestClient) -> None:
    """The platform's own JSON contract (used by the generated clients) holds."""
    mock_token_svc = MagicMock()
    mock_token_svc.introspect = AsyncMock(return_value={"active": False})
    mock_token_cls.return_value = mock_token_svc

    resp = client.post(
        "/oauth/introspect",
        json={"token": "at_unknown"},
        headers={"Authorization": "Bearer platform-token"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"active": False}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"data": {"token_type_hint": "access_token"}},  # form arm, no token
        {"json": {"token_type_hint": "access_token"}},  # JSON arm, no token
        {"content": b""},  # empty body
    ],
)
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_introspect_malformed_body_is_400_invalid_request(
    mock_token_cls: MagicMock, kwargs: dict[str, object], client: TestClient
) -> None:
    """RFC 7662 §2.1: a request missing the required ``token`` parameter
    answers 400 ``invalid_request`` on both arms; the service never runs."""
    resp = client.post(
        "/oauth/introspect",
        headers={"Authorization": "Bearer platform-token"},
        **kwargs,  # type: ignore[arg-type]
    )
    assert resp.status_code == 400
    assert resp.json()["type"] == "invalid_request"
    mock_token_cls.return_value.introspect.assert_not_called()
