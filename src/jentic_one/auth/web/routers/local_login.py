"""Local-account login form on the ``/authorize`` flow (#1276, ``auth.local_login``).

``GET /authorize`` validates client + redirect URI + PKCE and then requires an
external OIDC IdP — so the standards-track native-app sign-in flow (RFC 8252:
anonymous DCR + system browser + loopback redirect + PKCE) only works on
IdP-configured deployments, even though the server already has a first-party
password account store with hashing, failed-login counting, and lockout
(``admin/services/auth_service.py``). This router makes that store reachable
from the OAuth authorization flow, behind a default-off gate:

- ``GET /login?ls=…`` — server-rendered login form (same inline-template +
  security-header posture as the consent page). ``ls`` is the HMAC-signed,
  purpose-discriminated, TTL'd internal state ``/authorize`` already mints,
  reused verbatim as the carry-through token and verified before rendering.
- ``POST /login`` — verifies signature, TTL, and a single-use CSRF nonce;
  authenticates via ``AuthService.authenticate`` (shared lockout — **no JWT is
  minted**); rejoins the existing flow: platform client → code issuance and
  302 back; registered third-party client → the same consent handle the IdP
  callback writes, carrying ``local_user_id`` instead of IdP claims.

Security posture (see #1276): both routes take the existing per-client_id+IP
authorize limiter (IP-keyed here — the client_id rides inside ``ls``); CSRF is
a signed ``ls`` + single-use server-side nonce (no ambient credential — there
is no cookie session to ride); failed auth re-renders one generic message (no
user-enumeration oracle, same posture as ``POST /auth/login``); the password
only ever transits the system browser → server hop, never a native app.

Gate off (the default), both routes answer the framework's plain
route-not-found 404 via the same route-class pattern as the DCR front door —
indistinguishable from not-shipped.
"""

from __future__ import annotations

import html as html_mod
import secrets
from collections.abc import Callable, Coroutine
from typing import Any
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.routing import APIRoute

from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.errors import AccountLockedError, InvalidCredentialsError
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.web.flow import (
    CONSENT_SECURITY_HEADERS,
    CONSENT_STATE_MAX_AGE_SECONDS,
    FONTS_URL,
    STATE_MAX_AGE_SECONDS,
    check_rate_limit,
    client_gate_passes,
    get_cached_oauth_client,
    get_consent_backend,
    is_platform_client,
    state_signing_key,
    verify_payload,
    write_local_consent_handle,
)
from jentic_one.auth.web.routers.authorize import get_authorize_service
from jentic_one.shared.auth.identity import LoginPayload
from jentic_one.shared.context import Context
from jentic_one.shared.web.deps import get_ctx
from jentic_one.shared.web.sensitive import SENSITIVE

logger = structlog.get_logger(__name__)

#: TTL for the single-use CSRF nonce minted by ``GET /login``. Matches the
#: consent-handle window: long enough to type a password, short enough that a
#: leaked form snapshot goes stale quickly.
_CSRF_TTL_SECONDS = CONSENT_STATE_MAX_AGE_SECONDS

_GENERIC_FAILURE_MESSAGE = "Invalid email or password."


