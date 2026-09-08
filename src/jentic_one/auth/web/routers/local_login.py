"""Local-account login form on the ``/authorize`` flow (#1276, ``auth.local_login``).

``GET /authorize`` validates client + redirect URI + PKCE and then requires an
external OIDC IdP — so the standards-track native-app sign-in flow (RFC 8252:
anonymous DCR + system browser + loopback redirect + PKCE) only works on
IdP-configured deployments, even though the server already has a first-party
password account store with hashing, failed-login counting, and lockout
(``admin/services/auth_service.py``). This router makes that store reachable
from the OAuth authorization flow, behind a default-off gate:

- ``GET /login?ls=…`` — server-rendered login form (same inline-template +
  security-header posture as the consent page). ``ls`` is an HMAC-signed,
  purpose-discriminated, TTL'd carry-through token minted ONLY by rung 3 of
  ``flow.resolve_identity_gate`` under the dedicated ``login`` purpose — an
  IdP-leg ``state`` or an approval blob can never open the form — and it is
  verified before rendering.
- ``POST /login`` — verifies signature, TTL, and a single-use CSRF nonce
  bound to the ``ls`` it was minted for; authenticates via
  ``AuthService.authenticate`` (shared lockout — **no JWT is minted**);
  rejoins the existing flow: platform client → code issuance and 302 back;
  registered third-party client → the same consent handle the IdP callback
  writes, carrying ``local_user_id`` instead of IdP claims. A successful
  submit burns the ``ls`` (single-use); failures leave it valid so the user
  can retry a typo'd password.
- ``POST /oauth/session/continue`` — identity-ladder rung 1 (#1299): the
  login page's script exchanges a live same-origin SPA bearer session plus
  the pending ``ls`` for a short-TTL ``session``-purpose continuation blob
  pinning the caller's user id; ``GET /authorize`` redeems it (single-use)
  and rejoins at consent with zero logins. Detection is silent, continuation
  is an explicit "Continue as <email>" button — never automatic — with a
  "Use a different account" link falling back to the form (rung 3).

Security posture (see #1276): both routes take the existing per-client_id+IP
authorize limiter (IP-keyed here — the client_id rides inside ``ls``); CSRF is
a signed ``ls`` + single-use server-side nonce bound to that ``ls`` (no
ambient credential — there is no cookie session to ride); failed auth
re-renders one generic message (no user-enumeration *response* oracle, same
posture as ``POST /auth/login``); accounts flagged ``must_change_password``
authenticate but cannot rejoin the flow until they rotate via the UI; the
password only ever transits the system browser → server hop, never a native
app.

Gate off (the default) — or an external IdP configured
(``auth.idp.enabled=true``: IdP always wins, no mixed mode) — both routes
answer the framework's plain route-not-found 404 via the same route-class
pattern as the DCR front door — indistinguishable from not-shipped.
"""

from __future__ import annotations

import hashlib
import html as html_mod
import json
import secrets
import time
from collections.abc import Callable, Coroutine
from typing import Any
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.routing import APIRoute

from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.errors import (
    AccountLockedError,
    InvalidCredentialsError,
    UserNotFoundError,
)
from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.errors import InvalidGrantError, RateLimitExceededError
from jentic_one.auth.web.flow import (
    CONSENT_SECURITY_HEADERS,
    CONSENT_STATE_MAX_AGE_SECONDS,
    FONTS_URL,
    SPA_TOKEN_STORAGE_KEY,
    STATE_MAX_AGE_SECONDS,
    check_rate_limit,
    client_gate_passes,
    get_cached_oauth_client,
    get_consent_backend,
    is_platform_client,
    login_signing_key,
    session_signing_key,
    sign_payload,
    verify_payload,
    write_local_consent_handle,
)
from jentic_one.auth.web.ratelimit import client_ip, get_auth_backend
from jentic_one.auth.web.routers.authorize import get_authorize_service
from jentic_one.auth.web.schemas.local_login import (
    OAuthSessionContinueRequest,
    OAuthSessionContinueResponse,
)
from jentic_one.auth.web.theme import AUTH_PAGE_CSS, LOGO_BLOCK_HTML
from jentic_one.shared.auth.identity import Identity, LoginPayload
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.deps import get_ctx
from jentic_one.shared.web.sensitive import SENSITIVE

logger = structlog.get_logger(__name__)

