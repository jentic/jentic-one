"""Unit tests for identity-ladder rung 1: platform-session reuse (#1299).

Covers the ``POST /oauth/session/continue`` exchange (auth required, D7
re-check, generic rejection, resume URL shape), the ``sc`` resume arm on
``GET /authorize`` (skip to consent / direct code, single-use burn, TTL /
tamper / flow-binding fall-through), the four-way purpose matrix, and the
button-not-silent posture of the login page's continuation offer.

Service and DB boundaries are mocked (identity via the shared
``resolve_identity`` override, the user lookup, the cached OAuth-client
lookup); the full round trip against real databases lives in
``tests/integration/auth/test_session_continue_flow.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from jentic_one.admin.services.errors import UserNotFoundError
from jentic_one.auth.services.errors import AuthServiceError, InvalidGrantError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.flow import (
    approval_state_key,
    login_signing_key,
    session_signing_key,
    sign_payload,
    state_signing_key,
    verify_payload,
)
from jentic_one.auth.web.routers import authorize, local_login
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, LocalLoginConfig, PlatformClientConfig
from jentic_one.shared.models import ActorType
from jentic_one.shared.state.backend import MemoryStateBackend
from jentic_one.shared.web.deps import resolve_identity

JWT_SECRET = "unit-test-jwt-secret"  # pragma: allowlist secret
PLATFORM_CLIENT_ID = "platform-app"
PLATFORM_REDIRECT = "https://app.example.com/cb"
THIRD_PARTY_CLIENT_ID = "oc_third_party"
THIRD_PARTY_REDIRECT = "https://mcpapp.example.com/cb"
USER_ID = "usr_session_1"
USER_EMAIL = "session@test.local"


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


def _fake_identity(sub: str = USER_ID, actor_type: ActorType = ActorType.USER) -> Identity:
    return Identity(sub=sub, email=USER_EMAIL, actor_type=actor_type)


def _make_app(ctx: MagicMock, identity: Identity | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(authorize.router)
    app.include_router(local_login.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.state.ctx = ctx
    app.state.auth_state_backend = MemoryStateBackend()
    if identity is not None:

        async def _fake() -> Identity:
            return identity

        app.dependency_overrides[resolve_identity] = _fake
    return app


def _login_state(
    ctx: MagicMock,
    *,
    client_id: str = PLATFORM_CLIENT_ID,
    redirect_uri: str = PLATFORM_REDIRECT,
    iat_offset: int = 0,
    key: str | None = None,
    purpose: str = "login",
) -> str:
    payload: dict[str, str | None] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": "challenge-abc",
        "scope": "openid apis:read",
        "nonce": None,
        "original_state": "client-state-1",
        "iat": str(int(time.time()) + iat_offset),
    }
    return sign_payload(payload, key or login_signing_key(ctx), purpose=purpose)


def _session_blob(
    ctx: MagicMock,
    *,
    client_id: str = PLATFORM_CLIENT_ID,
    redirect_uri: str = PLATFORM_REDIRECT,
    code_challenge: str = "challenge-abc",
    scope: str = "openid apis:read",
    nonce: str | None = None,
    original_state: str | None = "client-state-1",
    user_id: str = USER_ID,
    iat_offset: int = 0,
    key: str | None = None,
    purpose: str = "session",
) -> str:
    payload: dict[str, str | None] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "scope": scope,
        "nonce": nonce,
        "original_state": original_state,
        "user_id": user_id,
        "iat": str(int(time.time()) + iat_offset),
    }
    return sign_payload(payload, key or session_signing_key(ctx), purpose=purpose)


def _user_view(*, active: bool = True, must_change_password: bool = False) -> MagicMock:
    user = MagicMock()
    user.id = USER_ID
    user.email = USER_EMAIL
    user.active = active
    user.must_change_password = must_change_password
    return user


def _patch_redeem_user(user: MagicMock | Exception | None = None) -> Any:
    """Patch the redemption arm's live user-row read in ``authorize``."""
    svc_cls = MagicMock()
    if isinstance(user, Exception):
        svc_cls.return_value.get_by_id = AsyncMock(side_effect=user)
    else:
        svc_cls.return_value.get_by_id = AsyncMock(return_value=user or _user_view())
    return patch.object(authorize, "UserService", svc_cls)


