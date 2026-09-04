"""AuthCode+PKCE authorization endpoints with consent screen support.

Flow overview:
  GET /authorize        — validate client + redirect_uri, redirect to IdP
                          (a pending-approval client renders the
                          approval-pending page instead — see below)
  GET /oauth/callback   — verify IdP response, show consent screen (or skip)
  POST /oauth/consent   — verify consent token, issue authorization code, redirect to client
  POST /oauth/token     — exchange code + PKCE verifier + client_secret for tokens

Approval-in-flow (D-approval-in-flow, P2): a registered-but-unapproved client
at /authorize renders a live approval-pending page instead of a dead stop. The
page polls GET /oauth/approval/status (anonymous, rate limited, keyed by a
signed state blob — never a bare client_id) and, when the browser holds an
admin SPA session, offers inline approve/deny via POST /oauth/approval/decision
(a thin wrapper over the admin OAuthClientService approve/deny with the same
``oauth-clients:write`` gate). On approval the page re-runs the ORIGINAL
authorize request, which now proceeds normally to the IdP redirect; on denial
it closes the loop with a standard ``error=access_denied`` redirect when the
redirect_uri is registered for the client.
"""

from __future__ import annotations

import hashlib
import hmac
import html as html_mod
import json
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Literal
from urllib.parse import urlencode, urlsplit

import httpx
import structlog
from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.auth.core.idp import IdpClaims
from jentic_one.auth.services.authorize_service import AgentConsentOption, AuthorizeService
from jentic_one.auth.services.errors import (
    ConsentAgentNotEligibleError,
    InvalidGrantError,
    RateLimitExceededError,
    UserNotAdmittedError,
)
from jentic_one.auth.services.oauth_grant_service import OAuthGrantService
from jentic_one.auth.web.ratelimit import client_ip, get_auth_backend
from jentic_one.auth.web.schemas.authorize import (
    OAuthApprovalDecisionRequest,
    OAuthApprovalStatusResponse,
)
from jentic_one.shared.auth.identity import Identity
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
from jentic_one.shared.models.oauth_clients import OAuthClientApprovalStatus, OAuthConsentModel
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.scopes import OIDC_PASSTHROUGH_SCOPES
from jentic_one.shared.state.backend import SharedStateBackend
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.deps import get_ctx
from jentic_one.shared.web.sensitive import SENSITIVE

logger = structlog.get_logger(__name__)

router = APIRouter()


def _get_authorize_limiter(request: Request, ctx: Context) -> RateLimiter:
    limiter: RateLimiter | None = getattr(request.app.state, "_authorize_limiter", None)
    if limiter is not None:
        return limiter
    cfg = ctx.config.auth.oauth_rate_limit
    backend = get_auth_backend(request)
    limiter = RateLimiter(backend, default_rpm=cfg.authorize_rpm, burst=cfg.authorize_burst)
    request.app.state._authorize_limiter = limiter
    return limiter


async def _check_rate_limit(request: Request, ctx: Context = Depends(get_ctx)) -> None:
    """Per-client+IP rate limiter for unauthenticated authorization endpoints."""
    trusted = frozenset(ctx.config.auth.oauth_rate_limit.trusted_proxies)
    client_id = request.query_params.get("client_id")
    ip = client_ip(request, trusted)
    key = f"{client_id}:{ip}" if client_id else ip
    limiter = _get_authorize_limiter(request, ctx)
    outcome = await limiter.acquire(key)
    if not outcome.allowed:
        raise RateLimitExceededError(retry_after=outcome.retry_after_s)


def _get_approval_status_limiter(request: Request, ctx: Context) -> RateLimiter:
    limiter: RateLimiter | None = getattr(request.app.state, "_approval_status_limiter", None)
    if limiter is not None:
        return limiter
    cfg = ctx.config.auth.oauth_rate_limit
    backend = get_auth_backend(request)
    # Own bucket namespace (mirrors the DCR door): the approval-pending page
    # polls this endpoint for minutes at a time, and a bare-IP key in the
    # shared store would otherwise collide with /authorize's fallback bucket —
    # steady polling must not drain the /authorize quota or vice versa.
    limiter = RateLimiter(
        backend,
        default_rpm=cfg.approval_status_rpm,
        burst=cfg.approval_status_burst,
        namespace="oauth-approval-status",
    )
    request.app.state._approval_status_limiter = limiter
    return limiter


async def _check_approval_status_rate_limit(
    request: Request, ctx: Context = Depends(get_ctx)
) -> None:
    """Per-IP rate limiter for the anonymous approval-status poll.

    Keyed by bare IP: the only other request input is the signed state blob,
    and a self-chosen key component would let one host sidestep the bucket by
    re-minting blobs (every /authorize render hands out a fresh one).
    """
    trusted = frozenset(ctx.config.auth.oauth_rate_limit.trusted_proxies)
    ip = client_ip(request, trusted)
    limiter = _get_approval_status_limiter(request, ctx)
    outcome = await limiter.acquire(ip)
    if not outcome.allowed:
        raise RateLimitExceededError(retry_after=outcome.retry_after_s)


def _is_platform_client(client_id: str, ctx: Context) -> bool:
    """Check if client_id is a known platform client from config."""
    return any(pc.client_id == client_id for pc in ctx.config.auth.platform_clients)


def _platform_client_allows_redirect(redirect_uri: str, client_id: str, ctx: Context) -> bool:
    """Check if a platform client's config allows the given redirect_uri."""
    for pc in ctx.config.auth.platform_clients:
        if pc.client_id == client_id:
            return redirect_uri in pc.redirect_uris
    return False


async def _get_cached_oauth_client(
    request: Request, client_id: str, ctx: Context
) -> OAuthClientView | None:
    """Return the OAuth client view for ``client_id``, cached per request.

    /authorize touches the same client row three times (redirect-URI validation,
    scope-allowlist check, consent decision); this collapses them into one DB
    read. ``None`` in the cache means "confirmed unknown" — a repeat lookup for
    the same client_id in the same request skips the DB round-trip.
    """
    cache: dict[str, OAuthClientView | None] | None = getattr(
        request.state, "_oauth_client_cache", None
    )
    if cache is None:
        cache = {}
        request.state._oauth_client_cache = cache
    if client_id not in cache:
        cache[client_id] = await OAuthClientService(ctx).get_by_client_id(client_id)
    return cache[client_id]