#: TTL for the single-use CSRF nonce minted by ``GET /login``. Matches the
#: consent-handle window: long enough to type a password, short enough that a
#: leaked form snapshot goes stale quickly.
_CSRF_TTL_SECONDS = CONSENT_STATE_MAX_AGE_SECONDS

_GENERIC_FAILURE_MESSAGE = "Invalid email or password."

#: Shown after a *successful* credential check when the account carries
#: ``must_change_password`` — post-authentication, so it opens no
#: user-enumeration surface (an anonymous attacker never reaches it without
#: the correct password).
_PASSWORD_ROTATION_MESSAGE = (
    "Your password must be changed before connecting applications — "
    "sign in to Jentic One directly, update your password, then try again."
)


class _LocalLoginRoute(APIRoute):
    """Route class owning the ``auth.local_login.enabled`` gate.

    Runs *before* the FastAPI dependency machinery so a disabled deployment
    answers the framework's own route-not-found 404 — no handler, dependency,
    or template ever runs (same posture as the DCR front door's
    ``_Rfc7591Route`` and the MCP discovery documents' ``_McpDiscoveryRoute``).

    The gate is two-sided: the routes are also 404 whenever an external IdP
    is configured (``auth.idp.enabled=true``). IdP always wins — even a valid
    login token handed around the /authorize dispatch must not reach a
    password form on an SSO deployment (the IdP's MFA/admission policy is the
    whole point of configuring one).
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            ctx: Context = request.app.state.ctx
            if not ctx.config.auth.local_login.enabled or ctx.config.auth.idp.enabled:
                # Mirror the framework's route-not-found body exactly.
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            return await original(request)

        return handler


router = APIRouter(route_class=_LocalLoginRoute)

_GATED_404_RESPONSE: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Local-account login is unavailable (`auth.local_login.enabled=false`, "
        "or an external IdP is configured — `auth.idp.enabled=true` — which always wins): "
        "the route answers the framework's plain route-not-found 404, so the gate state "
        "is unobservable."
    }
}

# Static page structure only — every dynamic value is HTML-escaped before it
# is formatted in, and the visual theme ({page_css}/{logo_block}) is the
# static, drift-guarded constant pair from ``auth.web.theme``.
_LOGIN_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Sign in | Jentic One</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="{fonts_url}" rel="stylesheet">
    <style>{page_css}</style>
</head>
<body>
    <div class="card">
        {logo_block}
        <h1>Sign in to continue</h1>
        <p class="description">Use your Jentic One account to authorize the application.</p>
        {error_block}
        <div class="session-panel" id="session-panel" hidden>
            <div class="label">Already signed in as</div>
            <div class="email" id="session-email"></div>
            <button type="button" id="btn-session-continue">Continue</button>
            <a href="#" class="alt" id="session-use-different">Use a different account</a>
        </div>
        <form method="post" action="/login">
            <label class="field-label" for="email">Email</label>
            <input type="email" id="email" name="email" value="{email}"
                   autocomplete="username" required autofocus>
            <label class="field-label" for="password">Password</label>
            <input type="password" id="password" name="password"
                   autocomplete="current-password" required>
            <input type="hidden" name="ls" value="{ls}">
            <input type="hidden" name="csrf" value="{csrf}">
            <button type="submit" class="primary block">Sign in</button>
        </form>
        <div class="footer">
            You will review what the application can access before it connects.
        </div>
    </div>
    <script id="session-config" type="application/json">{session_config}</script>
    {session_script}
</body>
</html>
"""

