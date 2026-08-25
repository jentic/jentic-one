"""AuthCode+PKCE authorization endpoints with consent screen support.

Flow overview:
  GET /authorize        — validate client + redirect_uri, redirect to IdP
  GET /oauth/callback   — verify IdP response, show consent screen (or skip)
  POST /oauth/consent   — verify consent token, issue authorization code, redirect to client
"""

from __future__ import annotations

import hashlib
import hmac
import html as html_mod
import json
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from urllib.parse import urlencode, urlparse

import httpx
import structlog
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from jentic_one.admin.repos.oauth_client_repo import OAuthClientRepository
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.errors import (
    InvalidGrantError,
    RateLimitExceededError,
    UserNotAdmittedError,
)
from jentic_one.shared.auth.permission_catalog import (
    AGENTS_READ,
    AGENTS_WRITE,
    ALL_PERMISSIONS,
    CREDENTIALS_READ,
    CREDENTIALS_WRITE,
    TOOLKITS_READ,
    TOOLKITS_WRITE,
    compute_implies_transitive,
)
from jentic_one.shared.context import Context
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.state.backend import MemoryStateBackend
from jentic_one.shared.web.deps import get_ctx

logger = structlog.get_logger(__name__)

router = APIRouter()

_AUTHORIZE_RATE_LIMIT_RPM = 30
_rate_limit_store = MemoryStateBackend()
_ip_rate_limiter = RateLimiter(_rate_limit_store, default_rpm=_AUTHORIZE_RATE_LIMIT_RPM, burst=30)


async def _check_rate_limit(request: Request) -> None:
    """Per-IP rate limiter for unauthenticated authorization endpoints."""
    ip = request.client.host if request.client else "unknown"
    outcome = await _ip_rate_limiter.acquire(ip)
    if not outcome.allowed:
        raise RateLimitExceededError(retry_after=outcome.retry_after_s)


_ALLOWED_CANONICAL_PATHS: frozenset[str] = frozenset(
    {
        "/oauth/callback",
        "/auth/callback",
        "/app/oauth/callback",
        "/app/auth/callback",
    }
)


def _matches_canonical_origin(redirect_uri: str, canonical_base_url: str) -> bool:
    """Check if redirect_uri matches the platform's canonical origin and an allowed path."""
    if not canonical_base_url:
        return False
    parsed_redirect = urlparse(redirect_uri)
    parsed_canonical = urlparse(canonical_base_url)
    if not parsed_redirect.scheme or not parsed_redirect.netloc:
        return False
    if (
        parsed_redirect.scheme != parsed_canonical.scheme
        or parsed_redirect.netloc != parsed_canonical.netloc
    ):
        return False
    normalised_path = parsed_redirect.path.rstrip("/")
    return normalised_path in _ALLOWED_CANONICAL_PATHS


async def _is_allowed_redirect_uri(redirect_uri: str, client_id: str, ctx: Context) -> bool:
    """Validate redirect_uri against the platform's canonical origin or registered clients.

    First checks if the redirect_uri matches the canonical base URL (for jentic-one's
    own UI). If not, looks up the client_id in the OAuth client registry and checks
    if the redirect_uri is in the client's allowed list.
    """
    if _matches_canonical_origin(redirect_uri, ctx.config.auth.canonical_base_url):
        return True

    async with ctx.admin_db.session() as session:
        client = await OAuthClientRepository.get_by_client_id(session, client_id)

    if client is None or not client.active:
        return False

    return redirect_uri in client.redirect_uris


async def _get_client_allowed_scopes(client_id: str, ctx: Context) -> frozenset[str] | None:
    """Return allowed scopes for a registered client, or None for first-party."""
    async with ctx.admin_db.session() as session:
        client = await OAuthClientRepository.get_by_client_id(session, client_id)
    if client is None:
        return None
    if client.allowed_scopes:
        return frozenset(client.allowed_scopes)
    return None


def _callback_uri(request: Request, canonical_base_url: str) -> str:
    """Build the IdP callback URI (the ``redirect_uri`` sent to the IdP).

    Behind a TLS-terminating proxy (e.g. an ALB) the app sees a plain-``http``
    request, so ``request.url_for`` would emit an ``http://`` callback that no
    longer matches the ``https://`` URI registered with the IdP — the IdP then
    rejects the request. When a canonical base URL is configured we therefore
    take its scheme + host and keep only the *path* resolved by ``url_for`` (so
    a route rename still flows through). Without a canonical base URL (local
    dev) we fall back to the request-derived URL unchanged.
    """
    resolved = request.url_for("authorize_oauth_callback")
    if not canonical_base_url:
        return str(resolved)
    return f"{canonical_base_url.rstrip('/')}{resolved.path}"