def _client_gate_passes(client: OAuthClientView) -> bool:
    """The D7 client gate: only ``active`` AND ``approved`` rows may proceed.

    Checked at /authorize entry and *re-checked* mid-flow (IdP callback,
    consent submit) so a client denied or deactivated inside the signed-state
    window cannot walk the rest of the flow to a minted code.
    """
    return client.active and client.approval_status == OAuthClientApprovalStatus.APPROVED.value


async def _is_allowed_redirect_uri(
    request: Request, redirect_uri: str, client_id: str, ctx: Context
) -> bool:
    """Validate redirect_uri against platform clients (config) or registered clients (DB).

    Platform clients are validated against their configured redirect_uris.
    Third-party clients are looked up in the oauth_clients registry.
    Unknown client_ids are rejected, and so are unapproved (pending/denied)
    rows — the D7 approval gate fails closed on the existing error path.
    (The human "awaiting approval" page is future work.)
    """
    if _is_platform_client(client_id, ctx):
        return _platform_client_allows_redirect(redirect_uri, client_id, ctx)
    client = await _get_cached_oauth_client(request, client_id, ctx)
    if client is None or not _client_gate_passes(client):
        return False
    return redirect_uri in client.redirect_uris


async def _get_client_allowed_scopes(
    request: Request, client_id: str, ctx: Context
) -> frozenset[str] | None:
    """Return allowed scopes for a registered client, or None for platform clients."""
    if _is_platform_client(client_id, ctx):
        return None
    client = await _get_cached_oauth_client(request, client_id, ctx)
    if client is None or client.allowed_scopes is None:
        return None
    return frozenset(client.allowed_scopes)


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


def get_oauth_grant_service(ctx: Context = Depends(get_ctx)) -> OAuthGrantService:
    return OAuthGrantService(ctx)


STATE_MAX_AGE_SECONDS = 600
CONSENT_STATE_MAX_AGE_SECONDS = 300
#: TTL for the signed approval-state blob carried by the approval-pending page
#: (poll + inline-decision key). Deliberately the same window as the IdP-leg
#: ``state`` — the page self-heals past expiry by re-running /authorize, which
#: mints a fresh blob while the client stays pending.
APPROVAL_STATE_MAX_AGE_SECONDS = STATE_MAX_AGE_SECONDS

#: How often the approval-pending page polls /oauth/approval/status. 5 s keeps
#: a single tab at 12 rpm, well inside the endpoint's own rate bucket.
_APPROVAL_POLL_INTERVAL_MS = 5000

#: localStorage key the operator SPA keeps its bearer session under. The
#: approval-pending page is served from the same origin as the SPA in the
#: default (combined) deployment, so page script can present that token to /me
#: and the decision endpoint. Kept in lockstep with ``ui/src/shared/auth``.
_SPA_TOKEN_STORAGE_KEY = "jentic-one.access_token"

#: SPA route of the OAuth-client approval queue (Settings → queue tab), used
#: for the "ask your admin" deep link. Kept in lockstep with the UI's
#: ``oauthQueue`` link target (``ui/src/shared/lib/agentStream.tsx``) and the
#: ``/app`` SPA mount (``shared/web/static.py``).
_APPROVAL_QUEUE_SPA_PATH = "/app/settings?tab=queue"

_CONSENT_SECURITY_HEADERS: dict[str, str] = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
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