# Inline behaviour for the session-continuation offer (identity-ladder rung 1,
# #1299). Kept as a plain string (not a .format template) so its braces need
# no doubling; every dynamic value comes from the JSON
# <script id="session-config"> block — the single escaped seam between server
# data and page script (same pattern as the approval-pending page).
#
# Deliberately button-not-silent: a detected SPA session only REVEALS the
# explicit "Continue as <email>" button. Nothing navigates without a click,
# so an account mismatch is visible before the flow is resumed, and the
# "Use a different account" link keeps rung 3 (the password form below)
# one gesture away.
_SESSION_CONTINUE_SCRIPT = """<script>
(function () {
    "use strict";
    var cfg = JSON.parse(document.getElementById("session-config").textContent);
    var panel = document.getElementById("session-panel");
    var emailEl = document.getElementById("session-email");
    var continueBtn = document.getElementById("btn-session-continue");

    var token = null;
    try { token = window.localStorage.getItem(cfg.token_key); } catch (e) { /* blocked */ }
    if (!token) { return; }

    // Silent detection only: /me confirms the same-origin SPA token belongs
    // to a live platform USER session before the panel is revealed.
    fetch(cfg.me_url, { headers: { Authorization: "Bearer " + token } })
        .then(function (resp) { return resp.ok ? resp.json() : null; })
        .then(function (me) {
            if (me && me.email && typeof me.id === "string" &&
                    me.id.indexOf("usr_") === 0) {
                emailEl.textContent = me.email;
                continueBtn.textContent = "Continue as " + me.email;
                panel.hidden = false;
            }
        })
        .catch(function () { /* stay anonymous — the form below is unchanged */ });

    function isSameOriginAuthorize(url) {
        // Only ever navigate to the relative /authorize resume URL the
        // exchange minted — never to a caller-influenced absolute URL.
        return typeof url === "string" && url.indexOf("/authorize?") === 0;
    }

    continueBtn.addEventListener("click", function () {
        continueBtn.disabled = true;
        fetch(cfg.continue_url, {
            method: "POST",
            headers: {
                Authorization: "Bearer " + token,
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ state: cfg.state }),
        })
            .then(function (resp) {
                if (!resp.ok) { throw new Error("session continue failed"); }
                return resp.json();
            })
            .then(function (body) {
                if (!isSameOriginAuthorize(body.redirect_url)) {
                    throw new Error("unexpected redirect");
                }
                window.location.replace(body.redirect_url);
            })
            .catch(function () {
                // Expired token, gated client, or config change: hide the
                // panel and fall back to the password form (rung 3).
                panel.hidden = true;
            });
    });

    document.getElementById("session-use-different").addEventListener("click", function (ev) {
        ev.preventDefault();
        panel.hidden = true;
        var emailField = document.getElementById("email");
        if (emailField) { emailField.focus(); }
    });
})();
</script>"""


def _verify_login_state(ls: str, ctx: Context) -> dict[str, str | None]:
    """Verify the carry-through token: signature, ``login`` purpose, and TTL.

    Only rung 3 of ``flow.resolve_identity_gate`` mints this purpose. The
    IdP-leg ``state`` and the approval blob use different derived keys AND
    purposes, so neither can open the form (mutual rejection in every
    direction — pinned by tests).
    """
    return verify_payload(
        ls, login_signing_key(ctx), purpose="login", max_age=STATE_MAX_AGE_SECONDS
    )


def _ls_digest(ls: str) -> str:
    """A compact digest of the carry-through token, used as a binding value."""
    return hashlib.sha256(ls.encode()).hexdigest()


async def _mint_csrf_nonce(request: Request, ls: str) -> str:
    """Mint a fresh single-use CSRF nonce, bound server-side to this ``ls``.

    The stored value is the digest of the ``ls`` the form was rendered for,
    so a nonce harvested from one flow (an attacker's own ``GET /login``)
    can never satisfy a submit for a different flow.
    """
    nonce = secrets.token_urlsafe(32)
    backend = get_consent_backend(request)
    await backend.set(
        f"login-csrf:{nonce}", _ls_digest(ls).encode(), ttl_s=float(_CSRF_TTL_SECONDS)
    )
    return nonce


async def _consume_csrf_nonce(csrf: str, ls: str, request: Request) -> bool:
    """Validate + consume the nonce: minted for THIS ``ls``, unexpired, unused.

    Two-step, mirroring the consent-handle replay guard: existence proves the
    server minted it inside the TTL window, the stored digest proves it was
    minted for this very ``ls``, and ``set_if_absent`` on a used-marker makes
    the first consumer win and every replay lose.
    """
    backend = get_consent_backend(request)
    stored = await backend.get(f"login-csrf:{csrf}")
    if stored is None or stored.decode() != _ls_digest(ls):
        return False
    return await backend.set_if_absent(
        f"login-csrf-used:{csrf}", b"1", ttl_s=float(_CSRF_TTL_SECONDS)
    )


async def _ls_already_used(ls: str, request: Request) -> bool:
    """Whether this carry-through token was already spent by a successful login."""
    backend = get_consent_backend(request)
    return await backend.get(f"login-ls-used:{_ls_digest(ls)}") is not None


