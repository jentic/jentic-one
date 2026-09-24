"""Unit tests for the local-account login form on the /authorize flow (#1276).

Covers the ``auth.local_login`` gate (framework 404 when off), the signed
carry-through token (``ls``) verification, the single-use CSRF nonce
(mint → consume → replay), the generic-failure posture (no user-enumeration
oracle), and both rejoin arms: platform client → direct code issuance,
registered third-party client → consent handle carrying ``local_user_id``.

Service and DB boundaries are mocked (``AuthService.authenticate``, the
authorize service, the cached OAuth-client lookup); the full round trip
against real databases lives in
``tests/integration/auth/test_local_login_flow.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from jentic_one.admin.services.errors import AccountLockedError, InvalidCredentialsError
from jentic_one.auth.services.errors import AuthServiceError, InvalidGrantError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.flow import (
    approval_state_key,
    login_signing_key,
    sign_payload,
    state_signing_key,
    verify_payload,
)
from jentic_one.auth.web.routers import authorize, local_login
from jentic_one.shared.config import AuthConfig, LocalLoginConfig, PlatformClientConfig
from jentic_one.shared.state.backend import MemoryStateBackend

JWT_SECRET = "unit-test-jwt-secret"
PLATFORM_CLIENT_ID = "platform-app"
PLATFORM_REDIRECT = "https://app.example.com/cb"
THIRD_PARTY_CLIENT_ID = "oc_third_party"
THIRD_PARTY_REDIRECT = "https://mcpapp.example.com/cb"


def _make_ctx(*, local_login_enabled: bool = True, idp_enabled: bool = False) -> MagicMock:
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(
        local_login=LocalLoginConfig(enabled=local_login_enabled),
        platform_clients=[
            PlatformClientConfig(client_id=PLATFORM_CLIENT_ID, redirect_uris=[PLATFORM_REDIRECT])
        ],
    )
    ctx.config.auth.idp.enabled = idp_enabled
    ctx.config.admin.auth.jwt_secret = SecretStr(JWT_SECRET)
    return ctx


def _make_app(ctx: MagicMock) -> FastAPI:
    app = FastAPI()
    app.include_router(authorize.router)
    app.include_router(local_login.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.state.ctx = ctx
    app.state.auth_state_backend = MemoryStateBackend()
    return app


def _signed_state(
    ctx: MagicMock,
    *,
    client_id: str = PLATFORM_CLIENT_ID,
    redirect_uri: str = PLATFORM_REDIRECT,
    iat_offset: int = 0,
    key: str | None = None,
    purpose: str = "login",
    original_state: str | None = "client-state-1",
) -> str:
    payload: dict[str, str | None] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": "challenge-abc",
        "scope": "openid apis:read",
        "nonce": None,
        "original_state": original_state,
        "iat": str(int(time.time()) + iat_offset),
    }
    return sign_payload(payload, key or login_signing_key(ctx), purpose=purpose)


def _extract_form_fields(html: str) -> dict[str, str]:
    """Pull the hidden ls/csrf values out of the rendered form."""
    fields: dict[str, str] = {}
    for name in ("ls", "csrf"):
        marker = f'name="{name}" value="'
        start = html.index(marker) + len(marker)
        fields[name] = html[start : html.index('"', start)]
    return fields


@pytest.fixture()
def ctx() -> MagicMock:
    return _make_ctx()


@pytest.fixture()
def client(ctx: MagicMock) -> TestClient:
    return TestClient(_make_app(ctx))


# ---------------------------------------------------------------------------
# The config gate.


def test_local_login_config_defaults_off() -> None:
    """The gate defaults off: stock deployments ship byte-identical behaviour."""
    assert AuthConfig().local_login.enabled is False


def test_gate_off_both_routes_answer_framework_404() -> None:
    """Disabled → the framework's plain route-not-found body, on a full route
    match, before any dependency (rate limiter) or template runs."""
    disabled = TestClient(_make_app(_make_ctx(local_login_enabled=False)))
    for resp in (
        disabled.get("/login", params={"ls": "anything"}),
        disabled.post("/login", data={"email": "e", "password": "p", "ls": "x", "csrf": "y"}),
    ):
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Not Found"}


def test_gate_off_authorize_error_unchanged() -> None:
    """Gate off + no IdP → /authorize keeps the exact server_error redirect."""
    disabled_ctx = _make_ctx(local_login_enabled=False)
    app = _make_app(disabled_ctx)
    svc = MagicMock()
    svc.get_authorize_redirect_url.return_value = None
    app.dependency_overrides[authorize.get_authorize_service] = lambda: svc
    resp = TestClient(app).get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": PLATFORM_CLIENT_ID,
            "redirect_uri": PLATFORM_REDIRECT,
            "code_challenge": "challenge-abc",
            "code_challenge_method": "S256",
            "state": "s1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    query = parse_qs(urlsplit(resp.headers["location"]).query)
    assert query["error"] == ["server_error"]
    assert query["error_description"] == ["no identity provider configured"]


def test_gate_on_authorize_redirects_to_login_form(ctx: MagicMock) -> None:
    """Gate on + no IdP → /authorize 302s to /login with the signed state."""
    app = _make_app(ctx)
    svc = MagicMock()
    svc.get_authorize_redirect_url.return_value = None
    app.dependency_overrides[authorize.get_authorize_service] = lambda: svc
    resp = TestClient(app).get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": PLATFORM_CLIENT_ID,
            "redirect_uri": PLATFORM_REDIRECT,
            "code_challenge": "challenge-abc",
            "code_challenge_method": "S256",
            "state": "s1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("/login?ls=")
    ls = parse_qs(urlsplit(location).query)["ls"][0]
    # The carry-through token is minted under the DEDICATED login purpose —
    # not the IdP-leg state purpose (MAJOR-1 of the adversarial review).
    params = verify_payload(ls, login_signing_key(ctx), purpose="login", max_age=600)
    assert params["client_id"] == PLATFORM_CLIENT_ID
    assert params["original_state"] == "s1"
    with pytest.raises(InvalidGrantError):
        verify_payload(ls, state_signing_key(ctx), purpose="state", max_age=600)


def test_gate_on_idp_still_wins(ctx: MagicMock) -> None:
    """An external IdP always wins: the login form is never offered."""
    app = _make_app(ctx)
    svc = MagicMock()
    svc.get_authorize_redirect_url.return_value = "https://idp.example.com/authorize?x=1"
    app.dependency_overrides[authorize.get_authorize_service] = lambda: svc
    resp = TestClient(app).get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": PLATFORM_CLIENT_ID,
            "redirect_uri": PLATFORM_REDIRECT,
            "code_challenge": "challenge-abc",
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://idp.example.com/authorize")


def test_idp_on_both_login_routes_answer_framework_404() -> None:
    """IdP configured (`auth.idp.enabled=true`) + gate on → /login is 404,
    framework-plain: IdP wins even for someone holding a valid login token
    minted around the dispatch (MAJOR-1b)."""
    idp_ctx = _make_ctx(local_login_enabled=True, idp_enabled=True)
    client = TestClient(_make_app(idp_ctx))
    valid_ls = _signed_state(idp_ctx)
    for resp in (
        client.get("/login", params={"ls": valid_ls}),
        client.post("/login", data={"email": "e@x", "password": "p", "ls": valid_ls, "csrf": "c"}),
    ):
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Not Found"}


def test_idp_on_redirect_state_not_accepted_at_login() -> None:
    """IdP on + gate on: the internal state the IdP redirect hands the browser
    (as its `state=` query param) opens nothing at /login — the route is 404,
    and even with the route live the purpose split would reject it."""
    idp_ctx = _make_ctx(local_login_enabled=True, idp_enabled=True)
    app = _make_app(idp_ctx)
    svc = MagicMock()
    svc.get_authorize_redirect_url.return_value = "https://idp.example.com/authorize?x=1"
    app.dependency_overrides[authorize.get_authorize_service] = lambda: svc
    client = TestClient(app)
    resp = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": PLATFORM_CLIENT_ID,
            "redirect_uri": PLATFORM_REDIRECT,
            "code_challenge": "challenge-abc",
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://idp.example.com/")
    # The state the IdP leg carries is exactly what the authorize service was
    # handed — the attacker-observable artifact of MAJOR-1's scenario.
    idp_state = svc.get_authorize_redirect_url.call_args.kwargs["state"]
    refused = client.get("/login", params={"ls": idp_state}, follow_redirects=False)
    assert refused.status_code == 404
    assert refused.json() == {"detail": "Not Found"}


def test_purpose_mutual_rejection_all_directions(ctx: MagicMock, client: TestClient) -> None:
    """The three purposes (state / approval / login) reject each other at
    every verifying endpoint: an IdP-leg state or an approval blob never opens
    the login form, and a login token never opens the callback or the
    approval poll."""
    # state-purpose and approval-purpose tokens at GET /login → refused.
    for wrong in (
        _signed_state(ctx, key=state_signing_key(ctx), purpose="state"),
        _signed_state(ctx, key=approval_state_key(ctx), purpose="approval"),
    ):
        resp = client.get("/login", params={"ls": wrong}, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/error?error=invalid_state"

    # login-purpose token at the IdP callback → invalid_state.
    login_token = _signed_state(ctx)
    resp = client.get(
        "/oauth/callback",
        params={"code": "upstream-code", "state": login_token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_state"

    # login-purpose token at the approval-status poll → 400.
    resp = client.get("/oauth/approval/status", params={"st": login_token})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /login — the form.


def test_login_page_renders_with_csrf_and_state(ctx: MagicMock, client: TestClient) -> None:
    ls = _signed_state(ctx)
    resp = client.get("/login", params={"ls": ls})
    assert resp.status_code == 200
    fields = _extract_form_fields(resp.text)
    assert fields["ls"] == ls
    assert len(fields["csrf"]) >= 32
    # Same security-header posture as the consent page.
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Cache-Control"] == "no-store"


def test_login_page_rejects_forged_state(ctx: MagicMock, client: TestClient) -> None:
    forged = _signed_state(ctx, key="wrong-secret")
    resp = client.get("/login", params={"ls": forged}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_state"


def test_login_page_rejects_expired_state(ctx: MagicMock, client: TestClient) -> None:
    expired = _signed_state(ctx, iat_offset=-601)
    resp = client.get("/login", params={"ls": expired}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_state"


# ---------------------------------------------------------------------------
# POST /login — state, CSRF, credentials.


def _get_form(ctx: MagicMock, client: TestClient, **state_kwargs: Any) -> dict[str, str]:
    ls = _signed_state(ctx, **state_kwargs)
    resp = client.get("/login", params={"ls": ls})
    assert resp.status_code == 200
    return _extract_form_fields(resp.text)


def test_submit_rejects_invalid_state(client: TestClient) -> None:
    resp = client.post(
        "/login",
        data={"email": "u@test.local", "password": "pw", "ls": "garbage", "csrf": "x"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_state"


def test_submit_rejects_unminted_csrf(ctx: MagicMock, client: TestClient) -> None:
    """A nonce the server never minted (or that expired) re-renders — the
    credential check never runs."""
    ls = _signed_state(ctx)
    with patch.object(local_login, "AuthService") as svc_cls:
        resp = client.post(
            "/login",
            data={"email": "u@test.local", "password": "pw", "ls": ls, "csrf": "never-minted"},
        )
    assert resp.status_code == 200
    assert "The form expired" in resp.text
    svc_cls.assert_not_called()


def test_submit_csrf_nonce_is_single_use(ctx: MagicMock, client: TestClient) -> None:
    """First submit consumes the nonce; a replay re-renders without touching
    the credential path again."""
    fields = _get_form(ctx, client)
    auth = MagicMock()
    auth.authenticate = AsyncMock(side_effect=InvalidCredentialsError())
    with patch.object(local_login, "AuthService", return_value=auth):
        first = client.post(
            "/login",
            data={"email": "u@test.local", "password": "wrong", **fields},
        )
        replay = client.post(
            "/login",
            data={"email": "u@test.local", "password": "wrong", **fields},
        )
    assert first.status_code == 200
    assert "Invalid email or password." in first.text
    assert replay.status_code == 200
    assert "The form expired" in replay.text
    auth.authenticate.assert_awaited_once()


@pytest.mark.parametrize("error", [InvalidCredentialsError(), AccountLockedError("usr_x")])
def test_submit_failure_is_one_generic_message(
    ctx: MagicMock, client: TestClient, error: Exception
) -> None:
    """Wrong password and locked account render the same message (no oracle),
    with a fresh nonce so the user can retry."""
    fields = _get_form(ctx, client)
    auth = MagicMock()
    auth.authenticate = AsyncMock(side_effect=error)
    with patch.object(local_login, "AuthService", return_value=auth):
        resp = client.post(
            "/login",
            data={"email": "u@test.local", "password": "bad", **fields},
        )
    assert resp.status_code == 200
    assert "Invalid email or password." in resp.text
    fresh = _extract_form_fields(resp.text)
    assert fresh["csrf"] != fields["csrf"]
    assert fresh["ls"] == fields["ls"]


def _auth_ok(user_id: str, *, rotation_required: bool = False) -> MagicMock:
    """An AuthService mock: successful credential check, configurable rotation flag."""
    auth = MagicMock()
    auth.authenticate = AsyncMock(return_value=user_id)
    auth.password_rotation_required = AsyncMock(return_value=rotation_required)
    return auth


def test_submit_platform_client_issues_code_directly(ctx: MagicMock) -> None:
    """Platform client → consent-skip: code minted and 302 straight back to
    the client redirect with the original state. No JWT anywhere."""
    app = _make_app(ctx)
    authorize_svc = MagicMock()
    authorize_svc.issue_authorization_code = AsyncMock(return_value="authcode-123")
    app.dependency_overrides[authorize.get_authorize_service] = lambda: authorize_svc
    client = TestClient(app)
    fields = _get_form(ctx, client)

    with patch.object(local_login, "AuthService", return_value=_auth_ok("usr_local_1")):
        resp = client.post(
            "/login",
            data={"email": "u@test.local", "password": "correct", **fields},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(f"{PLATFORM_REDIRECT}?")
    query = parse_qs(urlsplit(location).query)
    assert query["code"] == ["authcode-123"]
    assert query["state"] == ["client-state-1"]
    authorize_svc.issue_authorization_code.assert_awaited_once_with(
        user_id="usr_local_1",
        client_id=PLATFORM_CLIENT_ID,
        redirect_uri=PLATFORM_REDIRECT,
        code_challenge="challenge-abc",
        scopes="openid apis:read",
        nonce=None,
    )


def _third_party_view(**overrides: Any) -> MagicMock:
    view = MagicMock()
    view.active = overrides.get("active", True)
    view.approval_status = overrides.get("approval_status", "approved")
    view.name = "Third-Party App"
    view.description = "An app"
    return view


def test_submit_third_party_writes_consent_handle(ctx: MagicMock) -> None:
    """Registered third-party client → the same consent handle the IdP
    callback writes, carrying local_user_id instead of claims."""
    app = _make_app(ctx)
    client = TestClient(app)

    lookup = AsyncMock(return_value=_third_party_view())
    with (
        patch.object(local_login, "AuthService", return_value=_auth_ok("usr_local_2")),
        patch.object(local_login, "get_cached_oauth_client", lookup),
    ):
        fields = _get_form(
            ctx, client, client_id=THIRD_PARTY_CLIENT_ID, redirect_uri=THIRD_PARTY_REDIRECT
        )
        resp = client.post(
            "/login",
            data={"email": "third@test.local", "password": "correct", **fields},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("/oauth/consent?ch=")
    handle = parse_qs(urlsplit(location).query)["ch"][0]

    backend = app.state.auth_state_backend
    raw = asyncio.run(backend.get(f"consent-handle:{handle}"))
    assert raw is not None
    stored = json.loads(raw)
    assert stored["local_user_id"] == "usr_local_2"
    assert stored["user_email"] == "third@test.local"
    assert stored["client_id"] == THIRD_PARTY_CLIENT_ID
    assert "claims" not in stored


def test_submit_third_party_midflow_gate_recheck(ctx: MagicMock) -> None:
    """A client denied while the user is at the login form must not reach
    consent — same D7 mid-flow re-check as the IdP callback."""
    app = _make_app(ctx)
    client = TestClient(app)
    auth = _auth_ok("usr_local_3")

    approved_lookup = AsyncMock(return_value=_third_party_view())
    with patch.object(local_login, "get_cached_oauth_client", approved_lookup):
        fields = _get_form(
            ctx, client, client_id=THIRD_PARTY_CLIENT_ID, redirect_uri=THIRD_PARTY_REDIRECT
        )

    denied_lookup = AsyncMock(return_value=_third_party_view(approval_status="denied"))
    with (
        patch.object(local_login, "AuthService", return_value=auth),
        patch.object(local_login, "get_cached_oauth_client", denied_lookup),
    ):
        resp = client.post(
            "/login",
            data={"email": "u@test.local", "password": "correct", **fields},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=access_denied"
    # The gate runs before the credential check ever matters for the rejoin;
    # no consent handle may exist.
    auth.authenticate.assert_not_awaited()


def test_login_page_midflow_gate_recheck(ctx: MagicMock, client: TestClient) -> None:
    """GET /login re-checks the D7 gate too: a client denied while the user
    holds the ls never gets a rendered credential form (MINOR-2)."""
    ls = _signed_state(ctx, client_id=THIRD_PARTY_CLIENT_ID, redirect_uri=THIRD_PARTY_REDIRECT)
    denied_lookup = AsyncMock(return_value=_third_party_view(approval_status="denied"))
    with patch.object(local_login, "get_cached_oauth_client", denied_lookup):
        resp = client.get("/login", params={"ls": ls}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=access_denied"


def test_submit_rotation_required_blocks_rejoin(ctx: MagicMock) -> None:
    """A must_change_password account authenticates but cannot complete the
    flow: explicit post-auth message, no code minted, ls left unburned so the
    user can rotate elsewhere and retry the SAME flow (MEDIUM-1)."""
    app = _make_app(ctx)
    authorize_svc = MagicMock()
    authorize_svc.issue_authorization_code = AsyncMock(return_value="authcode-should-not-mint")
    app.dependency_overrides[authorize.get_authorize_service] = lambda: authorize_svc
    client = TestClient(app)
    fields = _get_form(ctx, client)

    rotation = _auth_ok("usr_rotate", rotation_required=True)
    with patch.object(local_login, "AuthService", return_value=rotation):
        resp = client.post(
            "/login",
            data={"email": "rotate@test.local", "password": "correct", **fields},
        )
    assert resp.status_code == 200
    assert "password must be changed" in resp.text
    authorize_svc.issue_authorization_code.assert_not_awaited()

    # Flag cleared → the SAME ls (not burned) completes with the re-rendered
    # form's fresh nonce.
    fresh = _extract_form_fields(resp.text)
    assert fresh["ls"] == fields["ls"]
    with patch.object(local_login, "AuthService", return_value=_auth_ok("usr_rotate")):
        done = client.post(
            "/login",
            data={"email": "rotate@test.local", "password": "correct", **fresh},
            follow_redirects=False,
        )
    assert done.status_code == 302
    assert done.headers["location"].startswith(f"{PLATFORM_REDIRECT}?")


def test_ls_single_use_on_success(ctx: MagicMock) -> None:
    """A successful submit burns the ls: replaying it at GET or POST within
    its TTL is refused (MINOR-3)."""
    app = _make_app(ctx)
    authorize_svc = MagicMock()
    authorize_svc.issue_authorization_code = AsyncMock(return_value="authcode-1")
    app.dependency_overrides[authorize.get_authorize_service] = lambda: authorize_svc
    client = TestClient(app)
    fields = _get_form(ctx, client)

    with patch.object(local_login, "AuthService", return_value=_auth_ok("usr_1")):
        done = client.post(
            "/login",
            data={"email": "u@test.local", "password": "correct", **fields},
            follow_redirects=False,
        )
        assert done.status_code == 302

        replay_get = client.get("/login", params={"ls": fields["ls"]}, follow_redirects=False)
        assert replay_get.status_code == 302
        assert replay_get.headers["location"] == "/error?error=invalid_state"

        fresh_nonce = asyncio.run(_mint_nonce_for(app, fields["ls"]))
        replay_post = client.post(
            "/login",
            data={
                "email": "u@test.local",
                "password": "correct",
                "ls": fields["ls"],
                "csrf": fresh_nonce,
            },
            follow_redirects=False,
        )
        assert replay_post.status_code == 302
        assert replay_post.headers["location"] == "/error?error=invalid_state"
    # Only the first submit minted a code.
    authorize_svc.issue_authorization_code.assert_awaited_once()


async def _mint_nonce_for(app: FastAPI, ls: str) -> str:
    """Mint a server-side nonce bound to ``ls`` directly against the backend."""
    nonce = secrets.token_urlsafe(32)
    await app.state.auth_state_backend.set(
        f"login-csrf:{nonce}",
        hashlib.sha256(ls.encode()).hexdigest().encode(),
        ttl_s=300.0,
    )
    return nonce


def test_failure_does_not_burn_ls(ctx: MagicMock, client: TestClient) -> None:
    """A failed credential check leaves the ls valid: the user retries a
    typo'd password on the same page and completes (MINOR-3's failure arm)."""
    fields = _get_form(ctx, client)
    failing = MagicMock()
    failing.authenticate = AsyncMock(side_effect=InvalidCredentialsError())
    with patch.object(local_login, "AuthService", return_value=failing):
        first = client.post("/login", data={"email": "u@test.local", "password": "typo", **fields})
    assert first.status_code == 200
    fresh = _extract_form_fields(first.text)
    assert fresh["ls"] == fields["ls"]

    with patch.object(local_login, "AuthService", return_value=_auth_ok("usr_retry")):
        done = client.post(
            "/login",
            data={"email": "u@test.local", "password": "correct", **fresh},
            follow_redirects=False,
        )
    assert done.status_code == 302


def test_csrf_nonce_bound_to_its_ls(ctx: MagicMock, client: TestClient) -> None:
    """A nonce harvested from one flow cannot satisfy another: the stored
    value binds it to the ls it was rendered for (MINOR-1)."""
    attacker_fields = _get_form(ctx, client)
    victim_ls = _signed_state(ctx, original_state="victim-state")
    assert victim_ls != attacker_fields["ls"]

    auth = MagicMock()
    with patch.object(local_login, "AuthService", return_value=auth) as svc_cls:
        resp = client.post(
            "/login",
            data={
                "email": "victim@test.local",
                "password": "pw",
                "ls": victim_ls,
                "csrf": attacker_fields["csrf"],
            },
        )
    assert resp.status_code == 200
    assert "The form expired" in resp.text
    svc_cls.assert_not_called()


def test_login_form_escapes_email_echo(ctx: MagicMock, client: TestClient) -> None:
    """The re-rendered form HTML-escapes the echoed email (XSS)."""
    fields = _get_form(ctx, client)
    auth = MagicMock()
    auth.authenticate = AsyncMock(side_effect=InvalidCredentialsError())
    with patch.object(local_login, "AuthService", return_value=auth):
        resp = client.post(
            "/login",
            data={"email": '"><script>alert(1)</script>', "password": "x", **fields},
        )
    assert resp.status_code == 200
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text