def get_authorize_service(ctx: Context = Depends(get_ctx)) -> AuthorizeService:
    return AuthorizeService(ctx)


STATE_MAX_AGE_SECONDS = 600
CONSENT_STATE_MAX_AGE_SECONDS = 300

_CONSENT_SECURITY_HEADERS: dict[str, str] = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

_FONTS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Nunito+Sans:wght@400;500;600;700"
    "&family=Sora:wght@600;700&display=swap"
)
_CHECK_SVG = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'"
    " viewBox='0 0 20 20' fill='%230E1A1D'%3E%3Cpath fill-rule="
    "'evenodd' d='M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-"
    "1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1"
    " 0 011.414 0z' clip-rule='evenodd'/%3E%3C/svg%3E"
)

_CONSENT_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Authorize {app_name} | Jentic One</title>
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
        .logo {{
            text-align: center;
            margin-bottom: 24px;
        }}
        .logo-text {{
            font-family: 'Sora', sans-serif;
            font-size: 22px;
            font-weight: 700;
            color: #0E1A1D;
            letter-spacing: -0.5px;
        }}
        .logo-text span {{
            color: #689296;
        }}
        h1 {{
            font-size: 17px;
            font-weight: 600;
            margin-bottom: 8px;
            color: #0E1A1D;
            text-align: center;
            line-height: 1.4;
        }}
        .app-name {{
            color: #305256;
            font-weight: 700;
        }}
        .description {{
            color: #689296;
            font-size: 14px;
            margin-bottom: 24px;
            line-height: 1.5;
            text-align: center;
        }}
        .user-info {{
            text-align: center;
            margin-bottom: 20px;
            padding: 12px;
            background: #f5f7f7;
            border-radius: 8px;
        }}
        .user-info .label {{
            font-size: 11px;
            color: #689296;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }}
        .user-info .email {{
            font-size: 14px;
            color: #0E1A1D;
            font-weight: 600;
        }}
        .permissions {{
            background: #f5f7f7;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 24px;
        }}
        .permissions h2 {{
            font-size: 12px;
            font-weight: 600;
            color: #305256;
            margin-bottom: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .permissions ul {{
            list-style: none;
            font-size: 14px;
            color: #305256;
        }}
        .permissions li {{
            padding: 8px 0;
            display: flex;
            align-items: center;
            border-bottom: 1px solid #E4EAEB;
        }}
        .permissions li:last-child {{
            border-bottom: none;
        }}
        .permissions li::before {{
            content: "";
            width: 18px;
            height: 18px;
            background: #5EDEB9;
            border-radius: 50%;
            margin-right: 12px;
            flex-shrink: 0;
            background-image: url("{check_svg}");
            background-size: 12px;
            background-repeat: no-repeat;
            background-position: center;
        }}
        .buttons {{
            display: flex;
            gap: 12px;
        }}
        button {{
            flex: 1;
            padding: 12px 20px;
            border-radius: 8px;
            font-family: 'Nunito Sans', sans-serif;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            border: none;
            transition: all 0.2s;
        }}
        .deny {{
            background: #f5f7f7;
            color: #305256;
            border: 1px solid #E4EAEB;
        }}
        .deny:hover {{ background: #E4EAEB; }}
        .approve {{
            background: #305256;
            color: white;
        }}
        .approve:hover {{
            background: #193238;
        }}
        .footer {{
            text-align: center;
            margin-top: 20px;
            font-size: 12px;
            color: #689296;
        }}
        .footer a {{
            color: #305256;
            text-decoration: none;
        }}
        .footer a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">
            <div class="logo-text">Jentic<span>One</span></div>
        </div>
        <div class="user-info">
            <div class="label">Signed in as</div>
            <div class="email">{user_email}</div>
        </div>
        <h1><span class="app-name">{app_name}</span> wants to access your account</h1>
        <p class="description">{app_description}</p>
        <div class="permissions">
            <h2>This will allow access to:</h2>
            <ul>
                {permission_items}
            </ul>
        </div>
        <div class="buttons">
            <form method="post" action="/oauth/consent" style="flex: 1; display: flex;">
                <input type="hidden" name="consent_token" value="{consent_token}">
                <input type="hidden" name="action" value="deny">
                <button type="submit" class="deny">Deny</button>
            </form>
            <form method="post" action="/oauth/consent" style="flex: 1; display: flex;">
                <input type="hidden" name="consent_token" value="{consent_token}">
                <input type="hidden" name="action" value="approve">
                <button type="submit" class="approve">Authorize</button>
            </form>
        </div>
        <div class="footer">
            Authorizing grants the application the permissions listed above.
        </div>
    </div>
</body>
</html>
"""


def _sign_payload(payload: dict[str, str | None], secret: str, *, purpose: str) -> str:
    """Encode and HMAC-sign a payload with a purpose discriminator."""
    payload["_purpose"] = purpose
    data = urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.HMAC(secret.encode(), data.encode(), hashlib.sha256).hexdigest()
    return f"{data}.{sig}"


def _verify_payload(
    token_str: str, secret: str, *, purpose: str, max_age: int
) -> dict[str, str | None]:
    """Verify and decode a signed payload, checking purpose and TTL."""
    parts = token_str.rsplit(".", 1)
    if len(parts) != 2:
        raise InvalidGrantError(f"invalid {purpose} token")
    data, sig = parts
    expected = hmac.HMAC(secret.encode(), data.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise InvalidGrantError(f"{purpose} signature invalid")
    payload: dict[str, str | None] = json.loads(urlsafe_b64decode(data))
    if payload.get("_purpose") != purpose:
        raise InvalidGrantError(f"token purpose mismatch: expected {purpose}")
    iat = payload.get("iat")
    if iat is not None:
        age = time.time() - float(iat)
        if age > max_age or age < 0:
            raise InvalidGrantError(f"{purpose} token expired")
    return payload


# Consumed consent nonce tracking (in-memory; acceptable for single-instance or
# sticky-session deploys; a shared cache backend would be needed for multi-instance).
_consumed_consent_nonces: set[str] = set()
_consumed_nonce_expiry: list[tuple[float, str]] = []
_NONCE_GC_THRESHOLD = 1000


def _consume_consent_nonce(nonce: str) -> bool:
    """Attempt to consume a consent nonce. Returns False if already consumed."""
    now = time.time()
    if len(_consumed_nonce_expiry) > _NONCE_GC_THRESHOLD:
        cutoff = now - CONSENT_STATE_MAX_AGE_SECONDS
        expired = [n for t, n in _consumed_nonce_expiry if t < cutoff]
        for n in expired:
            _consumed_consent_nonces.discard(n)
        _consumed_nonce_expiry[:] = [(t, n) for t, n in _consumed_nonce_expiry if t >= cutoff]
    if nonce in _consumed_consent_nonces:
        return False
    _consumed_consent_nonces.add(nonce)
    _consumed_nonce_expiry.append((now, nonce))
    return True


@router.get("/authorize", dependencies=[Depends(_check_rate_limit)])
async def authorize_endpoint(
    request: Request,
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query(...),
    scope: str = Query(default="openid"),
    state: str | None = Query(default=None),
    nonce: str | None = Query(default=None),
    ctx: Context = Depends(get_ctx),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
) -> RedirectResponse:
    """RFC 6749 Authorization endpoint with PKCE (S256 only).

    If an external IdP is configured, redirects to the upstream provider.
    Otherwise returns an error (direct login requires a separate credential exchange).
    """
    if not await _is_allowed_redirect_uri(redirect_uri, client_id, ctx):
        logger.warning(
            "oauth_invalid_redirect_uri",
            client_id=client_id,
            redirect_uri=redirect_uri,
        )
        return RedirectResponse(url="/error?error=invalid_redirect_uri", status_code=302)

    if response_type != "code":
        return _error_redirect(redirect_uri, "unsupported_response_type", state)

    if code_challenge_method != "S256":
        return _error_redirect(redirect_uri, "invalid_request", state, "only S256 is supported")

    # Validate requested scopes against client's allowed scopes
    allowed_scopes = await _get_client_allowed_scopes(client_id, ctx)
    if allowed_scopes is not None:
        requested = set(scope.split())
        excess = requested - allowed_scopes - {"openid", "email", "profile"}
        if excess:
            logger.warning(
                "oauth_scope_exceeds_client_allowlist",
                client_id=client_id,
                excess=sorted(excess),
            )
            return _error_redirect(
                redirect_uri, "invalid_scope", state, "requested scopes exceed allowlist"
            )

    callback_uri = _callback_uri(request, ctx.config.auth.canonical_base_url)

    internal_state = _sign_payload(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "scope": scope,
            "nonce": nonce,
            "original_state": state,
            "iat": str(int(time.time())),
        },
        ctx.config.admin.auth.jwt_secret.get_secret_value(),
        purpose="state",
    )

    idp_url = authorize_svc.get_authorize_redirect_url(
        state=internal_state,
        nonce=nonce or secrets.token_urlsafe(16),
        redirect_uri=callback_uri,
    )

    if idp_url is None:
        return _error_redirect(
            redirect_uri, "server_error", state, "no identity provider configured"
        )

    return RedirectResponse(url=idp_url, status_code=302)


@router.get(
    "/oauth/callback",
    operation_id="authorizeOauthCallback",
    name="authorize_oauth_callback",
    dependencies=[Depends(_check_rate_limit)],
)
async def oauth_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    ctx: Context = Depends(get_ctx),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
) -> RedirectResponse:
    """External IdP callback — exchanges upstream code and issues platform auth code."""
    try:
        params = _verify_payload(
            state,
            ctx.config.admin.auth.jwt_secret.get_secret_value(),
            purpose="state",
            max_age=STATE_MAX_AGE_SECONDS,
        )
    except InvalidGrantError:
        logger.warning("oauth_callback_invalid_state")
        return RedirectResponse(url="/error?error=invalid_state", status_code=302)

    client_id = params.get("client_id", "")
    original_redirect_uri = params.get("redirect_uri", "")
    code_challenge = params.get("code_challenge", "")
    scope = params.get("scope", "openid")
    nonce = params.get("nonce")
    original_state = params.get("original_state")

    callback_uri = _callback_uri(request, ctx.config.auth.canonical_base_url)
    try:
        platform_code, user_email = await authorize_svc.handle_idp_callback_with_email(
            code=code,
            redirect_uri=callback_uri,
            client_id=client_id or "",
            original_redirect_uri=original_redirect_uri or "",
            code_challenge=code_challenge or "",
            scopes=scope or "openid",
            nonce=nonce,
        )
    except UserNotAdmittedError:
        logger.warning("oauth_user_not_admitted", client_id=client_id)
        return RedirectResponse(url="/error?error=access_denied", status_code=302)
    except (InvalidGrantError, httpx.HTTPStatusError):
        logger.warning("oauth_idp_exchange_failed", client_id=client_id, exc_info=True)
        return RedirectResponse(url="/error?error=server_error", status_code=302)

    async with ctx.admin_db.session() as session:
        oauth_client = await OAuthClientRepository.get_by_client_id(session, client_id or "")

    if oauth_client is not None and oauth_client.require_consent:
        consent_nonce = secrets.token_urlsafe(32)
        consent_token = _sign_payload(
            {
                "consent_nonce": consent_nonce,
                "code": platform_code,
                "redirect_uri": original_redirect_uri,
                "original_state": original_state,
                "client_id": client_id,
                "client_name": oauth_client.name,
                "client_description": oauth_client.description,
                "scope": scope,
                "user_email": user_email,
                "iat": str(int(time.time())),
            },
            ctx.config.admin.auth.jwt_secret.get_secret_value(),
            purpose="consent",
        )
        return RedirectResponse(
            url=f"/oauth/consent?consent_token={consent_token}", status_code=302
        )

    redirect_params: dict[str, str] = {"code": platform_code}
    if original_state:
        redirect_params["state"] = original_state

    separator = "&" if "?" in (original_redirect_uri or "") else "?"
    return RedirectResponse(
        url=f"{original_redirect_uri}{separator}{urlencode(redirect_params)}", status_code=302
    )


@router.get("/error")
async def error_page(error: str = Query(default="unknown_error")) -> dict[str, str]:
    """Minimal error endpoint for browser-facing authorization failures."""
    return {"error": error}


_HIDDEN_SCOPES: frozenset[str] = frozenset({"openid"})

_OIDC_SCOPE_DESCRIPTIONS: dict[str, str] = {
    "email": "View your email address",
    "profile": "View your basic profile information",
}

_PLATFORM_SCOPE_DESCRIPTIONS: dict[str, str] = {
    AGENTS_READ: "View agents",
    AGENTS_WRITE: "Create and manage agents",
    TOOLKITS_READ: "View toolkits",
    TOOLKITS_WRITE: "Create and manage toolkits",
    CREDENTIALS_READ: "View credential metadata",
    CREDENTIALS_WRITE: "Create and manage credentials",
}


def _scope_to_permission_description(scope: str) -> str | None:
    """Map OAuth scopes to human-readable permission descriptions.

    Returns None for scopes that should not be displayed (e.g. openid).
    Falls back to the permission catalog description for platform scopes,
    or a generic label for completely unknown scopes.
    """
    if scope in _HIDDEN_SCOPES:
        return None
    if scope in _OIDC_SCOPE_DESCRIPTIONS:
        return _OIDC_SCOPE_DESCRIPTIONS[scope]
    if scope in _PLATFORM_SCOPE_DESCRIPTIONS:
        return _PLATFORM_SCOPE_DESCRIPTIONS[scope]
    perm = ALL_PERMISSIONS.get(scope)
    if perm is not None:
        return perm.description
    return f"Access: {scope}"


@router.get(
    "/oauth/consent", response_class=HTMLResponse, dependencies=[Depends(_check_rate_limit)]
)
async def consent_page(
    consent_token: str = Query(...),
    ctx: Context = Depends(get_ctx),
) -> HTMLResponse:
    """Display the OAuth consent screen."""
    try:
        params = _verify_payload(
            consent_token,
            ctx.config.admin.auth.jwt_secret.get_secret_value(),
            purpose="consent",
            max_age=CONSENT_STATE_MAX_AGE_SECONDS,
        )
    except InvalidGrantError:
        return HTMLResponse(
            content="<html><body><h1>Invalid or expired consent request</h1></body></html>",
            status_code=400,
            headers=_CONSENT_SECURITY_HEADERS,
        )

    app_name = params.get("client_name") or "Unknown Application"
    app_description = params.get("client_description") or "This application"
    user_email = params.get("user_email") or "unknown"
    scope = params.get("scope") or "openid"

    scopes = [s.strip() for s in scope.split() if s.strip()]
    implied_by_others: set[str] = set()
    for s in scopes:
        implied_by_others.update(compute_implies_transitive(s))
    visible_scopes = [s for s in scopes if s not in implied_by_others]
    permission_items = "\n".join(
        f"<li>{html_mod.escape(desc)}</li>"
        for s in visible_scopes
        if (desc := _scope_to_permission_description(s)) is not None
    )

    html = _CONSENT_PAGE_TEMPLATE.format(
        app_name=html_mod.escape(app_name),
        app_description=html_mod.escape(app_description),
        user_email=html_mod.escape(user_email),
        permission_items=permission_items,
        consent_token=html_mod.escape(consent_token),
        fonts_url=_FONTS_URL,
        check_svg=_CHECK_SVG,
    )
    return HTMLResponse(content=html, headers=_CONSENT_SECURITY_HEADERS)


@router.post("/oauth/consent", dependencies=[Depends(_check_rate_limit)])
async def consent_submit(
    consent_token: str = Form(...),
    action: str = Form(...),
    ctx: Context = Depends(get_ctx),
) -> RedirectResponse:
    """Process the consent form submission."""
    try:
        params = _verify_payload(
            consent_token,
            ctx.config.admin.auth.jwt_secret.get_secret_value(),
            purpose="consent",
            max_age=CONSENT_STATE_MAX_AGE_SECONDS,
        )
    except InvalidGrantError:
        logger.warning("oauth_consent_invalid_token")
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    consent_nonce = params.get("consent_nonce") or ""
    if not _consume_consent_nonce(consent_nonce):
        logger.warning("oauth_consent_nonce_replay", nonce=consent_nonce[:8])
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    redirect_uri = params.get("redirect_uri") or ""
    original_state = params.get("original_state")
    platform_code = params.get("code") or ""
    client_id = params.get("client_id") or ""

    if action == "deny":
        logger.info("oauth_consent_denied", client_id=client_id)
        return _error_redirect(redirect_uri, "access_denied", original_state)

    logger.info("oauth_consent_approved", client_id=client_id)
    redirect_params: dict[str, str] = {"code": platform_code}
    if original_state:
        redirect_params["state"] = original_state

    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        url=f"{redirect_uri}{separator}{urlencode(redirect_params)}", status_code=302
    )


def _error_redirect(
    redirect_uri: str, error: str, state: str | None, description: str | None = None
) -> RedirectResponse:
    params: dict[str, str] = {"error": error}
    if state:
        params["state"] = state
    if description:
        params["error_description"] = description
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(url=f"{redirect_uri}{separator}{urlencode(params)}", status_code=302)