def _third_party_view(**overrides: Any) -> MagicMock:
    view = MagicMock()
    view.active = overrides.get("active", True)
    view.approval_status = overrides.get("approval_status", "approved")
    view.name = "Third-Party App"
    view.description = "An app"
    view.redirect_uris = [THIRD_PARTY_REDIRECT]
    view.allowed_scopes = None
    view.consent_model = "user"
    return view


def _continue_call(client: TestClient, state: str) -> Any:
    return client.post(
        "/oauth/session/continue",
        json={"state": state},
        headers={"Authorization": "Bearer spa-token"},
    )


@pytest.fixture()
def ctx() -> MagicMock:
    return _make_ctx()


# ---------------------------------------------------------------------------
# POST /oauth/session/continue — the exchange.


def test_exchange_mints_session_continuation(ctx: MagicMock) -> None:
    """Happy path: valid ls + authenticated user → relative /authorize resume
    URL carrying a session-purpose blob pinning THIS caller's user id."""
    app = _make_app(ctx, identity=_fake_identity())
    client = TestClient(app)
    ls = _login_state(ctx)
    with patch.object(local_login, "UserService") as svc_cls:
        svc_cls.return_value.get_by_id = AsyncMock(return_value=_user_view())
        resp = _continue_call(client, ls)
    assert resp.status_code == 200
    redirect_url = resp.json()["redirect_url"]
    assert redirect_url.startswith("/authorize?")
    query = parse_qs(urlsplit(redirect_url).query)
    assert query["client_id"] == [PLATFORM_CLIENT_ID]
    assert query["state"] == ["client-state-1"]
    sc = query["sc"][0]
    payload = verify_payload(sc, session_signing_key(ctx), purpose="session", max_age=60)
    assert payload["user_id"] == USER_ID
    # Only the opaque user id is pinned — the blob rides a GET query param,
    # so it must carry no PII (F2 on the #1300 review).
    assert "user_email" not in payload
    assert USER_EMAIL not in redirect_url
    # Pinned to the flow, not just the user.
    assert payload["code_challenge"] == "challenge-abc"
    assert resp.headers["Cache-Control"] == "no-store"


def test_exchange_requires_authentication(ctx: MagicMock) -> None:
    """No bearer token → 401 before any blob detail can leak."""
    app = _make_app(ctx)

    async def _reject(*args: Any, **kwargs: Any) -> Identity:
        raise AssertionError("verify_token must not be reached in this test")

    app.state.verify_token = _reject
    client = TestClient(app)
    resp = client.post("/oauth/session/continue", json={"state": _login_state(ctx)})
    assert resp.status_code == 401


def test_exchange_rejects_non_user_actors(ctx: MagicMock) -> None:
    """An agent token cannot continue a human flow (403 from the actor gate)."""
    app = _make_app(ctx, identity=_fake_identity(sub="agnt_1", actor_type=ActorType.AGENT))
    client = TestClient(app)
    resp = _continue_call(client, _login_state(ctx))
    assert resp.status_code == 403


def test_exchange_gated_404_when_local_login_off() -> None:
    """Gate off → the framework's plain route-not-found 404, same as /login."""
    disabled = _make_ctx(local_login_enabled=False)
    client = TestClient(_make_app(disabled, identity=_fake_identity()))
    resp = _continue_call(client, "anything")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


def test_exchange_gated_404_when_idp_configured() -> None:
    """IdP on → 404 (IdP always wins; its SSO session is the reuse story)."""
    idp_ctx = _make_ctx(local_login_enabled=True, idp_enabled=True)
    client = TestClient(_make_app(idp_ctx, identity=_fake_identity()))
    resp = _continue_call(client, _login_state(idp_ctx))
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "bad_state_kwargs",
    [
        {"key": "wrong-secret"},  # tampered / foreign signature
        {"iat_offset": -601},  # expired
        {"key": None, "purpose": "state"},  # wrong purpose (needs state key too)
    ],
)
def test_exchange_rejects_bad_blobs_generically(
    ctx: MagicMock, bad_state_kwargs: dict[str, Any]
) -> None:
    """Tampered, expired, or wrong-purpose input → one generic 400; the body
    never names the client or the flow."""
    if bad_state_kwargs.get("purpose") == "state":
        bad_state_kwargs["key"] = state_signing_key(ctx)
    app = _make_app(ctx, identity=_fake_identity())
    client = TestClient(app)
    with patch.object(local_login, "UserService") as svc_cls:
        svc_cls.return_value.get_by_id = AsyncMock(return_value=_user_view())
        resp = _continue_call(client, _login_state(ctx, **bad_state_kwargs))
    assert resp.status_code == 400
    body = resp.text
    assert "session continuation rejected" in body
    assert PLATFORM_CLIENT_ID not in body
    assert PLATFORM_REDIRECT not in body