_AWAITING_APPROVAL_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Awaiting approval | Jentic One</title>
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
            max-width: 420px;
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
            line-height: 1.5;
            text-align: center;
            margin-bottom: 16px;
        }}
        .panel {{
            background: #f5f7f7;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 16px;
        }}
        .panel p {{
            color: #305256;
            font-size: 13px;
            line-height: 1.5;
            margin-bottom: 10px;
        }}
        .copy-row {{
            display: flex;
            gap: 8px;
        }}
        .copy-row input {{
            flex: 1;
            min-width: 0;
            padding: 8px 10px;
            border: 1px solid #E4EAEB;
            border-radius: 6px;
            font-family: 'Nunito Sans', sans-serif;
            font-size: 12px;
            color: #305256;
            background: white;
        }}
        .buttons {{
            display: flex;
            gap: 12px;
        }}
        button {{
            padding: 10px 16px;
            border-radius: 8px;
            font-family: 'Nunito Sans', sans-serif;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            border: none;
            transition: all 0.2s;
        }}
        button:disabled {{ opacity: 0.6; cursor: default; }}
        .buttons button {{ flex: 1; }}
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
        .approve:hover {{ background: #193238; }}
        .copy {{
            background: white;
            color: #305256;
            border: 1px solid #E4EAEB;
        }}
        .copy:hover {{ background: #E4EAEB; }}
        .status-line {{
            text-align: center;
            font-size: 13px;
            color: #689296;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">
            <div class="logo-text">Jentic<span>One</span></div>
        </div>
        <h1><span class="app-name">{app_name}</span> is awaiting administrator approval</h1>
        <p class="description">
            This page checks automatically and continues the connection
            as soon as an administrator approves it.
        </p>
        <div class="panel" id="anon-panel">
            <p>Ask your Jentic One admin to approve
                <span class="app-name">{app_name}</span> in the approval queue:</p>
            <div class="copy-row">
                <input id="queue-link" readonly value="{queue_url}">
                <button type="button" class="copy" id="copy-link">Copy</button>
            </div>
        </div>
        <div class="panel" id="admin-panel" hidden>
            <p>You are signed in as an administrator &mdash; you can decide now:</p>
            <div class="buttons">
                <button type="button" class="deny" id="btn-deny">Deny</button>
                <button type="button" class="approve" id="btn-approve">Approve</button>
            </div>
        </div>
        <div class="status-line" id="approval-status">Waiting for approval&hellip;</div>
    </div>
    <script id="approval-config" type="application/json">{config_json}</script>
    {page_script}
</body>
</html>
"""

# Inline behaviour for the approval-pending page. Kept as a plain string (not a
# .format template) so its braces need no doubling; every dynamic value comes
# from the JSON <script id="approval-config"> block, which is the single
# escaped seam between server data and page script.
_APPROVAL_PENDING_SCRIPT = """<script>
(function () {
    "use strict";
    var cfg = JSON.parse(document.getElementById("approval-config").textContent);
    var statusEl = document.getElementById("approval-status");
    var adminPanel = document.getElementById("admin-panel");
    var anonPanel = document.getElementById("anon-panel");
    var settled = false;

    function navigate(url) {
        settled = true;
        window.location.replace(url);
    }

    function isHttpUrl(url) {
        return typeof url === "string" &&
            (url.indexOf("https://") === 0 || url.indexOf("http://") === 0);
    }

    function onStatus(status) {
        if (settled) { return; }
        if (status === "approved") {
            statusEl.textContent = "Approved — continuing…";
            // Re-run the ORIGINAL authorize request; the client gate now
            // passes, so the server 302s to the identity provider.
            navigate(cfg.resume_url);
        } else if (status === "denied") {
            if (isHttpUrl(cfg.deny_redirect)) {
                // Standard OAuth closure for the client: error=access_denied
                // on its own registered redirect_uri.
                navigate(cfg.deny_redirect);
            } else {
                settled = true;
                statusEl.textContent =
                    "An administrator denied this application. " +
                    "You can close this page.";
            }
        }
    }

    function poll() {
        if (settled) { return; }
        fetch(cfg.status_url, { headers: { Accept: "application/json" } })
            .then(function (resp) {
                if (resp.status === 400) {
                    // Signed state expired — re-run /authorize to mint a
                    // fresh one (re-renders this page while still pending).
                    navigate(cfg.resume_url);
                    return null;
                }
                return resp.ok ? resp.json() : null;
            })
            .then(function (body) {
                if (body && body.status) { onStatus(body.status); }
            })
            .catch(function () { /* transient — retry next tick */ });
    }
    window.setInterval(poll, cfg.poll_ms);
    poll();

    // Admin detection: the operator SPA keeps its session as a bearer token
    // in same-origin localStorage. If one is present and /me confirms the
    // oauth-clients:write permission (or org:admin), reveal the inline
    // decision panel. Anything else keeps the anonymous panel.
    var token = null;
    try { token = window.localStorage.getItem(cfg.token_key); } catch (e) { /* blocked */ }
    if (token) {
        fetch(cfg.me_url, { headers: { Authorization: "Bearer " + token } })
            .then(function (resp) { return resp.ok ? resp.json() : null; })
            .then(function (me) {
                var scopes = (me && me.scopes) || [];
                if (me && (me.admin === true ||
                        scopes.indexOf("oauth-clients:write") !== -1)) {
                    adminPanel.hidden = false;
                    anonPanel.hidden = true;
                }
            })
            .catch(function () { /* stay anonymous */ });
    }

    function decide(action, button) {
        button.disabled = true;
        fetch(cfg.decision_url, {
            method: "POST",
            headers: {
                Authorization: "Bearer " + token,
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ state: cfg.state, action: action }),
        })
            .then(function (resp) {
                if (!resp.ok) { throw new Error("decision failed"); }
                return resp.json();
            })
            .then(function (body) { onStatus(body.status); })
            .catch(function () {
                button.disabled = false;
                statusEl.textContent =
                    "Decision failed — try again or use the approval queue.";
            });
    }
    document.getElementById("btn-approve").addEventListener("click", function () {
        decide("approve", this);
    });
    document.getElementById("btn-deny").addEventListener("click", function () {
        decide("deny", this);
    });

    document.getElementById("copy-link").addEventListener("click", function () {
        var input = document.getElementById("queue-link");
        input.select();
        var self = this;
        try {
            navigator.clipboard.writeText(input.value).then(function () {
                self.textContent = "Copied";
            });
        } catch (e) {
            document.execCommand("copy");
            self.textContent = "Copied";
        }
    });
})();
</script>"""


# The agent-picker consent variant (consent_model='agent' clients only —
# the 'user' template above stays byte-identical). Differences: the
# redirect-URI origin is rendered prominently (client-claimed names are
# untrusted — phishing counter), and the permission list is replaced by an
# agent picker whose per-agent scope lists show the D2 intersection: scopes
# the agent lacks render greyed-out so the user sees the ceiling, and the
# invariant (granted ≤ agent's live scopes) holds by construction.
_AGENT_CONSENT_PAGE_TEMPLATE = """<!DOCTYPE html>
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
            max-width: 440px;
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
            margin-bottom: 4px;
            color: #0E1A1D;
            text-align: center;
            line-height: 1.4;
        }}
        .app-name {{ color: #305256; font-weight: 700; }}
        .origin {{
            text-align: center;
            font-size: 13px;
            font-weight: 700;
            color: #0E1A1D;
            background: #EDF3F2;
            border-radius: 6px;
            padding: 6px 10px;
            margin: 8px auto 12px;
            display: table;
            max-width: 100%;
            word-break: break-all;
        }}
        .description {{
            color: #689296;
            font-size: 14px;
            margin-bottom: 16px;
            line-height: 1.5;
            text-align: center;
        }}
        .user-info {{
            text-align: center;
            margin-bottom: 16px;
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
        .agents {{ margin-bottom: 20px; }}
        .agents h2 {{
            font-size: 12px;
            font-weight: 600;
            color: #305256;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .agent {{
            display: block;
            border: 1px solid #E4EAEB;
            border-radius: 8px;
            padding: 12px 14px;
            margin-bottom: 10px;
            cursor: pointer;
        }}
        .agent:has(input:checked) {{
            border-color: #305256;
            background: #f5f7f7;
        }}
        .agent-header {{ display: flex; align-items: center; gap: 10px; }}
        .agent-name {{
            font-size: 14px;
            font-weight: 700;
            color: #0E1A1D;
        }}
        .agent-scopes {{
            list-style: none;
            margin-top: 8px;
            font-size: 13px;
            color: #305256;
        }}
        .agent-scopes li {{ padding: 3px 0; }}
        .agent-scopes li.granted::before {{
            content: "";
            display: inline-block;
            width: 14px;
            height: 14px;
            background: #5EDEB9;
            border-radius: 50%;
            margin-right: 8px;
            vertical-align: -2px;
            background-image: url("{check_svg}");
            background-size: 10px;
            background-repeat: no-repeat;
            background-position: center;
        }}
        .agent-scopes li.lacking {{
            color: #A9BCBE;
        }}
        .agent-scopes li.lacking::before {{
            content: "";
            display: inline-block;
            width: 14px;
            height: 14px;
            background: #E4EAEB;
            border-radius: 50%;
            margin-right: 8px;
            vertical-align: -2px;
        }}
        .buttons {{ display: flex; gap: 12px; }}
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
        .approve {{ background: #305256; color: white; }}
        .approve:hover {{ background: #193238; }}
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
        <div class="user-info">
            <div class="label">Signed in as</div>
            <div class="email">{user_email}</div>
        </div>
        <h1><span class="app-name">{app_name}</span> wants to connect to one of your agents</h1>
        <div class="origin">{redirect_origin}</div>
        <p class="description">{app_description}</p>
        <form method="post" action="/oauth/consent">
            <div class="agents">
                <h2>Choose the agent this application may act through:</h2>
                {agent_options}
            </div>
            <input type="hidden" name="consent_token" value="{consent_token}">
            <div class="buttons">
                <button type="submit" name="action" value="deny"
                        class="deny" formnovalidate>Deny</button>
                <button type="submit" name="action" value="approve"
                        class="approve">Authorize</button>
            </div>
        </form>
        <div class="footer">
            The application will act only through the selected agent,
            limited to the permissions shown.
        </div>
    </div>
</body>
</html>
"""

_AGENT_OPTION_TEMPLATE = """<label class="agent">
    <span class="agent-header">
        <input type="radio" name="agent_id" value="{agent_id}" required{checked}>
        <span class="agent-name">{agent_name}</span>
    </span>
    <ul class="agent-scopes">
        {scope_items}
    </ul>
</label>"""

# Zero active agents → an empty-state page, HTTP 200, no code minted.
_NO_AGENTS_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>No agents available | Jentic One</title>
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
        .app-name {{ color: #305256; font-weight: 700; }}
        .description {{
            color: #689296;
            font-size: 14px;
            line-height: 1.5;
            text-align: center;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">
            <div class="logo-text">Jentic<span>One</span></div>
        </div>
        <h1><span class="app-name">{app_name}</span> connects through an agent
            &mdash; you don't have one yet</h1>
        <p class="description">
            This application acts through one of your approved agents.
            Register an agent (see the Jentic One agent-registration docs),
            have an administrator approve it, then retry the connection
            from your application.
        </p>
    </div>
</body>
</html>
"""


def _derive_key(master_secret: str, purpose: str) -> str:
    """Derive a purpose-specific signing key from the master secret via HMAC."""
    return hmac.HMAC(
        master_secret.encode(), f"oauth-{purpose}".encode(), hashlib.sha256
    ).hexdigest()


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


def _get_consent_backend(request: Request) -> SharedStateBackend:
    return get_auth_backend(request)


# ---------- approval-in-flow helpers (P2) ----------


def _approval_state_key(ctx: Context) -> str:
    """Signing key for the approval-state blob.

    Same signer/mechanism as the IdP-leg ``state`` but a distinct derived key
    AND a distinct ``_purpose`` discriminator, so an approval blob can never be
    replayed into /oauth/callback (or vice versa).
    """
    return _derive_key(ctx.config.admin.auth.jwt_secret.get_secret_value(), "approval")


def _mint_approval_state(
    ctx: Context,
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    scope: str,
    state: str | None,
    nonce: str | None,
) -> str:
    """Sign the ORIGINAL authorize parameters into the approval-state blob.

    The blob is the only key the anonymous status endpoint accepts — never a
    bare client_id — and its ``iat`` bounds its life to
    :data:`APPROVAL_STATE_MAX_AGE_SECONDS`.
    """
    return _sign_payload(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "scope": scope,
            "nonce": nonce,
            "original_state": state,
            "iat": str(int(time.time())),
        },
        _approval_state_key(ctx),
        purpose="approval",
    )


def _approval_tri_state(
    client: OAuthClientView | None,
) -> Literal["pending", "approved", "denied"]:
    """Collapse a client row into the poll tri-state.

    ``approved`` only when the full D7 gate passes (approved AND active), so a
    kill-switched client is never announced as ready. A missing row reads as
    ``pending`` — rows are never deleted in normal operation, and answering
    anything else would turn the endpoint into a deletion oracle.
    """
    if client is None:
        return "pending"
    if client.approval_status == OAuthClientApprovalStatus.DENIED.value:
        return "denied"
    if _client_gate_passes(client):
        return "approved"
    return "pending"


def _safe_deny_redirect(
    client: OAuthClientView, redirect_uri: str, state: str | None
) -> str | None:
    """The access_denied redirect for the deny arm — only when provably safe.

    Safe means the request's redirect_uri is in the client's OWN registered
    set (registration already enforces https/loopback-http schemes) and parses
    as an http(s) URL. Anything else returns None and the page renders a
    terminal message instead of navigating.
    """
    if redirect_uri not in client.redirect_uris:
        return None
    if urlsplit(redirect_uri).scheme not in ("http", "https"):
        return None
    params: dict[str, str] = {"error": "access_denied"}
    if state:
        params["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return f"{redirect_uri}{separator}{urlencode(params)}"


def _render_approval_pending_page(
    request: Request,
    ctx: Context,
    *,
    client: OAuthClientView,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str,
    state: str | None,
    nonce: str | None,
) -> HTMLResponse:
    """Render the approval-pending page for an unapproved client.

    All dynamic values reach the page script through one escaped JSON block;
    the client's self-chosen name only ever lands in HTML-escaped text nodes.
    """
    approval_state = _mint_approval_state(
        ctx,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        scope=scope,
        state=state,
        nonce=nonce,
    )

    # The resume leg re-runs the ORIGINAL authorize request client-side; once
    # the admin approves, this URL 302s to the IdP like any approved client.
    resume_params: dict[str, str] = {
        "response_type": response_type,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
    }
    if state:
        resume_params["state"] = state
    if nonce:
        resume_params["nonce"] = nonce
    resume_url = f"/authorize?{urlencode(resume_params)}"

    base_url = ctx.config.auth.canonical_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    queue_url = f"{base_url}{_APPROVAL_QUEUE_SPA_PATH}"

    page_config = {
        "state": approval_state,
        "status_url": f"/oauth/approval/status?{urlencode({'st': approval_state})}",
        "decision_url": "/oauth/approval/decision",
        "me_url": "/me",
        "resume_url": resume_url,
        "deny_redirect": _safe_deny_redirect(client, redirect_uri, state),
        "poll_ms": _APPROVAL_POLL_INTERVAL_MS,
        "token_key": _SPA_TOKEN_STORAGE_KEY,
    }
    # \u003c-escape so attacker-influenced values (redirect_uri rides into
    # deny_redirect/resume_url) can never close the JSON <script> block.
    config_json = json.dumps(page_config).replace("<", "\\u003c")

    html = _AWAITING_APPROVAL_PAGE_TEMPLATE.format(
        app_name=html_mod.escape(client.name),
        queue_url=html_mod.escape(queue_url, quote=True),
        config_json=config_json,
        page_script=_APPROVAL_PENDING_SCRIPT,
        fonts_url=_FONTS_URL,
    )
    return HTMLResponse(content=html, headers=_CONSENT_SECURITY_HEADERS)


@router.get("/authorize", dependencies=[Depends(_check_rate_limit)], response_model=None)
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
) -> RedirectResponse | HTMLResponse:
    """RFC 6749 Authorization endpoint with PKCE (S256 only).

    If an external IdP is configured, redirects to the upstream provider.
    Otherwise returns an error (direct login requires a separate credential exchange).
    """
    # D7 approval gate, now approval-in-flow (P2): a registered-but-unapproved
    # client renders a live approval-pending page — NEVER an OAuth error
    # redirect (a hard authorize-time rejection bricks strict clients). The
    # page carries the original authorize parameters in a signed state blob,
    # polls the minimal status endpoint, offers inline approve/deny to an
    # admin browser session, and auto-continues on approval by re-running this
    # request. The initial render deliberately does not distinguish pending
    # from denied (deny is reversible); a denied client resolves through the
    # poll into the standard access_denied redirect when safe.
    if not _is_platform_client(client_id, ctx):
        unapproved = await _get_cached_oauth_client(request, client_id, ctx)
        if (
            unapproved is not None
            and unapproved.approval_status != OAuthClientApprovalStatus.APPROVED.value
        ):
            logger.info(
                "oauth_client_awaiting_approval_page",
                client_id=client_id,
                approval_status=unapproved.approval_status,
            )
            return _render_approval_pending_page(
                request,
                ctx,
                client=unapproved,
                response_type=response_type,
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                scope=scope,
                state=state,
                nonce=nonce,
            )

    if not await _is_allowed_redirect_uri(request, redirect_uri, client_id, ctx):
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
    allowed_scopes = await _get_client_allowed_scopes(request, client_id, ctx)
    if allowed_scopes is not None:
        requested = set(scope.split())
        excess = requested - allowed_scopes - OIDC_PASSTHROUGH_SCOPES
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
        _derive_key(ctx.config.admin.auth.jwt_secret.get_secret_value(), "state"),
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
    "/oauth/approval/status",
    dependencies=[Depends(_check_approval_status_rate_limit)],
    summary="Poll client approval status (approval-pending page)",
    responses={
        400: {"description": "Malformed, tampered, or expired approval-state blob."},
    },
)
async def approval_status_endpoint(
    request: Request,
    response: Response,
    st: str = Query(..., description="Signed approval-state blob minted by /authorize"),
    ctx: Context = Depends(get_ctx),
) -> OAuthApprovalStatusResponse:
    """Minimal tri-state poll for the approval-pending page.

    Anonymous but keyed by the signed approval-state blob — never a bare
    client_id, so the endpoint cannot be used to enumerate registrations. Any
    verification failure (bad signature, wrong purpose, expired ``iat``,
    malformed blob) is a 400 ``invalid_grant``; the page reacts to a 400 by
    re-running /authorize, which mints a fresh blob. The response carries ONLY
    the tri-state — no names, redirect URIs, or metadata.
    """
    params = _verify_payload(
        st,
        _approval_state_key(ctx),
        purpose="approval",
        max_age=APPROVAL_STATE_MAX_AGE_SECONDS,
    )
    client = await _get_cached_oauth_client(request, str(params.get("client_id") or ""), ctx)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return OAuthApprovalStatusResponse(status=_approval_tri_state(client))


@router.post(
    "/oauth/approval/decision",
    summary="Approve or deny a pending client inline (approval-pending page)",
    responses={
        400: {"description": "Malformed, tampered, or expired approval-state blob."},
    },
)
async def approval_decision_endpoint(
    request: Request,
    response: Response,
    body: OAuthApprovalDecisionRequest,
    identity: Identity = get_current_identity(required_permissions=["oauth-clients:write"]),
    ctx: Context = Depends(get_ctx),
) -> OAuthApprovalStatusResponse:
    """Thin wrapper over the admin approval path for the approval-pending page.

    Authorization is byte-identical to ``POST /admin/oauth-clients/{id}:approve``
    / ``:deny`` (``oauth-clients:write``, org:admin implies it) and the
    decision itself is the SAME ``OAuthClientService.approve``/``deny`` calls —
    same audit records, same D7 active/approval_status coupling; this endpoint
    only translates the signed state blob into the client row. CSRF posture
    matches the consent POST: no ambient credential is honored — the browser
    must explicitly present the SPA bearer token, which a cross-site form
    cannot do.
    """
    params = _verify_payload(
        body.state,
        _approval_state_key(ctx),
        purpose="approval",
        max_age=APPROVAL_STATE_MAX_AGE_SECONDS,
    )
    client_id = str(params.get("client_id") or "")
    svc = OAuthClientService(ctx)
    client = await svc.get_by_client_id(client_id)
    if client is None:
        raise InvalidGrantError("unknown client in approval state")
    if body.action == "approve":
        view = await svc.approve(client.id, identity=identity)
    else:
        view = await svc.deny(client.id, identity=identity)
    logger.info(
        "oauth_client_inline_decision",
        client_id=client_id,
        action=body.action,
        actor_id=identity.sub,
    )
    response.headers["Cache-Control"] = "no-store"
    return OAuthApprovalStatusResponse(status=_approval_tri_state(view))


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
            _derive_key(ctx.config.admin.auth.jwt_secret.get_secret_value(), "state"),
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

    oauth_client = await _get_cached_oauth_client(request, client_id or "", ctx)
    if oauth_client is not None and not _client_gate_passes(oauth_client):
        # Mid-flow D7 re-check: the gate at /authorize entry only covers the
        # start of the signed-state window — a client denied or deactivated
        # while the user is at the IdP must not reach consent or mint a code.
        # Browser-facing human error, never an OAuth redirect to the client
        # (D7: clients can't observe browser-side rejections).
        logger.warning("oauth_client_gate_failed_midflow", client_id=client_id, stage="callback")
        return RedirectResponse(url="/error?error=access_denied", status_code=302)
    # Third-party clients always require consent regardless of the client's
    # require_consent flag: consent-skip is a first-party trust decision that
    # only platform clients (configured operator-side, not admin-registered)
    # can make. The require_consent field on registered clients is retained
    # for future use but currently has no effect on the third-party path.
    needs_consent = oauth_client is not None

    if needs_consent and oauth_client is not None:
        # Do NOT provision the local user yet — third-party consent must gate
        # account creation so a "Deny" doesn't leave behind a user row and
        # external-identity link the user never approved. Exchange the IdP code
        # for claims only; the consent handle carries the claims and the
        # approve-path provisions from them.
        try:
            claims = await authorize_svc.exchange_idp_code_for_claims(
                code=code,
                redirect_uri=callback_uri,
            )
        except (InvalidGrantError, httpx.HTTPStatusError):
            logger.warning("oauth_idp_exchange_failed", client_id=client_id, exc_info=True)
            return RedirectResponse(url="/error?error=server_error", status_code=302)

        consent_handle = secrets.token_urlsafe(32)
        payload_json = json.dumps(
            {
                "claims": {
                    "external_subject": claims.external_subject,
                    "email": claims.email,
                    "email_verified": claims.email_verified,
                    "first_name": claims.first_name,
                    "last_name": claims.last_name,
                },
                "redirect_uri": original_redirect_uri,
                "original_state": original_state,
                "client_id": client_id,
                "code_challenge": code_challenge,
                "scope": scope,
                "nonce": nonce,
                "client_name": oauth_client.name,
                "client_description": oauth_client.description,
                "user_email": claims.email,
                "iat": int(time.time()),
            }
        ).encode()
        backend = _get_consent_backend(request)
        await backend.set(
            f"consent-handle:{consent_handle}",
            payload_json,
            ttl_s=float(CONSENT_STATE_MAX_AGE_SECONDS),
        )
        return RedirectResponse(url=f"/oauth/consent?ch={consent_handle}", status_code=302)

    try:
        platform_code, _email = await authorize_svc.handle_idp_callback_with_email(
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


async def _load_consent_handle(ch: str, request: Request) -> dict[str, object] | None:
    """Load consent params for a handle from the shared state backend."""
    backend = _get_consent_backend(request)
    raw = await backend.get(f"consent-handle:{ch}")
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


async def _consume_consent_handle(ch: str, request: Request) -> bool:
    """Atomically mark a consent handle as used; returns False on replay."""
    backend = _get_consent_backend(request)
    return await backend.set_if_absent(
        f"consent-handle-used:{ch}", b"1", ttl_s=float(CONSENT_STATE_MAX_AGE_SECONDS)
    )


def _redirect_origin(redirect_uri: str) -> str:
    """The redirect-URI origin, rendered prominently on the agent consent page.

    Client-claimed names are untrusted (a phishing counter) — the origin is
    the one client-controlled string the user can actually verify.
    """
    parts = urlsplit(redirect_uri)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return redirect_uri


def _claims_from_params(params: dict[str, object]) -> IdpClaims | None:
    """Re-hydrate the IdP claims stored on the consent handle."""
    claims_data = params.get("claims")
    if not isinstance(claims_data, dict):
        return None
    return IdpClaims(
        external_subject=str(claims_data.get("external_subject") or ""),
        email=str(claims_data.get("email") or ""),
        email_verified=bool(claims_data.get("email_verified") or False),
        first_name=str(claims_data.get("first_name") or ""),
        last_name=str(claims_data.get("last_name") or ""),
    )


def _effective_agent_scopes(
    requested: list[str],
    allowlist: frozenset[str] | None,
    agent_scopes: frozenset[str],
) -> list[str]:
    """The D2 grant-scope intersection: requested ∩ client allowlist ∩ agent live scopes.

    ``openid``/OIDC passthrough scopes are stripped first (D11): agent-bound
    grants carry no OIDC identity, so they must never enter the granted set.
    Order follows the request so the consent page and the grant row agree.
    """
    effective = [s for s in requested if s not in OIDC_PASSTHROUGH_SCOPES]
    if allowlist is not None:
        effective = [s for s in effective if s in allowlist]
    return [s for s in effective if s in agent_scopes]


def _render_agent_options(
    agents: list[AgentConsentOption],
    candidate_scopes: list[str],
) -> str:
    """Render the agent picker: one radio per active agent.

    Each agent shows the candidate scope set (requested ∩ client allowlist,
    OIDC stripped) marked granted/lacking against its live scopes — the user
    sees the ceiling; the submit path recomputes the math server-side.
    """
    blocks: list[str] = []
    for idx, agent in enumerate(agents):
        items: list[str] = []
        for scope_name in candidate_scopes:
            desc = _scope_to_permission_description(scope_name)
            if desc is None:
                continue
            if scope_name in agent.scopes:
                items.append(f'<li class="granted">{html_mod.escape(desc)}</li>')
            else:
                items.append(
                    f'<li class="lacking">{html_mod.escape(desc)}'
                    " &mdash; not granted (agent lacks this scope)</li>"
                )
        if not items:
            items.append('<li class="lacking">No requested permissions available</li>')
        blocks.append(
            _AGENT_OPTION_TEMPLATE.format(
                agent_id=html_mod.escape(agent.id),
                agent_name=html_mod.escape(agent.name),
                scope_items="\n        ".join(items),
                checked=" checked" if idx == 0 and len(agents) == 1 else "",
            )
        )
    return "\n".join(blocks)


@router.get(
    "/oauth/consent", response_class=HTMLResponse, dependencies=[Depends(_check_rate_limit)]
)
async def consent_page(
    request: Request,
    ch: str = Query(..., description="Opaque consent-flow handle"),
    ctx: Context = Depends(get_ctx),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
) -> HTMLResponse:
    """Display the OAuth consent screen."""
    params = await _load_consent_handle(ch, request)
    if params is None:
        return HTMLResponse(
            content="<html><body><h1>Invalid or expired consent request</h1></body></html>",
            status_code=400,
            headers=_CONSENT_SECURITY_HEADERS,
        )

    app_name = str(params.get("client_name") or "Unknown Application")
    app_description = str(params.get("client_description") or "This application")
    user_email = str(params.get("user_email") or "unknown")
    scope = str(params.get("scope") or "openid")

    client_id = str(params.get("client_id") or "")
    oauth_client: OAuthClientView | None = None
    if client_id and not _is_platform_client(client_id, ctx):
        oauth_client = await _get_cached_oauth_client(request, client_id, ctx)
    if oauth_client is not None and oauth_client.consent_model == OAuthConsentModel.AGENT.value:
        return await _render_agent_consent_page(
            params,
            oauth_client=oauth_client,
            consent_token=ch,
            authorize_svc=authorize_svc,
        )

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
        consent_token=html_mod.escape(ch),
        fonts_url=_FONTS_URL,
        check_svg=_CHECK_SVG,
    )
    return HTMLResponse(content=html, headers=_CONSENT_SECURITY_HEADERS)


async def _render_agent_consent_page(
    params: dict[str, object],
    *,
    oauth_client: OAuthClientView,
    consent_token: str,
    authorize_svc: AuthorizeService,
) -> HTMLResponse:
    """The agent-picker consent variant for ``consent_model='agent'`` clients.

    Lists only the consenting user's own ``status='active'`` agents; zero
    agents (or an unresolvable user — deferred provisioning means the row may
    not exist yet) renders the empty-state page with no code minted. The
    user identity is resolved read-only: rendering consent must not create a
    user row (the Deny contract).
    """
    app_name = str(params.get("client_name") or "Unknown Application")
    app_description = str(params.get("client_description") or "This application")
    user_email = str(params.get("user_email") or "unknown")
    scope = str(params.get("scope") or "")
    redirect_uri = str(params.get("redirect_uri") or "")

    claims = _claims_from_params(params)
    user_id = await authorize_svc.resolve_existing_user_id(claims) if claims else None
    agents = await authorize_svc.list_consentable_agents(user_id) if user_id else []
    if not agents:
        html = _NO_AGENTS_PAGE_TEMPLATE.format(
            app_name=html_mod.escape(app_name),
            fonts_url=_FONTS_URL,
        )
        return HTMLResponse(content=html, headers=_CONSENT_SECURITY_HEADERS)

    requested = [s.strip() for s in scope.split() if s.strip()]
    allowlist = (
        frozenset(oauth_client.allowed_scopes) if oauth_client.allowed_scopes is not None else None
    )
    # The candidate set shown per agent: requested ∩ allowlist, OIDC stripped
    # (D11). Granted/lacking marking against each agent's live scopes happens
    # in _render_agent_options.
    candidates = [s for s in requested if s not in OIDC_PASSTHROUGH_SCOPES]
    if allowlist is not None:
        candidates = [s for s in candidates if s in allowlist]

    html = _AGENT_CONSENT_PAGE_TEMPLATE.format(
        app_name=html_mod.escape(app_name),
        app_description=html_mod.escape(app_description),
        user_email=html_mod.escape(user_email),
        redirect_origin=html_mod.escape(_redirect_origin(redirect_uri)),
        agent_options=_render_agent_options(agents, candidates),
        consent_token=html_mod.escape(consent_token),
        fonts_url=_FONTS_URL,
        check_svg=_CHECK_SVG,
    )
    return HTMLResponse(content=html, headers=_CONSENT_SECURITY_HEADERS)


@router.post("/oauth/consent", dependencies=[Depends(_check_rate_limit)])
async def consent_submit(
    request: Request,
    consent_token: str = Form(..., json_schema_extra=SENSITIVE),
    action: str = Form(...),
    agent_id: str | None = Form(default=None),
    ctx: Context = Depends(get_ctx),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
    grant_svc: OAuthGrantService = Depends(get_oauth_grant_service),
) -> RedirectResponse:
    """Process the consent form submission. Mints the auth code only on approval.

    ``consent_token`` is the opaque handle emitted by the callback. It never
    leaves the state backend as anything more than an ID — the actual consent
    parameters (user_id, email, scopes, redirect_uri) live server-side and
    can't be tampered with or captured from browser history/proxy logs.

    ``agent_id`` is posted only by the agent-picker variant
    (``consent_model='agent'`` clients); it is validated and the scope math
    recomputed entirely server-side — the browser's selection is never
    trusted.
    """
    params = await _load_consent_handle(consent_token, request)
    if params is None:
        logger.warning("oauth_consent_invalid_handle")
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    if not await _consume_consent_handle(consent_token, request):
        logger.warning("oauth_consent_handle_replay", handle=consent_token[:8])
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    redirect_uri = str(params.get("redirect_uri") or "")
    raw_state = params.get("original_state")
    original_state = str(raw_state) if raw_state else None
    client_id = str(params.get("client_id") or "")
    scope = str(params.get("scope") or "openid")

    oauth_client: OAuthClientView | None = None
    if not _is_platform_client(client_id, ctx):
        # Mid-flow D7 re-check (see oauth_callback): a client denied between
        # the consent page render and this submit must not provision a user
        # row or mint a code — even on the Deny arm we return the human error
        # rather than an OAuth redirect the denied client could observe.
        oauth_client = await _get_cached_oauth_client(request, client_id, ctx)
        if oauth_client is None or not _client_gate_passes(oauth_client):
            logger.warning(
                "oauth_client_gate_failed_midflow", client_id=client_id, stage="consent_submit"
            )
            return RedirectResponse(url="/error?error=access_denied", status_code=302)

    if action == "deny":
        # No user_id yet because provisioning is deferred to approve; audit
        # against the user's IdP email so the deny is still attributable.
        deny_email = str(params.get("user_email") or "")
        logger.info("oauth_consent_denied", client_id=client_id, email=deny_email)
        return _error_redirect(redirect_uri, "access_denied", original_state)

    claims_data = params.get("claims")
    if not isinstance(claims_data, dict):
        logger.warning("oauth_consent_missing_claims", client_id=client_id)
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    idp_claims = IdpClaims(
        external_subject=str(claims_data.get("external_subject") or ""),
        email=str(claims_data.get("email") or ""),
        email_verified=bool(claims_data.get("email_verified") or False),
        first_name=str(claims_data.get("first_name") or ""),
        last_name=str(claims_data.get("last_name") or ""),
    )
    try:
        user_id = await authorize_svc.provision_from_claims(idp_claims)
    except UserNotAdmittedError:
        logger.warning("oauth_user_not_admitted", client_id=client_id)
        return RedirectResponse(url="/error?error=access_denied", status_code=302)
    except InvalidGrantError:
        logger.warning("oauth_provision_failed", client_id=client_id, exc_info=True)
        return RedirectResponse(url="/error?error=server_error", status_code=302)

    code_challenge = str(params.get("code_challenge") or "")
    raw_nonce = params.get("nonce")
    nonce = str(raw_nonce) if raw_nonce else None

    if oauth_client is not None and oauth_client.consent_model == OAuthConsentModel.AGENT.value:
        # Consent→agent binding: validate the posted agent server-side
        # (exists + active + owned by the consenting user — the picker's
        # option list IS that predicate), recompute the D2 scope
        # intersection, mint the grant row (+ audit + oauth_grant.created),
        # and stamp grant_id on the code. Validation failures render the
        # human error page — never an OAuth redirect with a code.
        grant_result = await _approve_agent_consent(
            authorize_svc,
            grant_svc,
            oauth_client=oauth_client,
            user_id=user_id,
            client_id=client_id,
            agent_id=agent_id,
            scope=scope,
        )
        if isinstance(grant_result, RedirectResponse):
            return grant_result
        grant_id_value, effective_scopes = grant_result

        platform_code = await authorize_svc.issue_authorization_code(
            user_id=user_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scopes=" ".join(effective_scopes),
            nonce=nonce,
            grant_id=grant_id_value,
        )
    else:
        if not _is_platform_client(client_id, ctx):
            await authorize_svc.record_consent_decision(
                user_id=user_id,
                oauth_client_id=client_id,
                approved=True,
                scopes=scope,
            )

        platform_code = await authorize_svc.issue_authorization_code(
            user_id=user_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scopes=scope,
            nonce=nonce,
        )

    logger.info("oauth_consent_approved", client_id=client_id)
    redirect_params: dict[str, str] = {"code": platform_code}
    if original_state:
        redirect_params["state"] = original_state

    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        url=f"{redirect_uri}{separator}{urlencode(redirect_params)}", status_code=302
    )


async def _approve_agent_consent(
    authorize_svc: AuthorizeService,
    grant_svc: OAuthGrantService,
    *,
    oauth_client: OAuthClientView,
    user_id: str,
    client_id: str,
    agent_id: str | None,
    scope: str,
) -> RedirectResponse | tuple[str, list[str]]:
    """Validate the picked agent and mint the grant. Returns (grant_id, scopes)."""
    if not agent_id:
        logger.warning("oauth_consent_agent_missing", client_id=client_id)
        return RedirectResponse(url="/error?error=invalid_agent_selection", status_code=302)

    options = await authorize_svc.list_consentable_agents(user_id)
    selected = next((o for o in options if o.id == agent_id), None)
    if selected is None:
        # Not the user's own active agent — covers unknown ids, other users'
        # agents, and pending/disabled/archived agents in one predicate.
        logger.warning("oauth_consent_agent_invalid", client_id=client_id)
        return RedirectResponse(url="/error?error=invalid_agent_selection", status_code=302)

    requested = [s.strip() for s in scope.split() if s.strip()]
    allowlist = (
        frozenset(oauth_client.allowed_scopes) if oauth_client.allowed_scopes is not None else None
    )
    effective = _effective_agent_scopes(requested, allowlist, selected.scopes)
    if not effective:
        logger.warning("oauth_consent_no_grantable_scopes", client_id=client_id, agent_id=agent_id)
        return RedirectResponse(url="/error?error=no_grantable_scopes", status_code=302)

    try:
        grant_id_value = await grant_svc.create_grant(
            user_id=user_id,
            oauth_client_id=client_id,
            agent_id=selected.id,
            scopes=effective,
            client_name=oauth_client.name,
        )
    except ConsentAgentNotEligibleError:
        # The mint-time lock + re-check refused: the agent was transferred,
        # archived, or disabled between the picker validation above and the
        # grant write. Same user-facing posture as a failed picker
        # validation — the human error page, never a code redirect or a 500.
        logger.warning(
            "oauth_consent_agent_invalid_at_mint", client_id=client_id, agent_id=agent_id
        )
        return RedirectResponse(url="/error?error=invalid_agent_selection", status_code=302)
    return grant_id_value, effective


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
