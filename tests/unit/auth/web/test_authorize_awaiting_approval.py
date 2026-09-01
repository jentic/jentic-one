"""Unit tests for the /authorize awaiting-approval page (D7, design §4.3).

A registered-but-unapproved client must get a human HTML page (200) — never an
OAuth error redirect — and the page must not reveal pending vs denied.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import authorize
from jentic_one.shared.config import AuthConfig

_AUTHORIZE_PARAMS = {
    "response_type": "code",
    "client_id": "oc_test",
    "redirect_uri": "https://app.example.com/cb",
    "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    "code_challenge_method": "S256",
    "scope": "apis:read",
}


def _client_view(*, approval_status: str, active: bool, name: str = "Cursor") -> OAuthClientView:
    return OAuthClientView(
        id="oac_1",
        client_id="oc_test",
        name=name,
        description=None,
        redirect_uris=["https://app.example.com/cb"],
        allowed_scopes=None,
        active=active,
        require_consent=True,
        token_endpoint_auth_method="none",
        consent_model="agent",
        registration_source="dcr",
        software_id="com.cursor.ide",
        approval_status=approval_status,
        created_at=datetime.now(UTC),
        updated_at=None,
        created_by=None,
    )


@pytest.fixture()
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(authorize.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)

    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    mock_ctx.config.auth.platform_clients = []
    mock_ctx.config.admin.auth.jwt_secret.get_secret_value = MagicMock(return_value="secret")
    app.state.ctx = mock_ctx

    authorize_svc = MagicMock()
    authorize_svc.get_authorize_redirect_url = MagicMock(
        return_value="https://idp.example.com/authorize?state=x"
    )
    app.dependency_overrides[authorize.get_authorize_service] = lambda: authorize_svc
    return app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app, follow_redirects=False)


def _with_client_view(view: OAuthClientView | None) -> MagicMock:
    mock_svc = MagicMock()
    mock_svc.get_by_client_id = AsyncMock(return_value=view)
    return mock_svc


@pytest.mark.parametrize(
    ("approval_status", "active"),
    [("pending", False), ("denied", False)],
)
def test_unapproved_client_gets_human_page_not_redirect(
    client: TestClient, approval_status: str, active: bool
) -> None:
    view = _client_view(approval_status=approval_status, active=active)
    with patch.object(authorize, "OAuthClientService", return_value=_with_client_view(view)):
        resp = client.get("/authorize", params=_AUTHORIZE_PARAMS)

    assert resp.status_code == 200
    assert "location" not in resp.headers
    assert resp.headers["content-type"].startswith("text/html")
    assert "awaiting administrator approval" in resp.text
    assert "Cursor" in resp.text
    # Consent-grade security headers on the human page (§9 security-shaped).
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Cache-Control"] == "no-store"


def test_page_does_not_leak_pending_vs_denied(client: TestClient) -> None:
    """Deny is reversible and silent — the page body is identical either way."""
    bodies: dict[str, str] = {}
    for status_value in ("pending", "denied"):
        view = _client_view(approval_status=status_value, active=False)
        with patch.object(authorize, "OAuthClientService", return_value=_with_client_view(view)):
            resp = client.get("/authorize", params=_AUTHORIZE_PARAMS)
        bodies[status_value] = resp.text
    assert bodies["pending"] == bodies["denied"]
    assert "pending" not in bodies["pending"].lower()
    assert "denied" not in bodies["denied"].lower()


def test_client_name_is_escaped_on_page(client: TestClient) -> None:
    view = _client_view(approval_status="pending", active=False, name="<script>alert(1)</script>")
    with patch.object(authorize, "OAuthClientService", return_value=_with_client_view(view)):
        resp = client.get("/authorize", params=_AUTHORIZE_PARAMS)
    assert resp.status_code == 200
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text


def test_approved_active_client_proceeds_to_idp_redirect(client: TestClient) -> None:
    """The gate only intercepts unapproved rows — approved clients keep the
    normal flow (redirect to the IdP)."""
    view = _client_view(approval_status="approved", active=True)
    with patch.object(authorize, "OAuthClientService", return_value=_with_client_view(view)):
        resp = client.get("/authorize", params=_AUTHORIZE_PARAMS)

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://idp.example.com/")


def test_unknown_client_keeps_existing_error_path(client: TestClient) -> None:
    """No registry row → today's invalid_redirect_uri error redirect, no page."""
    with patch.object(authorize, "OAuthClientService", return_value=_with_client_view(None)):
        resp = client.get("/authorize", params=_AUTHORIZE_PARAMS)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_redirect_uri"


def test_approved_but_deactivated_client_keeps_error_path(client: TestClient) -> None:
    """active=false on an approved row is the kill switch (distinct from the
    approval gate) — it stays on the existing error path, not the human page."""
    view = _client_view(approval_status="approved", active=False)
    with patch.object(authorize, "OAuthClientService", return_value=_with_client_view(view)):
        resp = client.get("/authorize", params=_AUTHORIZE_PARAMS)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_redirect_uri"