def test_exchange_rejects_garbage_blob_without_flow_info(ctx: MagicMock) -> None:
    """A caller with no valid blob learns nothing (mission: no client/flow
    info leak on invalid input)."""
    app = _make_app(ctx, identity=_fake_identity())
    client = TestClient(app)
    resp = _continue_call(client, "not-a-blob")
    assert resp.status_code == 400
    assert "session continuation rejected" in resp.text


def test_exchange_recheck_d7_gate(ctx: MagicMock) -> None:
    """A client denied while the user holds the ls → the SAME generic 400 as
    an invalid blob (D7 re-check at exchange, no denial oracle)."""
    app = _make_app(ctx, identity=_fake_identity())
    client = TestClient(app)
    ls = _login_state(ctx, client_id=THIRD_PARTY_CLIENT_ID, redirect_uri=THIRD_PARTY_REDIRECT)
    denied = AsyncMock(return_value=_third_party_view(approval_status="denied"))
    with patch.object(local_login, "get_cached_oauth_client", denied):
        resp = _continue_call(client, ls)
    assert resp.status_code == 400
    assert "session continuation rejected" in resp.text
    assert THIRD_PARTY_CLIENT_ID not in resp.text


def test_exchange_rejects_spent_ls(ctx: MagicMock) -> None:
    """An ls already burned by a successful password login cannot be
    exchanged (the flow completed)."""
    app = _make_app(ctx, identity=_fake_identity())
    client = TestClient(app)
    ls = _login_state(ctx)
    asyncio.run(
        app.state.auth_state_backend.set(
            f"login-ls-used:{hashlib.sha256(ls.encode()).hexdigest()}", b"1", ttl_s=600.0
        )
    )
    resp = _continue_call(client, ls)
    assert resp.status_code == 400
    assert "session continuation rejected" in resp.text


def test_exchange_rejects_inactive_user(ctx: MagicMock) -> None:
    """A disabled account cannot mint a continuation — same generic 400."""
    app = _make_app(ctx, identity=_fake_identity())
    client = TestClient(app)
    with patch.object(local_login, "UserService") as svc_cls:
        svc_cls.return_value.get_by_id = AsyncMock(return_value=_user_view(active=False))
        resp = _continue_call(client, _login_state(ctx))
    assert resp.status_code == 400
    assert "session continuation rejected" in resp.text


def test_exchange_rejects_password_fenced_user(ctx: MagicMock) -> None:
    """F1 (#1300 review): the rotation fence is a LIVE row read. A SPA token
    minted before an admin-forced reset passes the (claims-based) auth
    dependency, but the row's ``must_change_password`` must still 400 the
    exchange — same generic body as every other rejection."""
    app = _make_app(ctx, identity=_fake_identity())
    client = TestClient(app)
    with patch.object(local_login, "UserService") as svc_cls:
        svc_cls.return_value.get_by_id = AsyncMock(
            return_value=_user_view(must_change_password=True)
        )
        resp = _continue_call(client, _login_state(ctx))
    assert resp.status_code == 400
    assert "session continuation rejected" in resp.text


def test_exchange_works_again_once_fence_cleared(ctx: MagicMock) -> None:
    """Clearing the rotation flag un-fences the SAME token immediately (the
    fence is the row, not the claim)."""
    app = _make_app(ctx, identity=_fake_identity())
    client = TestClient(app)
    with patch.object(local_login, "UserService") as svc_cls:
        svc_cls.return_value.get_by_id = AsyncMock(
            return_value=_user_view(must_change_password=True)
        )
        assert _continue_call(client, _login_state(ctx)).status_code == 400
        svc_cls.return_value.get_by_id = AsyncMock(return_value=_user_view())
        assert _continue_call(client, _login_state(ctx)).status_code == 200