class _LocalLoginRoute(APIRoute):
    """Route class owning the ``auth.local_login.enabled`` gate.

    Runs *before* the FastAPI dependency machinery so a disabled deployment
    answers the framework's own route-not-found 404 — no handler, dependency,
    or template ever runs (same posture as the DCR front door's
    ``_Rfc7591Route`` and the MCP discovery documents' ``_McpDiscoveryRoute``).
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            ctx: Context = request.app.state.ctx
            if not ctx.config.auth.local_login.enabled:
                # Mirror the framework's route-not-found body exactly.
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            return await original(request)

        return handler


router = APIRouter(route_class=_LocalLoginRoute)

_GATED_404_RESPONSE: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Local-account login is disabled (`auth.local_login.enabled=false`): "
        "the route answers the framework's plain route-not-found 404, so the gate state "
        "is unobservable."
    }
}

_LOGIN_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Sign in | Jentic One</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="{fonts_url}" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Nunito Sans', -apple-system, BlinkMacSystemFont, sans-serif;
            background: #f5f7f7;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .card {{
            background: white;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.07), 0 1px 3px rgba(0,0,0,0.06);
            max-width: 400px;
            width: 100%;
            padding: 32px;
        }}
        .logo {{ text-align: center; margin-bottom: 24px; }}
        .logo-text {{
            font-family: 'Sora', sans-serif;
            font-size: 22px;
            font-weight: 700;
            color: #0E1A1D;
            letter-spacing: -0.5px;
        }}
        .logo-text span {{ color: #689296; }}
        h1 {{
            font-size: 17px;
            font-weight: 600;
            margin-bottom: 8px;
            color: #0E1A1D;
            text-align: center;
            line-height: 1.4;
        }}
        .description {{
            color: #689296;
            font-size: 14px;
            margin-bottom: 24px;
            line-height: 1.5;
            text-align: center;
        }}
        .error {{
            background: #FCEBE5;
            color: #A02D0B;
            border-radius: 8px;
            font-size: 14px;
            padding: 10px 14px;
            margin-bottom: 16px;
            text-align: center;
        }}
        label {{
            display: block;
            font-size: 12px;
            font-weight: 600;
            color: #305256;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 6px;
        }}
        input[type="email"], input[type="password"] {{
            width: 100%;
            padding: 12px 14px;
            border: 1px solid #E4EAEB;
            border-radius: 8px;
            font-family: 'Nunito Sans', sans-serif;
            font-size: 14px;
            color: #0E1A1D;
            margin-bottom: 16px;
        }}
        input:focus {{
            outline: none;
            border-color: #305256;
        }}
        button {{
            width: 100%;
            padding: 12px 20px;
            border-radius: 8px;
            font-family: 'Nunito Sans', sans-serif;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            border: none;
            background: #305256;
            color: white;
            transition: all 0.2s;
        }}
        button:hover {{ background: #193238; }}
        .footer {{
            text-align: center;
            margin-top: 20px;
            font-size: 12px;
            color: #689296;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">
            <div class="logo-text">Jentic<span>One</span></div>
        </div>
        <h1>Sign in to continue</h1>
        <p class="description">Use your Jentic One account to authorize the application.</p>
        {error_block}
        <form method="post" action="/login">
            <label for="email">Email</label>
            <input type="email" id="email" name="email" value="{email}"
                   autocomplete="username" required autofocus>
            <label for="password">Password</label>
            <input type="password" id="password" name="password"
                   autocomplete="current-password" required>
            <input type="hidden" name="ls" value="{ls}">
            <input type="hidden" name="csrf" value="{csrf}">
            <button type="submit">Sign in</button>
        </form>
        <div class="footer">
            You will review what the application can access before it connects.
        </div>
    </div>
</body>
</html>
"""


def _verify_login_state(ls: str, ctx: Context) -> dict[str, str | None]:
    """Verify the carry-through token: signature, purpose, and TTL."""
    return verify_payload(
        ls, state_signing_key(ctx), purpose="state", max_age=STATE_MAX_AGE_SECONDS
    )


async def _mint_csrf_nonce(request: Request) -> str:
    """Mint a fresh single-use CSRF nonce, recorded server-side with a TTL."""
    nonce = secrets.token_urlsafe(32)
    backend = get_consent_backend(request)
    await backend.set(f"login-csrf:{nonce}", b"1", ttl_s=float(_CSRF_TTL_SECONDS))
    return nonce


async def _consume_csrf_nonce(csrf: str, request: Request) -> bool:
    """Validate + consume the nonce: it must exist (minted, unexpired) and be unused.

    Two-step, mirroring the consent-handle replay guard: existence proves the
    server minted it inside the TTL window; ``set_if_absent`` on a used-marker
    makes the first consumer win and every replay lose.
    """
    backend = get_consent_backend(request)
    if await backend.get(f"login-csrf:{csrf}") is None:
        return False
    return await backend.set_if_absent(
        f"login-csrf-used:{csrf}", b"1", ttl_s=float(_CSRF_TTL_SECONDS)
    )


def _render_login_page(
    ls: str, csrf: str, *, email: str = "", error: str | None = None
) -> HTMLResponse:
    """Render the login form with the consent page's security-header posture."""
    error_block = f'<div class="error">{html_mod.escape(error)}</div>' if error else ""
    html = _LOGIN_PAGE_TEMPLATE.format(
        fonts_url=FONTS_URL,
        error_block=error_block,
        email=html_mod.escape(email),
        ls=html_mod.escape(ls),
        csrf=html_mod.escape(csrf),
    )
    return HTMLResponse(content=html, headers=CONSENT_SECURITY_HEADERS)


@router.get(
    "/login",
    operation_id="loginPage",
    summary="Local-account login form (authorization flow)",
    response_class=HTMLResponse,
    response_model=None,
    responses=_GATED_404_RESPONSE,
    dependencies=[Depends(check_rate_limit)],
)
async def login_page(
    request: Request,
    ls: str = Query(..., description="Signed authorization-flow state (carry-through token)"),
    ctx: Context = Depends(get_ctx),
) -> HTMLResponse | RedirectResponse:
    """Render the local-account login form for an in-flight ``/authorize`` request.

    Verifies the ``ls`` signature/TTL **before** rendering — an expired or
    forged token never gets a form — and embeds ``ls`` plus a fresh single-use
    CSRF nonce.
    """
    try:
        _verify_login_state(ls, ctx)
    except InvalidGrantError:
        logger.warning("local_login_invalid_state", stage="form")
        return RedirectResponse(url="/error?error=invalid_state", status_code=302)

    csrf = await _mint_csrf_nonce(request)
    return _render_login_page(ls, csrf)


