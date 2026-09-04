"""Unit tests for the /authorize approval-in-flow surface (P2).

Covers: the approval-pending page render (poll wiring, deep link, hidden admin
panel, resume URL, deny redirect safety), the anonymous signed-state status
endpoint (tri-state only, 400 on bad/expired blobs, own rate bucket), and the
inline admin decision endpoint (same authz gate + service calls as the admin
approval routes; 401 anonymous, 403 without oauth-clients:write).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import authorize
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig
from jentic_one.shared.state.backend import MemoryStateBackend
from jentic_one.shared.web.deps import resolve_identity

_AUTHORIZE_PARAMS = {
    "response_type": "code",
    "client_id": "oc_test",
    "redirect_uri": "https://app.example.com/cb",
    "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    "code_challenge_method": "S256",
    "scope": "apis:read",
    "state": "client-state-1",
}

_SECRET = "secret"


def _client_view(
    *,
    approval_status: str,
    active: bool,
    name: str = "Cursor",
    redirect_uris: list[str] | None = None,
) -> OAuthClientView:
    return OAuthClientView(
        id="oac_1",
        client_id="oc_test",
        name=name,
        description=None,
        redirect_uris=redirect_uris
        if redirect_uris is not None
        else ["https://app.example.com/cb"],
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
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]

    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    mock_ctx.config.auth.platform_clients = []
    mock_ctx.config.admin.auth.jwt_secret.get_secret_value = MagicMock(return_value=_SECRET)
    app.state.ctx = mock_ctx
    # One shared backend so rate-limit buckets persist across requests.
    app.state.auth_state_backend = MemoryStateBackend()

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


def _page_config(html: str) -> dict[str, object]:
    """Extract the embedded JSON config block from the rendered page."""
    marker = '<script id="approval-config" type="application/json">'
    start = html.index(marker) + len(marker)
    end = html.index("</script>", start)
    data = json.loads(html[start:end])
    assert isinstance(data, dict)
    return data


def _mint_state(**overrides: str | None) -> str:
    payload: dict[str, str | None] = {
        "client_id": "oc_test",
        "redirect_uri": "https://app.example.com/cb",
        "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        "scope": "apis:read",
        "nonce": None,
        "original_state": "client-state-1",
        "iat": str(int(time.time())),
    }
    payload.update(overrides)
    key = authorize._derive_key(_SECRET, "approval")
    return authorize._sign_payload(payload, key, purpose="approval")


# ---------- the approval-pending page ----------


def test_pending_client_page_has_poll_and_resume_wiring(client: TestClient) -> None:
    view = _client_view(approval_status="pending", active=False)
    with patch.object(authorize, "OAuthClientService", return_value=_with_client_view(view)):
        resp = client.get("/authorize", params=_AUTHORIZE_PARAMS)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Cache-Control"] == "no-store"

    cfg = _page_config(resp.text)
    # Poll wiring: signed blob + status URL + interval.
    state_blob = cfg["state"]
    assert isinstance(state_blob, str) and "." in state_blob
    assert isinstance(cfg["status_url"], str)
    assert cfg["status_url"].startswith("/oauth/approval/status?st=")
    assert cfg["poll_ms"] == authorize._APPROVAL_POLL_INTERVAL_MS
    assert cfg["decision_url"] == "/oauth/approval/decision"
    assert cfg["token_key"] == "jentic-one.access_token"
    # The blob verifies under the approval purpose and binds the client.
    params = authorize._verify_payload(
        state_blob,
        authorize._derive_key(_SECRET, "approval"),
        purpose="approval",
        max_age=authorize.APPROVAL_STATE_MAX_AGE_SECONDS,
    )
    assert params["client_id"] == "oc_test"
    assert params["original_state"] == "client-state-1"


def test_page_resume_url_reruns_original_authorize_request(client: TestClient) -> None:
    view = _client_view(approval_status="pending", active=False)
    with patch.object(authorize, "OAuthClientService", return_value=_with_client_view(view)):
        resp = client.get("/authorize", params=_AUTHORIZE_PARAMS)

    cfg = _page_config(resp.text)
    resume_url = cfg["resume_url"]
    assert isinstance(resume_url, str)
    assert resume_url.startswith("/authorize?")
    query = parse_qs(urlsplit(resume_url).query)
    for key, value in _AUTHORIZE_PARAMS.items():
        assert query[key] == [value]


def test_resume_after_approval_proceeds_to_idp_redirect(client: TestClient) -> None:
    """The resume leg IS a plain /authorize re-run: once approved, it 302s to
    the IdP like any approved client."""
    pending = _client_view(approval_status="pending", active=False)
    with patch.object(authorize, "OAuthClientService", return_value=_with_client_view(pending)):
        page = client.get("/authorize", params=_AUTHORIZE_PARAMS)
    resume_url = _page_config(page.text)["resume_url"]
    assert isinstance(resume_url, str)

    approved = _client_view(approval_status="approved", active=True)
    with patch.object(authorize, "OAuthClientService", return_value=_with_client_view(approved)):
        resp = client.get(resume_url)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://idp.example.com/")


def test_page_admin_panel_hidden_and_anon_panel_present(client: TestClient) -> None:
    """The server renders the admin controls hidden; only page script may
    reveal them after /me confirms oauth-clients:write. The anonymous panel
    (ask-your-admin + copyable deep link) is the visible default."""
    view = _client_view(approval_status="pending", active=False)
    with patch.object(authorize, "OAuthClientService", return_value=_with_client_view(view)):
        resp = client.get("/authorize", params=_AUTHORIZE_PARAMS)

    assert '<div class="panel" id="admin-panel" hidden>' in resp.text
    assert '<div class="panel" id="anon-panel">' in resp.text
    # Deep link to the SPA approval queue, absolute on the canonical origin.
    assert 'value="https://auth.example.com/app/settings?tab=queue"' in resp.text
    # Admin-session detection + decision wiring live in the page script.
    assert "oauth-clients:write" in resp.text
    assert "/me" in resp.text


def test_page_deny_redirect_only_when_redirect_uri_registered(client: TestClient) -> None:
    registered = _client_view(approval_status="pending", active=False)
    with patch.object(authorize, "OAuthClientService", return_value=_with_client_view(registered)):
        resp = client.get("/authorize", params=_AUTHORIZE_PARAMS)
    deny_redirect = _page_config(resp.text)["deny_redirect"]
    assert isinstance(deny_redirect, str)
    assert deny_redirect.startswith("https://app.example.com/cb?")
    assert "error=access_denied" in deny_redirect
    assert "state=client-state-1" in deny_redirect

    unregistered = _client_view(
        approval_status="pending", active=False, redirect_uris=["https://other.example.com/cb"]
    )
    with patch.object(
        authorize, "OAuthClientService", return_value=_with_client_view(unregistered)
    ):
        resp = client.get("/authorize", params=_AUTHORIZE_PARAMS)
    assert _page_config(resp.text)["deny_redirect"] is None


def test_page_client_name_escaped_and_config_script_safe(client: TestClient) -> None:
    view = _client_view(approval_status="pending", active=False, name="<script>alert(1)</script>")
    with patch.object(authorize, "OAuthClientService", return_value=_with_client_view(view)):
        resp = client.get("/authorize", params=_AUTHORIZE_PARAMS)
    assert resp.status_code == 200
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text
    # The JSON config block never contains a raw '<' (client-influenced URLs
    # ride into it), so it cannot close its own script tag.
    marker = '<script id="approval-config" type="application/json">'
    start = resp.text.index(marker) + len(marker)
    end = resp.text.index("</script>", start)
    assert "<" not in resp.text[start:end]


# ---------- GET /oauth/approval/status ----------


def test_status_endpoint_returns_only_tri_state(client: TestClient) -> None:
    cases = [
        (_client_view(approval_status="pending", active=False), "pending"),
        (_client_view(approval_status="approved", active=True), "approved"),
        (_client_view(approval_status="denied", active=False), "denied"),
        # Approved-but-deactivated (the kill switch): never announced ready.
        (_client_view(approval_status="approved", active=False), "pending"),
        # Vanished row: no deletion oracle — reads as pending.
        (None, "pending"),
    ]
    for view, expected in cases:
        with patch.object(authorize, "OAuthClientService", return_value=_with_client_view(view)):
            resp = client.get("/oauth/approval/status", params={"st": _mint_state()})
        assert resp.status_code == 200
        # The tri-state is the ENTIRE body — no names, URIs, or metadata.
        assert resp.json() == {"status": expected}
        assert resp.headers["Cache-Control"] == "no-store"


def test_status_endpoint_rejects_bad_signature(client: TestClient) -> None:
    good = _mint_state()
    tampered = good[:-1] + ("a" if good[-1] != "a" else "b")
    resp = client.get("/oauth/approval/status", params={"st": tampered})
    assert resp.status_code == 400
    assert resp.json()["type"] == "invalid_grant"


def test_status_endpoint_rejects_expired_state(client: TestClient) -> None:
    expired = _mint_state(iat=str(int(time.time()) - authorize.APPROVAL_STATE_MAX_AGE_SECONDS - 1))
    resp = client.get("/oauth/approval/status", params={"st": expired})
    assert resp.status_code == 400


def test_status_endpoint_rejects_wrong_purpose_blob(client: TestClient) -> None:
    """A callback-leg 'state' blob must not open the status endpoint (and vice
    versa): distinct derived key AND purpose discriminator."""
    callback_state = authorize._sign_payload(
        {"client_id": "oc_test", "iat": str(int(time.time()))},
        authorize._derive_key(_SECRET, "state"),
        purpose="state",
    )
    resp = client.get("/oauth/approval/status", params={"st": callback_state})
    assert resp.status_code == 400


def test_status_endpoint_rejects_malformed_blob(client: TestClient) -> None:
    resp = client.get("/oauth/approval/status", params={"st": "no-dot-here"})
    assert resp.status_code == 400


def test_status_endpoint_rate_limited_in_own_bucket(app: FastAPI) -> None:
    """The poll endpoint 429s past its burst — and the /authorize bucket is
    untouched (own namespace)."""
    app.state.ctx.config.auth.oauth_rate_limit.approval_status_rpm = 1
    app.state.ctx.config.auth.oauth_rate_limit.approval_status_burst = 2
    client = TestClient(app, follow_redirects=False)

    view = _client_view(approval_status="pending", active=False)
    with patch.object(authorize, "OAuthClientService", return_value=_with_client_view(view)):
        st = _mint_state()
        statuses = [
            client.get("/oauth/approval/status", params={"st": st}).status_code for _ in range(3)
        ]
        assert statuses[:2] == [200, 200]
        limited = client.get("/oauth/approval/status", params={"st": st})
        assert limited.status_code == 429
        assert "Retry-After" in limited.headers
        # /authorize still answers: polling drained only its own bucket.
        page = client.get("/authorize", params=_AUTHORIZE_PARAMS)
        assert page.status_code == 200


# ---------- POST /oauth/approval/decision ----------


def _identity(permissions: list[str]) -> Identity:
    return Identity(sub="usr_admin", email="admin@example.com", permissions=permissions)


def test_decision_endpoint_rejects_anonymous(app: FastAPI) -> None:
    """No SPA bearer token → 401. The page's anonymous panel never reaches
    this endpoint; a cross-site form has no token to present (CSRF posture)."""

    async def _no_verifier(token: str, request: object) -> Identity:
        raise AssertionError("must not be called without a credential")

    app.state.verify_token = _no_verifier
    client = TestClient(app, follow_redirects=False)
    resp = client.post(
        "/oauth/approval/decision", json={"state": _mint_state(), "action": "approve"}
    )
    assert resp.status_code == 401


def test_decision_endpoint_rejects_non_admin(app: FastAPI) -> None:
    app.dependency_overrides[resolve_identity] = lambda: _identity(["agents:read"])
    client = TestClient(app, follow_redirects=False)
    resp = client.post(
        "/oauth/approval/decision", json={"state": _mint_state(), "action": "approve"}
    )
    assert resp.status_code == 403


def test_decision_endpoint_approve_reuses_admin_service_path(app: FastAPI) -> None:
    """The inline approve is the SAME OAuthClientService.approve call (by the
    row's internal id, with the caller's identity) as the admin route."""
    app.dependency_overrides[resolve_identity] = lambda: _identity(["oauth-clients:write"])
    client = TestClient(app, follow_redirects=False)

    pending = _client_view(approval_status="pending", active=False)
    approved = _client_view(approval_status="approved", active=True)
    mock_svc = _with_client_view(pending)
    mock_svc.approve = AsyncMock(return_value=approved)
    with patch.object(authorize, "OAuthClientService", return_value=mock_svc):
        resp = client.post(
            "/oauth/approval/decision", json={"state": _mint_state(), "action": "approve"}
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "approved"}
    mock_svc.approve.assert_awaited_once()
    args, kwargs = mock_svc.approve.await_args
    assert args == ("oac_1",)
    assert kwargs["identity"].sub == "usr_admin"


def test_decision_endpoint_deny_returns_denied(app: FastAPI) -> None:
    app.dependency_overrides[resolve_identity] = lambda: _identity(["oauth-clients:write"])
    client = TestClient(app, follow_redirects=False)

    pending = _client_view(approval_status="pending", active=False)
    denied = _client_view(approval_status="denied", active=False)
    mock_svc = _with_client_view(pending)
    mock_svc.deny = AsyncMock(return_value=denied)
    with patch.object(authorize, "OAuthClientService", return_value=mock_svc):
        resp = client.post(
            "/oauth/approval/decision", json={"state": _mint_state(), "action": "deny"}
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "denied"}
    mock_svc.deny.assert_awaited_once()


def test_decision_endpoint_rejects_bad_state_even_for_admin(app: FastAPI) -> None:
    app.dependency_overrides[resolve_identity] = lambda: _identity(["oauth-clients:write"])
    client = TestClient(app, follow_redirects=False)
    resp = client.post(
        "/oauth/approval/decision", json={"state": "garbage.sig", "action": "approve"}
    )
    assert resp.status_code == 400


def test_decision_endpoint_rejects_unknown_action(app: FastAPI) -> None:
    app.dependency_overrides[resolve_identity] = lambda: _identity(["oauth-clients:write"])
    client = TestClient(app, follow_redirects=False)
    resp = client.post(
        "/oauth/approval/decision", json={"state": _mint_state(), "action": "revoke"}
    )
    assert resp.status_code == 422