# ---------------------------------------------------------------------------
# GET /authorize — the sc resume arm.


def _authorize_app(ctx: MagicMock) -> tuple[FastAPI, MagicMock]:
    app = _make_app(ctx)
    svc = MagicMock()
    svc.get_authorize_redirect_url.return_value = None
    svc.issue_authorization_code = AsyncMock(return_value="authcode-sc-1")
    app.dependency_overrides[authorize.get_authorize_service] = lambda: svc
    return app, svc


def _authorize_call(
    client: TestClient,
    *,
    sc: str | None = None,
    client_id: str = PLATFORM_CLIENT_ID,
    redirect_uri: str = PLATFORM_REDIRECT,
) -> Any:
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": "challenge-abc",
        "code_challenge_method": "S256",
        "scope": "openid apis:read",
        "state": "client-state-1",
    }
    if sc is not None:
        params["sc"] = sc
    return client.get("/authorize", params=params, follow_redirects=False)


def test_resume_platform_client_issues_code_directly(ctx: MagicMock) -> None:
    """Valid sc + platform client → rungs 2/3 skipped, code minted for the
    PINNED user, 302 straight back to the client."""
    app, svc = _authorize_app(ctx)
    client = TestClient(app)
    with _patch_redeem_user():
        resp = _authorize_call(client, sc=_session_blob(ctx))
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(f"{PLATFORM_REDIRECT}?")
    query = parse_qs(urlsplit(location).query)
    assert query["code"] == ["authcode-sc-1"]
    assert query["state"] == ["client-state-1"]
    assert svc.issue_authorization_code.await_args.kwargs["user_id"] == USER_ID


def test_resume_third_party_lands_on_consent_with_pinned_identity(ctx: MagicMock) -> None:
    """Valid sc + registered client → the SAME consent handle shape the local
    login writes (local_user_id + user_email resolved from the ROW at
    redemption, never from the blob), then 302 to /oauth/consent."""
    app, _svc = _authorize_app(ctx)
    client = TestClient(app)
    sc = _session_blob(ctx, client_id=THIRD_PARTY_CLIENT_ID, redirect_uri=THIRD_PARTY_REDIRECT)
    lookup = AsyncMock(return_value=_third_party_view())
    with patch.object(authorize, "get_cached_oauth_client", lookup), _patch_redeem_user():
        resp = _authorize_call(
            client, sc=sc, client_id=THIRD_PARTY_CLIENT_ID, redirect_uri=THIRD_PARTY_REDIRECT
        )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("/oauth/consent?ch=")
    handle = parse_qs(urlsplit(location).query)["ch"][0]
    raw = asyncio.run(app.state.auth_state_backend.get(f"consent-handle:{handle}"))
    assert raw is not None
    stored = json.loads(raw)
    assert stored["local_user_id"] == USER_ID
    assert stored["user_email"] == USER_EMAIL
    assert "claims" not in stored


def test_resume_sc_is_single_use(ctx: MagicMock) -> None:
    """Replaying a redeemed sc falls through to the login form — it must not
    keep minting codes for the pinned user."""
    app, svc = _authorize_app(ctx)
    client = TestClient(app)
    sc = _session_blob(ctx)
    with _patch_redeem_user():
        first = _authorize_call(client, sc=sc)
        assert first.status_code == 302
        assert first.headers["location"].startswith(f"{PLATFORM_REDIRECT}?")
        replay = _authorize_call(client, sc=sc)
    assert replay.status_code == 302
    assert replay.headers["location"].startswith("/login?ls=")
    svc.issue_authorization_code.assert_awaited_once()