@router.post(
    "/login",
    operation_id="loginSubmit",
    summary="Local-account login submit (authorization flow)",
    response_model=None,
    responses=_GATED_404_RESPONSE,
    dependencies=[Depends(check_rate_limit)],
)
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(..., json_schema_extra=SENSITIVE),
    ls: str = Form(...),
    csrf: str = Form(..., json_schema_extra=SENSITIVE),
    ctx: Context = Depends(get_ctx),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
) -> HTMLResponse | RedirectResponse:
    """Authenticate the local account and rejoin the authorization flow.

    Success never mints a JWT — it flows straight into code issuance (platform
    client) or the consent handle (registered third-party client), exactly
    where the IdP callback rejoins. Credential failures re-render the form
    with one generic message: lockout state, unknown email, and wrong password
    are indistinguishable (no user-enumeration oracle), while the shared
    ``AuthService.authenticate`` core still increments the failed-login count
    and applies the lockout threshold.
    """
    try:
        params = _verify_login_state(ls, ctx)
    except InvalidGrantError:
        logger.warning("local_login_invalid_state", stage="submit")
        return RedirectResponse(url="/error?error=invalid_state", status_code=302)

    if not await _consume_csrf_nonce(csrf, request):
        # Expired or replayed nonce: not a credential failure — re-render with
        # a fresh nonce so a stale tab recovers with one more submit. The
        # password is deliberately not echoed back.
        logger.warning("local_login_csrf_rejected")
        fresh = await _mint_csrf_nonce(request)
        return _render_login_page(
            ls, fresh, email=email, error="The form expired — please try again."
        )

    client_id = str(params.get("client_id") or "")
    redirect_uri = str(params.get("redirect_uri") or "")
    code_challenge = str(params.get("code_challenge") or "")
    scope = str(params.get("scope") or "openid")
    raw_nonce = params.get("nonce")
    nonce = str(raw_nonce) if raw_nonce else None
    raw_state = params.get("original_state")
    original_state = str(raw_state) if raw_state else None

    oauth_client = None
    if not is_platform_client(client_id, ctx):
        # Mid-flow D7 re-check (same as the IdP callback): a client denied or
        # deactivated while the user is at the login form must not reach
        # consent or mint a code.
        oauth_client = await get_cached_oauth_client(request, client_id, ctx)
        if oauth_client is None or not client_gate_passes(oauth_client):
            logger.warning(
                "oauth_client_gate_failed_midflow", client_id=client_id, stage="local_login"
            )
            return RedirectResponse(url="/error?error=access_denied", status_code=302)

    try:
        user_id = await AuthService(ctx).authenticate(LoginPayload(email=email, password=password))
    except (InvalidCredentialsError, AccountLockedError):
        # One generic message for every credential failure (wrong password,
        # unknown email, locked account) — same posture as POST /auth/login.
        # authenticate() has already counted the failure / applied the lockout.
        logger.info("local_login_failed", client_id=client_id)
        fresh = await _mint_csrf_nonce(request)
        return _render_login_page(ls, fresh, email=email, error=_GENERIC_FAILURE_MESSAGE)

    # ``must_change_password`` deliberately does not block code issuance: the
    # OAuth path mints no session, and the flag keeps gating the UI login where
    # the password can actually be changed (#1276 open question 2's lean).

    if oauth_client is None:
        # Platform client: consent-skip is a first-party trust decision, same
        # terminal step as the IdP callback's platform arm.
        platform_code = await authorize_svc.issue_authorization_code(
            user_id=user_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scopes=scope,
            nonce=nonce,
        )
        logger.info("local_login_succeeded", client_id=client_id, consent="platform-skip")
        redirect_params: dict[str, str] = {"code": platform_code}
        if original_state:
            redirect_params["state"] = original_state
        separator = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(
            url=f"{redirect_uri}{separator}{urlencode(redirect_params)}", status_code=302
        )

    # Registered third-party client: the same consent handle the IdP callback
    # writes (one writer owns the shape — see flow.write_local_consent_handle),
    # carrying the already-provisioned local user instead of IdP claims.
    consent_handle = await write_local_consent_handle(
        request,
        local_user_id=user_id,
        user_email=email,
        redirect_uri=redirect_uri,
        original_state=original_state,
        client_id=client_id,
        code_challenge=code_challenge,
        scope=scope,
        nonce=nonce,
        oauth_client=oauth_client,
    )
    logger.info("local_login_succeeded", client_id=client_id, consent="required")
    return RedirectResponse(url=f"/oauth/consent?ch={consent_handle}", status_code=302)
