"""Unit tests for the mid-flow D7 client-gate re-checks (PR #1218 review).

The approval+active gate runs at /authorize entry, but the signed-state
window (STATE_MAX_AGE_SECONDS) leaves the rest of the flow open: these tests
pin the re-checks at the IdP callback and the consent-submit step, so a
client denied (or deactivated) after /authorize can neither provision a user
row nor mint an authorization code. Rejections are browser-facing error
redirects (/error), never OAuth error redirects to the client's redirect_uri
(D7: clients can't observe browser-side rejections).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.auth.web.routers import authorize
from jentic_one.auth.web.routers.authorize import _derive_key, _sign_payload
from jentic_one.shared.config import AuthConfig
from jentic_one.shared.state.backend import MemoryStateBackend

_JWT_SECRET = "test-authorize-secret"
_CLIENT_ID = "oc_midflow_app"
_REDIRECT_URI = "https://app.example.com/cb"
_HANDLE = "handle-midflow-test"


def _client_view(*, active: bool = True, approval_status: str = "approved") -> OAuthClientView:
    return OAuthClientView(
        id="oac_1",
        client_id=_CLIENT_ID,
        name="app",
        description=None,
        redirect_uris=[_REDIRECT_URI],
        allowed_scopes=None,
        active=active,
        require_consent=True,
        token_endpoint_auth_method="none",
        consent_model="user",
        registration_source="admin",
        software_id=None,
        approval_status=approval_status,
        created_at=datetime.now(UTC),
        updated_at=None,
        created_by=None,
    )


def _make_app() -> tuple[TestClient, MemoryStateBackend]:
    app = FastAPI()
    app.include_router(authorize.router)

    ctx = MagicMock()
    ctx.config.auth = AuthConfig(
        canonical_base_url="https://auth.example.com",
        platform_clients=[],
    )
    ctx.config.admin.auth.jwt_secret.get_secret_value.return_value = _JWT_SECRET
    app.state.ctx = ctx

    backend = MemoryStateBackend()
    app.state.auth_state_backend = backend
    return TestClient(app), backend


def _signed_state() -> str:
    return _sign_payload(
        {
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_challenge": "challenge",
            "scope": "openid",
            "nonce": None,
            "original_state": None,
            "iat": str(int(time.time())),
        },
        _derive_key(_JWT_SECRET, "state"),
        purpose="state",
    )


def _seed_consent_handle(backend: MemoryStateBackend, handle: str = _HANDLE) -> None:
    """Store the server-side consent params, as the callback would have."""
    payload = json.dumps(
        {
            "claims": {
                "external_subject": "ext-1",
                "email": "user@example.com",
                "email_verified": True,
                "first_name": "Mid",
                "last_name": "Flow",
            },
            "redirect_uri": _REDIRECT_URI,
            "original_state": None,
            "client_id": _CLIENT_ID,
            "code_challenge": "challenge",
            "scope": "openid",
            "nonce": None,
            "client_name": "app",
            "client_description": None,
            "user_email": "user@example.com",
            "iat": int(time.time()),
        }
    ).encode()
    # MemoryStateBackend is a plain dict store, safe to drive from its own loop.
    asyncio.run(backend.set(f"consent-handle:{handle}", payload, ttl_s=300.0))


# ---------- IdP callback ----------


@pytest.mark.parametrize(
    ("active", "approval_status"),
    [
        (True, "denied"),
        (True, "pending"),
        (False, "approved"),
    ],
)
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.routers.authorize.OAuthClientService")
def test_callback_rejects_client_gated_mid_flow(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    active: bool,
    approval_status: str,
) -> None:
    """A client denied/deactivated between /authorize and the IdP callback is
    stopped before the IdP code exchange — even a denied row force-set active
    (approval_status is checked independently)."""
    client, _backend = _make_app()
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(
        return_value=_client_view(active=active, approval_status=approval_status)
    )
    mock_authorize_svc = MagicMock()
    mock_authorize_svc.exchange_idp_code_for_claims = AsyncMock()
    mock_authorize_svc.handle_idp_callback_with_email = AsyncMock()
    mock_authorize_cls.return_value = mock_authorize_svc

    resp = client.get(
        "/oauth/callback",
        params={"code": "idp_code", "state": _signed_state()},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=access_denied"
    mock_authorize_svc.exchange_idp_code_for_claims.assert_not_awaited()
    mock_authorize_svc.handle_idp_callback_with_email.assert_not_awaited()


# ---------- consent submit ----------


@pytest.mark.parametrize(
    ("active", "approval_status"),
    [
        (True, "denied"),
        (True, "pending"),
        (False, "approved"),
    ],
)
@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.routers.authorize.OAuthClientService")
def test_consent_submit_rejects_client_gated_mid_flow(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
    active: bool,
    approval_status: str,
) -> None:
    """The #1218 review scenario: deny the client between /authorize and the
    consent submit → no code minted, and no user row provisioned."""
    client, backend = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(
        return_value=_client_view(active=active, approval_status=approval_status)
    )
    mock_authorize_svc = MagicMock()
    mock_authorize_svc.provision_from_claims = AsyncMock()
    mock_authorize_svc.record_consent_decision = AsyncMock()
    mock_authorize_svc.issue_authorization_code = AsyncMock()
    mock_authorize_cls.return_value = mock_authorize_svc

    resp = client.post(
        "/oauth/consent",
        data={"consent_token": _HANDLE, "action": "approve"},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=access_denied"
    mock_authorize_svc.provision_from_claims.assert_not_awaited()
    mock_authorize_svc.issue_authorization_code.assert_not_awaited()


@patch("jentic_one.auth.web.routers.authorize.AuthorizeService")
@patch("jentic_one.auth.web.routers.authorize.OAuthClientService")
def test_consent_submit_approved_client_still_mints_code(
    mock_client_svc_cls: MagicMock,
    mock_authorize_cls: MagicMock,
) -> None:
    """Control: the re-check must not break the approved+active happy path."""
    client, backend = _make_app()
    _seed_consent_handle(backend)
    mock_client_svc_cls.return_value.get_by_client_id = AsyncMock(return_value=_client_view())
    mock_authorize_svc = MagicMock()
    mock_authorize_svc.provision_from_claims = AsyncMock(return_value="usr_1")
    mock_authorize_svc.record_consent_decision = AsyncMock()
    mock_authorize_svc.issue_authorization_code = AsyncMock(return_value="code_abc")
    mock_authorize_cls.return_value = mock_authorize_svc

    resp = client.post(
        "/oauth/consent",
        data={"consent_token": _HANDLE, "action": "approve"},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == f"{_REDIRECT_URI}?code=code_abc"
    mock_authorize_svc.issue_authorization_code.assert_awaited_once()