@pytest.mark.parametrize(
    "blob_kwargs",
    [
        {"key": "wrong-secret"},  # tampered
        {"iat_offset": -61},  # expired (60 s TTL)
        {"iat_offset": 120},  # future iat
        {"user_id": ""},  # no pinned user
        # Per-field splice: each of the six bound fields individually breaks
        # redemption when it differs from the current request (F4).
        {"client_id": "other-client"},
        {"redirect_uri": "https://evil.example.com/cb"},
        {"code_challenge": "other-challenge"},
        {"scope": "openid agents:write"},
        {"nonce": "unexpected-nonce"},
        {"original_state": "other-state"},
    ],
)
def test_resume_bad_sc_falls_through_to_login(ctx: MagicMock, blob_kwargs: dict[str, Any]) -> None:
    """Any verification or flow-binding failure leaves rungs 2/3 exactly as
    today: 302 to the login form, no code, no consent handle."""
    app, svc = _authorize_app(ctx)
    client = TestClient(app)
    resp = _authorize_call(client, sc=_session_blob(ctx, **blob_kwargs))
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?ls=")
    svc.issue_authorization_code.assert_not_awaited()


@pytest.mark.parametrize("row_outcome", ["inactive", "must_change_password", "not_found"])
def test_resume_recheck_user_row_at_redemption(ctx: MagicMock, row_outcome: str) -> None:
    """F2 (#1300 review): redemption re-reads the user row LIVE — an account
    deactivated, password-fenced, or deleted inside the blob's 60 s window
    falls through to the login form instead of redeeming."""
    app, svc = _authorize_app(ctx)
    client = TestClient(app)
    user: MagicMock | Exception
    if row_outcome == "inactive":
        user = _user_view(active=False)
    elif row_outcome == "must_change_password":
        user = _user_view(must_change_password=True)
    else:
        user = UserNotFoundError("gone")
    with _patch_redeem_user(user):
        resp = _authorize_call(client, sc=_session_blob(ctx))
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?ls=")
    svc.issue_authorization_code.assert_not_awaited()


def test_resume_ignored_when_local_login_off() -> None:
    """Gate off: even a well-signed sc changes nothing (two-sided gate — a
    mid-window config flip must not leave a redeemable continuation)."""
    disabled = _make_ctx(local_login_enabled=False)
    app, svc = _authorize_app(disabled)
    client = TestClient(app)
    resp = _authorize_call(client, sc=_session_blob(disabled))
    assert resp.status_code == 302
    query = parse_qs(urlsplit(resp.headers["location"]).query)
    assert query["error"] == ["server_error"]
    svc.issue_authorization_code.assert_not_awaited()


def test_resume_ignored_when_idp_enabled() -> None:
    """IdP configured: the sc is ignored and the IdP redirect wins."""
    idp_ctx = _make_ctx(local_login_enabled=True, idp_enabled=True)
    app, svc = _authorize_app(idp_ctx)
    svc.get_authorize_redirect_url.return_value = "https://idp.example.com/authorize?x=1"
    client = TestClient(app)
    resp = _authorize_call(client, sc=_session_blob(idp_ctx))
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://idp.example.com/")
    svc.issue_authorization_code.assert_not_awaited()


def test_no_sc_flow_unchanged(ctx: MagicMock) -> None:
    """Without a continuation the request is byte-identical to before: 302 to
    /login with a login-purpose ls."""
    app, _svc = _authorize_app(ctx)
    client = TestClient(app)
    resp = _authorize_call(client)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("/login?ls=")
    ls = parse_qs(urlsplit(location).query)["ls"][0]
    params = verify_payload(ls, login_signing_key(ctx), purpose="login", max_age=600)
    assert params["client_id"] == PLATFORM_CLIENT_ID


# ---------------------------------------------------------------------------
# Purpose matrix: the fourth purpose rejects and is rejected in ALL directions.