async def _burn_login_state(ls: str, request: Request) -> bool:
    """Spend the carry-through token (single-use on SUCCESS only).

    Called after the credential check succeeds and immediately before the
    flow rejoin, so a completed flow's ``ls`` can't be replayed within its
    TTL. Failures deliberately do NOT burn it — the user retries a typo'd
    password on the same page. ``set_if_absent`` makes concurrent successful
    submits race safely: the first wins, the rest read as replays.
    """
    backend = get_consent_backend(request)
    return await backend.set_if_absent(
        f"login-ls-used:{_ls_digest(ls)}", b"1", ttl_s=float(STATE_MAX_AGE_SECONDS)
    )


def _render_login_page(
    ls: str, csrf: str, *, email: str = "", error: str | None = None
) -> HTMLResponse:
    """Render the login form with the consent page's security-header posture."""
    error_block = f'<div class="error" role="alert">{html_mod.escape(error)}</div>' if error else ""
    session_config = {
        "me_url": "/me",
        "continue_url": "/oauth/session/continue",
        "token_key": SPA_TOKEN_STORAGE_KEY,
        "state": ls,
    }
    # \u003c-escape so no embedded value can ever close the JSON <script>
    # block (same seam discipline as the approval-pending page).
    session_config_json = json.dumps(session_config).replace("<", "\\u003c")
    html = _LOGIN_PAGE_TEMPLATE.format(
        fonts_url=FONTS_URL,
        page_css=AUTH_PAGE_CSS,
        logo_block=LOGO_BLOCK_HTML,
        error_block=error_block,
        email=html_mod.escape(email),
        ls=html_mod.escape(ls),
        csrf=html_mod.escape(csrf),
        session_config=session_config_json,
        session_script=_SESSION_CONTINUE_SCRIPT,
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

    Verifies the ``ls`` signature/TTL/purpose **before** rendering — an
    expired, forged, or wrong-purpose token never gets a form — re-checks the
    D7 client gate (a client denied or deactivated while the user holds the
    ``ls`` must not be asked for a password), rejects an already-spent ``ls``,
    and embeds ``ls`` plus a fresh single-use CSRF nonce bound to it.
    """
    try:
        params = _verify_login_state(ls, ctx)
    except InvalidGrantError:
        logger.warning("local_login_invalid_state", stage="form")
        return RedirectResponse(url="/error?error=invalid_state", status_code=302)

    if await _ls_already_used(ls, request):
        # A completed flow's ls is spent: don't render a form whose submit is
        # guaranteed to be rejected as a replay.
        logger.warning("local_login_state_replayed", stage="form")
        return RedirectResponse(url="/error?error=invalid_state", status_code=302)

    client_id = str(params.get("client_id") or "")
    if not is_platform_client(client_id, ctx):
        # Mid-flow D7 re-check, same as the IdP callback and POST /login: the
        # user must not type a password into a flow that can no longer
        # complete. (The /authorize entry gate renders the approval-pending
        # page; by this point the signed window is mid-flight, so the posture
        # is the shared browser-facing error redirect.)
        oauth_client = await get_cached_oauth_client(request, client_id, ctx)
        if oauth_client is None or not client_gate_passes(oauth_client):
            logger.warning(
                "oauth_client_gate_failed_midflow", client_id=client_id, stage="local_login_form"
            )
            return RedirectResponse(url="/error?error=access_denied", status_code=302)

    csrf = await _mint_csrf_nonce(request, ls)
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
    where the IdP callback rejoins, and burns the single-use ``ls``.
    Credential failures re-render the form with one generic message: lockout
    state, unknown email, and wrong password are indistinguishable (no
    user-enumeration response oracle), while the shared
    ``AuthService.authenticate`` core still increments the failed-login count
    and applies the lockout threshold. An account flagged
    ``must_change_password`` authenticates but is told to rotate via the UI
    first — the OAuth plane must not hand a fully-scoped token to a
    temporary-password principal the UI would have boxed into
    change-password-only.
    """
    try:
        params = _verify_login_state(ls, ctx)
    except InvalidGrantError:
        logger.warning("local_login_invalid_state", stage="submit")
        return RedirectResponse(url="/error?error=invalid_state", status_code=302)

    if not await _consume_csrf_nonce(csrf, ls, request):
        # Expired, replayed, or wrong-flow nonce: not a credential failure —
        # re-render with a fresh nonce so a stale tab recovers with one more
        # submit. The password is deliberately not echoed back.
        logger.warning("local_login_csrf_rejected")
        fresh = await _mint_csrf_nonce(request, ls)
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

    auth_svc = AuthService(ctx)
    try:
        user_id = await auth_svc.authenticate(LoginPayload(email=email, password=password))
    except (InvalidCredentialsError, AccountLockedError):
        # One generic message for every credential failure (wrong password,
        # unknown email, locked account) — same posture as POST /auth/login.
        # authenticate() has already counted the failure / applied the lockout.
        logger.info("local_login_failed", client_id=client_id)
        fresh = await _mint_csrf_nonce(request, ls)
        return _render_login_page(ls, fresh, email=email, error=_GENERIC_FAILURE_MESSAGE)

    if await auth_svc.password_rotation_required(user_id):
        # Post-authentication rotation fence: the flag means "rotate before
        # you use the platform", and the OAuth plane must honor it exactly
        # like the UI gate does — otherwise a temp-password principal
        # (invite/admin-reset) walks /authorize into a fully-scoped token.
        # Post-auth, so no enumeration surface; the ls is NOT burned (the
        # user rotates in another tab and retries the same flow).
        logger.info("local_login_password_rotation_required", client_id=client_id)
        fresh = await _mint_csrf_nonce(request, ls)
        return _render_login_page(ls, fresh, email=email, error=_PASSWORD_ROTATION_MESSAGE)

    if not await _burn_login_state(ls, request):
        # Success on an already-spent ls: a replayed ticket, not a credential
        # failure. Same terminal posture as any other invalid state.
        logger.warning("local_login_state_replayed", stage="submit")
        return RedirectResponse(url="/error?error=invalid_state", status_code=302)

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


# ---------------------------------------------------------------------------
# Platform-session continuation exchange (identity-ladder rung 1, #1299).

#: One generic rejection for every post-verification failure (spent ls, gated
#: client, unknown/inactive user): a caller must not be able to distinguish
#: WHY a session cannot be continued, only that it cannot — the same 400 the
#: signature/TTL/purpose checks produce.
_SESSION_CONTINUE_REJECTED = "session continuation rejected"


def _get_session_continue_limiter(request: Request, ctx: Context) -> RateLimiter:
    limiter: RateLimiter | None = getattr(request.app.state, "_session_continue_limiter", None)
    if limiter is not None:
        return limiter
    cfg = ctx.config.auth.oauth_rate_limit
    backend = get_auth_backend(request)
    # Own bucket namespace (the RFC 7009 revocation pattern): keyed on bare
    # IP, so it must not share a bucket with another bare-IP limiter carrying
    # different rate/burst params. Reuses the exchange rpm/burst knobs
    # (config schema unchanged): the endpoint is authenticated, one click
    # sends one request, and its traffic is bounded by the same browser
    # population as /oauth/token.
    limiter = RateLimiter(
        backend,
        default_rpm=cfg.exchange_rpm,
        burst=cfg.exchange_burst,
        namespace="oauth-session-continue",
    )
    request.app.state._session_continue_limiter = limiter
    return limiter


async def check_session_continue_rate_limit(
    request: Request, ctx: Context = Depends(get_ctx)
) -> None:
    """Per-IP rate limiter for the session-continue exchange.

    Keyed by bare IP: the request's only other inputs are the bearer token
    and the signed blob, and both are caller-supplied — a self-chosen key
    component would let one host sidestep the bucket.
    """
    trusted = frozenset(ctx.config.auth.oauth_rate_limit.trusted_proxies)
    ip = client_ip(request, trusted)
    limiter = _get_session_continue_limiter(request, ctx)
    outcome = await limiter.acquire(ip)
    if not outcome.allowed:
        raise RateLimitExceededError(retry_after=outcome.retry_after_s)


@router.post(
    "/oauth/session/continue",
    operation_id="sessionContinueEndpoint",
    summary="Exchange a live platform session for an authorize continuation",
    responses={
        **_GATED_404_RESPONSE,
        400: {
            "description": "Malformed, tampered, expired, or otherwise unusable "
            "authorize state — one generic rejection, never a reason."
        },
    },
    dependencies=[Depends(check_session_continue_rate_limit)],
)
async def session_continue_endpoint(
    request: Request,
    response: Response,
    body: OAuthSessionContinueRequest,
    identity: Identity = get_current_identity(require_actor_type=ActorType.USER),
    ctx: Context = Depends(get_ctx),
) -> OAuthSessionContinueResponse:
    """Rung 1 of the /authorize identity ladder: reuse the platform session.

    The login page's script posts the pending authorize state (the ``ls``
    carry-through token) with the SPA's bearer token in the Authorization
    header — no cookies, no ambient credentials, so a cross-site form cannot
    drive it (same CSRF posture as the consent POST and the inline approval
    decision). The platform token is validated by the standard auth
    dependency (users only), and the ``active`` / ``must_change_password``
    fences are re-checked with a LIVE user-row read — not the token's baked
    claims — matching rung 3's ``password_rotation_required`` posture, so an
    admin-forced reset fences the exchange immediately even while pre-reset
    SPA tokens are still in flight. The D7 client gate is re-checked, and on
    success the response carries a relative ``/authorize`` resume URL bearing
    a short-TTL, ``session``-purpose continuation blob that pins THIS
    caller's ``user_id`` — the identity is fixed at exchange time, before the
    consent page renders it with its "Not you?" escape.

    Every failure after authentication is the same generic 400: an invalid
    blob must not let the caller learn anything about the client or the flow.
    """
    try:
        params = _verify_login_state(body.state, ctx)
    except InvalidGrantError:
        logger.warning("oauth_session_continue_rejected", reason="state_verify_failed")
        raise InvalidGrantError(_SESSION_CONTINUE_REJECTED) from None

    if await _ls_already_used(body.state, request):
        # The flow this state belongs to already completed via the form.
        logger.warning("oauth_session_continue_rejected", reason="state_spent")
        raise InvalidGrantError(_SESSION_CONTINUE_REJECTED)

    client_id = str(params.get("client_id") or "")
    if not is_platform_client(client_id, ctx):
        # Mid-flow D7 re-check, same as GET/POST /login: a client denied or
        # deactivated while the user holds the ls must not be resumable.
        oauth_client = await get_cached_oauth_client(request, client_id, ctx)
        if oauth_client is None or not client_gate_passes(oauth_client):
            logger.warning(
                "oauth_client_gate_failed_midflow", client_id=client_id, stage="session_continue"
            )
            raise InvalidGrantError(_SESSION_CONTINUE_REJECTED)

    try:
        user = await UserService(ctx).get_by_id(identity.sub)
    except UserNotFoundError:
        logger.warning("oauth_session_continue_rejected", reason="user_not_found")
        raise InvalidGrantError(_SESSION_CONTINUE_REJECTED) from None
    if not user.active or user.must_change_password:
        # LIVE row read, not the JWT claim: an admin-forced password reset
        # must fence the account immediately, exactly as rung 3's
        # ``password_rotation_required`` does — a SPA token minted before the
        # reset still carries a stale ``must_change_password=false`` claim
        # for the rest of its TTL and must not mint a continuation.
        logger.warning("oauth_session_continue_rejected", reason="user_fenced")
        raise InvalidGrantError(_SESSION_CONTINUE_REJECTED)

    continuation_payload: dict[str, str | None] = {
        "client_id": client_id,
        "redirect_uri": str(params.get("redirect_uri") or ""),
        "code_challenge": str(params.get("code_challenge") or ""),
        "scope": str(params.get("scope") or "openid"),
        "nonce": params.get("nonce"),
        "original_state": params.get("original_state"),
        # Pinned at exchange time: the resume leg trusts the blob, never the
        # (by then anonymous) browser. Only the opaque user_id — the blob
        # rides a GET query param (access logs, browser history), so no PII;
        # the redemption arm re-reads the row for the email anyway.
        "user_id": user.id,
        "iat": str(int(time.time())),
    }
    continuation = sign_payload(continuation_payload, session_signing_key(ctx), purpose="session")

    # Re-run the ORIGINAL authorize request plus the continuation — the same
    # resume shape as the approval-pending page, so rung 1 slots into the
    # ladder without new /authorize semantics for the anonymous case.
    resume_params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": str(params.get("redirect_uri") or ""),
        "code_challenge": str(params.get("code_challenge") or ""),
        "code_challenge_method": "S256",
        "scope": str(params.get("scope") or "openid"),
    }
    original_state = params.get("original_state")
    if original_state:
        resume_params["state"] = original_state
    nonce = params.get("nonce")
    if nonce:
        resume_params["nonce"] = nonce
    resume_params["sc"] = continuation

    logger.info("oauth_session_continue_minted", client_id=client_id, user_id=user.id)
    response.headers["Cache-Control"] = "no-store"
    return OAuthSessionContinueResponse(redirect_url=f"/authorize?{urlencode(resume_params)}")
