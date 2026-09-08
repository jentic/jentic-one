"""Integration tests: platform-session reuse on the /authorize flow (#1299).

Runs the zero-login rung-1 path against the real routers and databases —
platform login (real HS256 JWT from ``AuthService.login``) → GET /authorize →
login form → POST /oauth/session/continue (bearer + ls) → resume /authorize
with the ``sc`` continuation → consent (rendering the pinned identity and the
"Not you?" escape) → code → PKCE token exchange. No password ever reaches
POST /login. Also pins the no-session arm (byte-identical rung 3) and the
mismatch escape.

Only config is mutated (via the shared ``local_login_ctx`` fixture) and it is
restored — AppConfig is shared session state.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from jentic_one.admin.repos import OAuthClientRepository, UserRepository
from jentic_one.admin.services.auth_service import AuthService
from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.app import make_superset_verifier
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import authorize, local_login, oauth
from jentic_one.shared.auth.identity import LoginPayload
from jentic_one.shared.context import Context
from jentic_one.shared.state.backend import MemoryStateBackend
from tests.integration.auth.conftest import (
    LOCAL_LOGIN_PLATFORM_CLIENT_ID,
    LOCAL_LOGIN_PLATFORM_REDIRECT,
)
from tests.integration.auth.seeds import (
    CODE_VERIFIER,
    SEED_MARKER,
    SEED_PASSWORD,
    code_challenge,
    seed_password_user,
)

pytestmark = pytest.mark.integration

_THIRD_PARTY_CLIENT_ID = "oc_session_continue_test"
_THIRD_PARTY_REDIRECT = "https://sessionapp.test.local/cb"


def _make_app(ctx: Context) -> FastAPI:
    """Authorize + login + token routers wired to the REAL context.

    Unlike the local-login suite, the session-continue exchange authenticates
    a platform bearer token, so the real superset verifier is installed.
    """
    app = FastAPI()
    app.include_router(authorize.router)
    app.include_router(local_login.router)
    app.include_router(oauth.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.state.ctx = ctx
    app.state.auth_state_backend = MemoryStateBackend()
    app.state.verify_token = make_superset_verifier(ctx)
    return app


def _web_client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


async def _seed_third_party_client(ctx: Context) -> str:
    async with ctx.admin_db.session() as session:
        client = await OAuthClientRepository.create(
            session,
            client_id=_THIRD_PARTY_CLIENT_ID,
            name="Session Continue Third Party",
            redirect_uris=[_THIRD_PARTY_REDIRECT],
            client_secret_hash=None,
            token_endpoint_auth_method="none",
            created_by=SEED_MARKER,
        )
        await session.commit()
        return client.client_id


def _form_fields(html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name in ("ls", "csrf"):
        marker = f'name="{name}" value="'
        start = html.index(marker) + len(marker)
        fields[name] = html[start : html.index('"', start)]
    return fields


async def _platform_login(ctx: Context, email: str) -> str:
    """The 'platform login' leg: a real web-session JWT, as the SPA holds it."""
    bundle = await AuthService(ctx).login(LoginPayload(email=email, password=SEED_PASSWORD))
    return bundle.access_token


async def _walk_to_login_form(
    client: AsyncClient, *, client_id: str, redirect_uri: str, scope: str = "apis:read"
) -> tuple[str, str]:
    """GET /authorize (no IdP, gate on) → follow to the form → (ls, html)."""
    resp = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge(CODE_VERIFIER),
            "code_challenge_method": "S256",
            "scope": scope,
            "state": "session-state-1",
        },
    )
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    assert location.startswith("/login?ls="), location
    form = await client.get(location)
    assert form.status_code == 200, form.text
    return _form_fields(form.text)["ls"], form.text


async def _exchange_session(client: AsyncClient, token: str, ls: str) -> str:
    """POST the exchange; returns the relative resume URL."""
    resp = await client.post(
        "/oauth/session/continue",
        json={"state": ls},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    redirect_url = str(resp.json()["redirect_url"])
    assert redirect_url.startswith("/authorize?"), redirect_url
    return redirect_url


async def test_exchange_fenced_by_live_password_rotation_flag(
    local_login_ctx: Context, clean_grants: None, clean_user_secrets: None
) -> None:
    """F1 (#1300 review): an admin-forced password reset fences the exchange
    IMMEDIATELY via a live row read — a SPA token minted before the reset
    (whose baked must_change_password claim is stale-false) cannot mint a
    continuation; clearing the flag un-fences the same token."""
    ctx = local_login_ctx
    user_id, email = await seed_password_user(ctx, "usr_session_fence")

    app = _make_app(ctx)
    async with _web_client(app) as client:
        # Token minted BEFORE the reset — carries must_change_password=false.
        token = await _platform_login(ctx, email)
        ls, _ = await _walk_to_login_form(
            client,
            client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
            redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
        )

        # Admin forces a rotation: only the ROW changes, the token does not.
        async with ctx.admin_db.session() as session:
            await UserRepository.update(session, user_id, must_change_password=True)
            await session.commit()

        fenced = await client.post(
            "/oauth/session/continue",
            json={"state": ls},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert fenced.status_code == 400, fenced.text
        assert "session continuation rejected" in fenced.text

        # Flag cleared → the SAME (unchanged) token exchanges fine: the fence
        # is the row, not the claim.
        async with ctx.admin_db.session() as session:
            await UserRepository.update(session, user_id, must_change_password=False)
            await session.commit()
        resume_url = await _exchange_session(client, token, ls)
        resumed = await client.get(resume_url)
        assert resumed.status_code == 302
        assert resumed.headers["location"].startswith(f"{LOCAL_LOGIN_PLATFORM_REDIRECT}?")


async def test_zero_login_pass_third_party_to_token(
    local_login_ctx: Context, clean_grants: None, clean_user_secrets: None
) -> None:
    """The full rung-1 round trip: platform login → authorize → session
    continue → consent → code → PKCE token exchange, with the password posted
    ONLY to the platform login (never to POST /login)."""
    ctx = local_login_ctx
    _user_id, email = await seed_password_user(ctx, "usr_session_3p")
    await _seed_third_party_client(ctx)

    app = _make_app(ctx)
    async with _web_client(app) as client:
        token = await _platform_login(ctx, email)
        ls, form_html = await _walk_to_login_form(
            client, client_id=_THIRD_PARTY_CLIENT_ID, redirect_uri=_THIRD_PARTY_REDIRECT
        )
        # The form ships the continuation offer (hidden panel + explicit button).
        assert 'id="session-panel" hidden' in form_html
        assert 'id="btn-session-continue"' in form_html

        resume_url = await _exchange_session(client, token, ls)
        resumed = await client.get(resume_url)
        assert resumed.status_code == 302, resumed.text
        consent_url = resumed.headers["location"]
        assert consent_url.startswith("/oauth/consent?ch="), consent_url
        handle = parse_qs(urlsplit(consent_url).query)["ch"][0]

        # The consent page renders the PINNED identity and the escape hatch.
        page = await client.get(consent_url)
        assert page.status_code == 200, page.text
        assert email in page.text
        assert "Not you? Use a different account" in page.text

        approve = await client.post(
            "/oauth/consent", data={"consent_token": handle, "action": "approve"}
        )
        assert approve.status_code == 302, approve.text
        location = approve.headers["location"]
        assert location.startswith(f"{_THIRD_PARTY_REDIRECT}?"), location
        query = parse_qs(urlsplit(location).query)
        assert query["state"] == ["session-state-1"]
        code = query["code"][0]

        token_resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": CODE_VERIFIER,
                "redirect_uri": _THIRD_PARTY_REDIRECT,
                "client_id": _THIRD_PARTY_CLIENT_ID,
            },
        )
        assert token_resp.status_code == 200, token_resp.text
        assert token_resp.json()["access_token"]


async def test_zero_login_pass_platform_client_direct_code(
    local_login_ctx: Context, clean_grants: None, clean_user_secrets: None
) -> None:
    """Platform client: the resume skips consent (first-party trust) and the
    code exchanges for tokens — zero logins, zero consent screens."""
    ctx = local_login_ctx
    _user_id, email = await seed_password_user(ctx, "usr_session_platform")

    app = _make_app(ctx)
    async with _web_client(app) as client:
        token = await _platform_login(ctx, email)
        ls, _ = await _walk_to_login_form(
            client,
            client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
            redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
        )
        resume_url = await _exchange_session(client, token, ls)
        resumed = await client.get(resume_url)
        assert resumed.status_code == 302, resumed.text
        location = resumed.headers["location"]
        assert location.startswith(f"{LOCAL_LOGIN_PLATFORM_REDIRECT}?"), location
        query = parse_qs(urlsplit(location).query)
        assert query["state"] == ["session-state-1"]

        token_resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": query["code"][0],
                "code_verifier": CODE_VERIFIER,
                "redirect_uri": LOCAL_LOGIN_PLATFORM_REDIRECT,
                "client_id": LOCAL_LOGIN_PLATFORM_CLIENT_ID,
            },
        )
        assert token_resp.status_code == 200, token_resp.text


async def test_no_session_flow_unchanged_and_replay_falls_back(
    local_login_ctx: Context, clean_grants: None, clean_user_secrets: None
) -> None:
    """Without a session the flow is rung 3 exactly as before (the local-login
    suite covers the full pass); a REPLAYED continuation falls back there too
    instead of minting anything."""
    ctx = local_login_ctx
    _user_id, email = await seed_password_user(ctx, "usr_session_replay")

    app = _make_app(ctx)
    async with _web_client(app) as client:
        # No sc → 302 to the login form, byte-identical rung 3.
        ls, _ = await _walk_to_login_form(
            client,
            client_id=LOCAL_LOGIN_PLATFORM_CLIENT_ID,
            redirect_uri=LOCAL_LOGIN_PLATFORM_REDIRECT,
        )

        token = await _platform_login(ctx, email)
        resume_url = await _exchange_session(client, token, ls)
        first = await client.get(resume_url)
        assert first.status_code == 302
        assert first.headers["location"].startswith(f"{LOCAL_LOGIN_PLATFORM_REDIRECT}?")

        # Same sc again: single-use burn → back to the login form, no code.
        replay = await client.get(resume_url)
        assert replay.status_code == 302
        assert replay.headers["location"].startswith("/login?ls="), replay.headers["location"]


async def test_mismatch_escape_restarts_flow_without_session(
    local_login_ctx: Context, clean_grants: None, clean_user_secrets: None
) -> None:
    """The consent page's "Not you?" link re-runs the ORIGINAL authorize
    request without the continuation — landing on rung 3 (the login form) so
    the user can authenticate as someone else."""
    ctx = local_login_ctx
    _user_id, email = await seed_password_user(ctx, "usr_session_mismatch")
    await _seed_third_party_client(ctx)

    app = _make_app(ctx)
    async with _web_client(app) as client:
        token = await _platform_login(ctx, email)
        ls, _ = await _walk_to_login_form(
            client, client_id=_THIRD_PARTY_CLIENT_ID, redirect_uri=_THIRD_PARTY_REDIRECT
        )
        resume_url = await _exchange_session(client, token, ls)
        resumed = await client.get(resume_url)
        page = await client.get(resumed.headers["location"])

        marker = 'href="'
        anchor = page.text.index("Not you? Use a different account")
        start = page.text.rindex(marker, 0, anchor) + len(marker)
        restart_url = page.text[start : page.text.index('"', start)].replace("&amp;", "&")
        assert restart_url.startswith("/authorize?")
        assert "sc=" not in restart_url

        restarted = await client.get(restart_url)
        assert restarted.status_code == 302, restarted.text
        assert restarted.headers["location"].startswith("/login?ls=")

        # And the escape really does let a DIFFERENT account complete: the
        # rejoined form accepts another user's password.
        _other_id, other_email = await seed_password_user(ctx, "usr_session_other")
        form = await client.get(restarted.headers["location"])
        fields = _form_fields(form.text)
        done = await client.post(
            "/login", data={"email": other_email, "password": SEED_PASSWORD, **fields}
        )
        assert done.status_code == 302, done.text
        assert done.headers["location"].startswith("/oauth/consent?ch=")