def test_session_purpose_rejected_at_every_other_endpoint(ctx: MagicMock) -> None:
    """A session continuation never opens the login form, the IdP callback,
    the approval poll, or the exchange itself."""
    app = _make_app(ctx, identity=_fake_identity())
    client = TestClient(app)
    session_blob = _session_blob(ctx)

    # GET /login → invalid_state redirect.
    resp = client.get("/login", params={"ls": session_blob}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_state"

    # IdP callback → invalid_state redirect.
    resp = client.get(
        "/oauth/callback",
        params={"code": "upstream-code", "state": session_blob},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/error?error=invalid_state"

    # Approval-status poll → 400.
    resp = client.get("/oauth/approval/status", params={"st": session_blob})
    assert resp.status_code == 400

    # The exchange's own input wants the LOGIN purpose — a session blob
    # (its own output shape) is refused.
    resp = _continue_call(client, session_blob)
    assert resp.status_code == 400
    assert "session continuation rejected" in resp.text


def test_session_purpose_rejected_at_approval_decision(ctx: MagicMock) -> None:
    """F4 (#1300 review): the remaining matrix cell — a session blob never
    drives the inline approval decision, even for a caller holding the
    oauth-clients:write permission."""
    admin = Identity(
        sub="usr_admin",
        email="admin@test.local",
        actor_type=ActorType.USER,
        permissions=["oauth-clients:write"],
    )
    app = _make_app(ctx, identity=admin)
    client = TestClient(app)
    resp = client.post(
        "/oauth/approval/decision",
        json={"state": _session_blob(ctx), "action": "approve"},
        headers={"Authorization": "Bearer spa-token"},
    )
    assert resp.status_code == 400


def test_other_purposes_rejected_at_session_verifiers(ctx: MagicMock) -> None:
    """state/approval/login blobs never resume the flow as a session, and
    state/approval blobs never feed the exchange."""
    app, svc = _authorize_app(ctx)
    client = TestClient(app)

    def _foreign(purpose: str, key: str) -> str:
        return _session_blob(ctx, key=key, purpose=purpose)

    foreign_blobs = [
        _foreign("state", state_signing_key(ctx)),
        _foreign("approval", approval_state_key(ctx)),
        _foreign("login", login_signing_key(ctx)),
    ]
    # At the /authorize resume arm: all fall through to the login form.
    for blob in foreign_blobs:
        resp = _authorize_call(client, sc=blob)
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/login?ls=")
    svc.issue_authorization_code.assert_not_awaited()

    # At the exchange input: state/approval blobs are refused (login is the
    # one accepted purpose — covered by the happy path).
    exchange_app = _make_app(ctx, identity=_fake_identity())
    exchange_client = TestClient(exchange_app)
    for blob in foreign_blobs[:2]:
        resp = _continue_call(exchange_client, blob)
        assert resp.status_code == 400
        assert "session continuation rejected" in resp.text


def test_session_key_is_purpose_derived(ctx: MagicMock) -> None:
    """The session purpose has its own derived key: a blob signed with any
    sibling key fails verification even if the _purpose field is forged."""
    for wrong_key in (state_signing_key(ctx), approval_state_key(ctx), login_signing_key(ctx)):
        forged = _session_blob(ctx, key=wrong_key)  # purpose says "session"
        with pytest.raises(InvalidGrantError):
            verify_payload(forged, session_signing_key(ctx), purpose="session", max_age=60)


# ---------------------------------------------------------------------------
# The login page's continuation offer: button, not silent.


def test_login_page_offer_is_button_not_silent(ctx: MagicMock) -> None:
    """The page ships a hidden panel with an explicit continue button and a
    "Use a different account" link; the script only reveals the panel on a
    confirmed session and only navigates from the button's click handler."""
    client = TestClient(_make_app(ctx))
    resp = client.get("/login", params={"ls": _login_state(ctx)})
    assert resp.status_code == 200
    html = resp.text
    # Hidden-by-default panel + explicit controls.
    assert '<div class="session-panel" id="session-panel" hidden>' in html
    assert 'id="btn-session-continue"' in html
    assert "Use a different account" in html
    # The config seam carries the ls for the exchange.
    assert '"continue_url": "/oauth/session/continue"' in html
    # Detection is silent (reveal only) …
    assert "panel.hidden = false;" in html
    # … and navigation happens ONLY inside the button's click listener; the
    # /me success path never calls it.
    script = html[html.index('id="session-config"') :]
    before_click, after_click = script.split('continueBtn.addEventListener("click"', 1)
    assert "window.location.replace" not in before_click
    assert "window.location.replace" in after_click


def test_login_page_script_config_escapes_closing_tags(ctx: MagicMock) -> None:
    """The JSON config block \\u003c-escapes ``<`` so an embedded value can
    never close the script element."""
    client = TestClient(_make_app(ctx))
    resp = client.get("/login", params={"ls": _login_state(ctx)})
    config_start = resp.text.index('id="session-config"')
    config_end = resp.text.index("</script>", config_start)
    assert "</" not in resp.text[resp.text.index(">", config_start) + 1 : config_end]
