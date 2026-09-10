"""AuthCode+PKCE authorization endpoints with consent screen support.

Flow overview:
  GET /authorize        — validate client + redirect_uri, redirect to IdP
                          (a pending-approval client renders the
                          approval-pending page instead — see below); with a
                          valid session continuation (``sc``, #1299) skip the
                          identity rungs and rejoin at consent directly
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

Inline agent creation (P4): a consenting user who owns zero active agents used
to dead-end on a terminal empty-state page (the G12(b) first-run dead-end —
deferred provisioning means a first-time user reaching consent owns nothing,
and the MCP client just times out). The zero-agents arm now renders a
create-agent form instead: POST /oauth/consent/agent verifies a signed,
single-use ``agent-create``-purpose blob bound to the consent handle AND the
authenticated subject, re-validates the handle and the zero-agents predicate
(an agent appearing in between skips creation; a per-subject ``set_if_absent``
marker makes parallel submits create exactly one), and creates the agent as
the consenting user through the same ``AgentService.create`` path the SPA uses
(default agent scopes, same audit + event).

The creation posture is a hybrid mirroring the platform's two existing doors
(security review on P4 — the SPA's POST /agents gates on ``agents:write``,
and this public mid-flow endpoint must not out-privilege it):

- the consenting user's effective permissions include ``agents:write`` (same
  implication math as the web gate; org:admin implies it) → the agent is
  created ACTIVE and a 303 re-enters GET /oauth/consent with it pre-selected;
- otherwise → the agent is created PENDING (the ``POST /register`` posture:
  no scope grants until approval, the same requires-action event in the
  admins' queue) and an awaiting-approval page renders — it polls
  GET /oauth/consent/agent/status (anonymous, keyed by a signed
  ``agent-status`` blob bound to the consent handle and the agent id, never a
  bare id) and auto-continues into consent the moment an admin approves; a
  deny renders a terminal message. Re-entering the flow while the agent is
  still pending re-renders the awaiting page (the consent handle outlives few
  approvals, so the flow must be re-enterable); a user whose agents were all
  disabled/archived/rejected keeps a terminal empty state.
"""

from __future__ import annotations

import hashlib
import html as html_mod
import json
import secrets
import time
from typing import Literal
from urllib.parse import urlencode, urlsplit

import httpx
import structlog
from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from jentic_one.admin.services.errors import UserNotFoundError
from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.core.idp import IdpClaims
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.authorize_service import AgentConsentOption, AuthorizeService
from jentic_one.auth.services.errors import (
    ConsentAgentNotEligibleError,
    InvalidGrantError,
    UserNotAdmittedError,
)
from jentic_one.auth.services.oauth_grant_service import OAuthGrantService
from jentic_one.auth.services.schemas.agents import AgentCreatePayload
from jentic_one.auth.web.deps import get_agent_service
from jentic_one.auth.web.flow import (
    CONSENT_SECURITY_HEADERS,
    CONSENT_STATE_MAX_AGE_SECONDS,
    FONTS_URL,
    SESSION_CONTINUATION_MAX_AGE_SECONDS,
    SPA_TOKEN_STORAGE_KEY,
    STATE_MAX_AGE_SECONDS,
    SessionContinuation,
    agent_create_signing_key,
    agent_status_signing_key,
    approval_state_key,
    check_approval_status_rate_limit,
    check_rate_limit,
    client_gate_passes,
    get_cached_oauth_client,
    get_consent_backend,
    is_platform_client,
    platform_client_allows_redirect,
    resolve_identity_gate,
    sign_payload,
    state_signing_key,
    verify_payload,
    write_idp_consent_handle,
    write_local_consent_handle,
)
from jentic_one.auth.web.schemas.authorize import (
    ConsentAgentStatusResponse,
    OAuthApprovalDecisionRequest,
    OAuthApprovalStatusResponse,
)
from jentic_one.auth.web.theme import AUTH_PAGE_CSS, LOGO_BLOCK_HTML
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
from jentic_one.shared.db import DatabaseIntegrityError
from jentic_one.shared.models import ActorStatus, ActorType
from jentic_one.shared.models.oauth_clients import OAuthClientApprovalStatus, OAuthConsentModel
from jentic_one.shared.scopes import OIDC_PASSTHROUGH_SCOPES
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.deps import derive_origin, get_ctx
from jentic_one.shared.web.sensitive import SENSITIVE

logger = structlog.get_logger(__name__)

router = APIRouter()


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
    if is_platform_client(client_id, ctx):
        return platform_client_allows_redirect(redirect_uri, client_id, ctx)
    client = await get_cached_oauth_client(request, client_id, ctx)
    if client is None or not client_gate_passes(client):
        return False
    return redirect_uri in client.redirect_uris


async def _get_client_allowed_scopes(
    request: Request, client_id: str, ctx: Context
) -> frozenset[str] | None:
    """Return allowed scopes for a registered client, or None for platform clients."""
    if is_platform_client(client_id, ctx):
        return None
    client = await get_cached_oauth_client(request, client_id, ctx)
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


#: TTL for the signed approval-state blob carried by the approval-pending page
#: (poll + inline-decision key). Deliberately the same window as the IdP-leg
#: ``state`` — the page self-heals past expiry by re-running /authorize, which
#: mints a fresh blob while the client stays pending.
APPROVAL_STATE_MAX_AGE_SECONDS = STATE_MAX_AGE_SECONDS

#: How often the approval-pending page polls /oauth/approval/status. 5 s keeps
#: a single tab at 12 rpm, well inside the endpoint's own rate bucket.
_APPROVAL_POLL_INTERVAL_MS = 5000

#: localStorage key the operator SPA keeps its bearer session under (shared
#: with the login page's session-continuation offer — the single source of
#: truth lives in flow.py, kept in lockstep with ``ui/src/shared/auth``).
_SPA_TOKEN_STORAGE_KEY = SPA_TOKEN_STORAGE_KEY

#: SPA route of the OAuth-client approval queue (Settings → queue tab), used
#: for the "ask your admin" deep link. Kept in lockstep with the UI's
#: ``oauthQueue`` link target (``ui/src/shared/lib/agentStream.tsx``) and the
#: ``/app`` SPA mount (``shared/web/static.py``).
_APPROVAL_QUEUE_SPA_PATH = "/app/settings?tab=queue"

# Static page structure only — every dynamic value is HTML-escaped before it
# is formatted in, and the visual theme ({page_css}/{logo_block}) is the
# static, drift-guarded constant pair from ``auth.web.theme``. The check-mark
# bullet lives in auth.css.
_CONSENT_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Authorize {app_name} | Jentic One</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="{fonts_url}" rel="stylesheet">
    <style>{page_css}</style>
</head>
<body>
    <div class="card">
        {logo_block}
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
            <form method="post" action="/oauth/consent" class="form-slot">
                <input type="hidden" name="consent_token" value="{consent_token}">
                <input type="hidden" name="action" value="deny">
                <button type="submit" class="deny">Deny</button>
            </form>
            <form method="post" action="/oauth/consent" class="form-slot">
                <input type="hidden" name="consent_token" value="{consent_token}">
                <input type="hidden" name="action" value="approve">
                <button type="submit" class="approve">Authorize</button>
            </form>
        </div>
        <div class="footer">
            Authorizing grants the application the permissions listed above.<br>
            <a href="{restart_url}">Not you? Use a different account</a>
        </div>
    </div>
</body>
</html>
"""


def _restart_authorize_url(params: dict[str, object]) -> str:
    """Rebuild the original /authorize URL from a consent handle (no ``sc``).

    The consent page's "Not you?" escape: re-runs the SAME authorize request
    without any session continuation, so the ladder lands on rung 2/3 (IdP or
    the login form) and the user authenticates as someone else. All values
    are handle-derived (server-side state), never request input.
    """
    query: dict[str, str] = {
        "response_type": "code",
        "client_id": str(params.get("client_id") or ""),
        "redirect_uri": str(params.get("redirect_uri") or ""),
        "code_challenge": str(params.get("code_challenge") or ""),
        "code_challenge_method": "S256",
        "scope": str(params.get("scope") or "openid"),
    }
    original_state = params.get("original_state")
    if original_state:
        query["state"] = str(original_state)
    nonce = params.get("nonce")
    if nonce:
        query["nonce"] = str(nonce)
    return f"/authorize?{urlencode(query)}"


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
        .panel .hint {{
            color: #689296;
            font-size: 12px;
        }}
        .client-details {{
            background: white;
            border: 1px solid #E4EAEB;
            border-radius: 6px;
            padding: 10px 12px;
            margin-bottom: 10px;
            font-size: 12px;
        }}
        .client-details dt {{
            color: #689296;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-size: 10px;
            margin-top: 8px;
        }}
        .client-details dt:first-child {{ margin-top: 0; }}
        .client-details dd {{
            color: #0E1A1D;
            font-weight: 600;
            word-break: break-all;
        }}
        .panel a {{
            color: #305256;
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
            <p>You are signed in as an administrator &mdash; review before
                deciding. The name above is self-chosen by the client; these
                details come from its registration:</p>
            <dl class="client-details">
                <dt>Redirect origins</dt>
                <dd>{client_origins}</dd>
                <dt>Client ID</dt>
                <dd>{client_id_text}</dd>
                {software_id_row}
            </dl>
            <p class="hint"><a href="{queue_url}">Open the approval queue</a>
                for the full registration record.</p>
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

    // I2 clamp: a 400 (expired/invalid blob) re-runs /authorize to mint a
    // fresh blob. A clock-skewed verifier could 400 fresh blobs too, so
    // re-mints are capped per tab; any healthy poll answer resets the count.
    var REMINT_KEY = "jentic-one.approval-remints";
    var MAX_REMINTS = 3;
    function remintOrStop() {
        var count = 0;
        try {
            count = parseInt(window.sessionStorage.getItem(REMINT_KEY), 10) || 0;
        } catch (e) { /* storage blocked */ }
        if (count >= MAX_REMINTS) {
            settled = true;
            statusEl.textContent =
                "This page could not verify its state. " +
                "Retry the connection from your application.";
            return;
        }
        try {
            window.sessionStorage.setItem(REMINT_KEY, String(count + 1));
        } catch (e) { /* storage blocked */ }
        navigate(cfg.resume_url);
    }
    function clearRemints() {
        try { window.sessionStorage.removeItem(REMINT_KEY); } catch (e) { /* blocked */ }
    }

    // Poll cadence: cfg.poll_ms steady-state. A 429 backs off to the
    // server's Retry-After hint (or doubles the cadence when absent),
    // clamped to [cfg.poll_ms, 60 s]; any non-429 answer resets it.
    var pollDelay = cfg.poll_ms;
    function schedule() {
        if (!settled) { window.setTimeout(poll, pollDelay); }
    }
    function poll() {
        if (settled) { return; }
        fetch(cfg.status_url, { headers: { Accept: "application/json" } })
            .then(function (resp) {
                if (resp.status === 400) {
                    remintOrStop();
                    return null;
                }
                if (resp.status === 429) {
                    var retryAfter = parseInt(resp.headers.get("Retry-After"), 10);
                    var hinted = isNaN(retryAfter) ? 0 : retryAfter * 1000;
                    pollDelay = Math.min(
                        Math.max(hinted, pollDelay * 2, cfg.poll_ms), 60000);
                    return null;
                }
                pollDelay = cfg.poll_ms;
                if (resp.ok) {
                    clearRemints();
                    return resp.json();
                }
                return null;
            })
            .then(function (body) {
                if (body && body.status) { onStatus(body.status); }
                schedule();
            })
            .catch(function () { schedule(); });
    }
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
    <style>{page_css}</style>
</head>
<body>
    <div class="card card--wide">
        {logo_block}
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
            limited to the permissions shown.<br>
            <a href="{restart_url}">Not you? Use a different account</a>
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

# Zero active agents AND (no provisionable subject on the handle OR the user
# owns only out-of-service agent rows) → a terminal empty-state page, HTTP
# 200, no code minted. (The genuine first-run zero-agents arm renders the
# inline create-agent form below — P4 — and a user whose only agents are
# PENDING gets the awaiting-approval page; this template survives for a
# malformed handle that names no subject, and for users whose agents were all
# disabled/archived/rejected — the create form must not sidestep that admin
# action. The two arms carry different copy — {headline_suffix} and
# {description} — because "you don't have one yet" is a lie for the second.)
_NO_AGENTS_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>No agents available | Jentic One</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="{fonts_url}" rel="stylesheet">
    <style>{page_css}</style>
</head>
<body>
    <div class="card">
        {logo_block}
        <h1><span class="app-name">{app_name}</span> connects through an agent
            &mdash; {headline_suffix}</h1>
        <p class="description">
            {description}
        </p>
    </div>
</body>
</html>
"""

_NO_AGENTS_FIRST_RUN_HEADLINE = "you don't have one yet"
_NO_AGENTS_FIRST_RUN_DESCRIPTION = (
    "This application acts through one of your approved agents.\n"
    "            Register an agent (see the Jentic One agent-registration docs),\n"
    "            have an administrator approve it, then retry the connection\n"
    "            from your application."
)
_NO_AGENTS_UNAVAILABLE_HEADLINE = "you have no available agents"
_NO_AGENTS_UNAVAILABLE_DESCRIPTION = (
    "This application acts through one of your approved agents, but\n"
    "            none of your agents are currently available. Contact your\n"
    "            administrator to restore one, then retry the connection\n"
    "            from your application."
)


# The zero-agents create-agent form (P4): rendered in place of the terminal
# empty state when the consent handle names a provisionable subject AND the
# resolved user owns no agent rows at all (any status — first run). Static
# page structure only — every dynamic value is HTML-escaped before it is
# formatted in; no page script, so no JSON seam is needed.
_CREATE_AGENT_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Create an agent | Jentic One</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="{fonts_url}" rel="stylesheet">
    <style>{page_css}</style>
</head>
<body>
    <div class="card">
        {logo_block}
        <div class="user-info">
            <div class="label">Signed in as</div>
            <div class="email">{user_email}</div>
        </div>
        <h1><span class="app-name">{app_name}</span> connects through an agent
            &mdash; create your first one to continue</h1>
        <div class="origin">{redirect_origin}</div>
        <p class="description">
            An agent is the identity this application will act as on
            Jentic One: it belongs to you and carries its own set of
            permissions, separate from your account. You don't have one
            yet &mdash; name it to continue.
        </p>
        {error_block}
        <form method="post" action="/oauth/consent/agent">
            <label class="field-label" for="agent_name">Agent name</label>
            <input type="text" id="agent_name" name="agent_name" value="{agent_name}"
                   maxlength="255" required autofocus autocomplete="off"
                   placeholder="e.g. my-assistant">
            <input type="hidden" name="consent_token" value="{consent_token}">
            <input type="hidden" name="create_state" value="{create_state}">
            <button type="submit" class="primary block">Create agent and continue</button>
        </form>
        <div class="footer">
            {approval_note}<br>
            You will still review and approve the connection on the
            next screen.
        </div>
    </div>
</body>
</html>
"""

#: Footer note on the create form — set per arm so the form never promises
#: the wrong outcome (security review on P4). The GET render detects the arm
#: for already-provisioned users; a not-yet-provisioned IdP subject gets the
#: neutral wording because the definitive check runs post-provisioning on the
#: POST.
_CREATE_NOTE_ACTIVE = (
    "The agent is created with the platform's default agent\n            permissions, owned by you."
)
_CREATE_NOTE_PENDING = (
    "Your agent will need administrator approval before you can\n"
    "            continue &mdash; the next page waits and continues\n"
    "            automatically once it is approved."
)
_CREATE_NOTE_NEUTRAL = (
    "The agent is owned by you and may need administrator approval\n"
    "            before you can continue."
)


# The pending-agent awaiting page (P4 hybrid): rendered when the inline
# creation lands in the PENDING posture (consenting user lacks agents:write)
# and when a flow re-entry finds the user's only agents still pending. Mirrors
# the P2 approval-pending page: static structure, one escaped JSON
# <script id="agent-approval-config"> seam, poll + auto-continue + terminal
# deny, and the same "ask your admin" copy-link pattern. No inline decision
# panel: the consenting user lacks agents:write by definition, and admins
# decide in the SPA agents queue.
_AGENT_AWAITING_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Awaiting agent approval | Jentic One</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="{fonts_url}" rel="stylesheet">
    <style>{page_css}</style>
</head>
<body>
    <div class="card">
        {logo_block}
        <div class="user-info">
            <div class="label">Signed in as</div>
            <div class="email">{user_email}</div>
        </div>
        <h1>Your agent <span class="app-name">{agent_name}</span> is awaiting
            administrator approval</h1>
        <p class="description">
            This page checks automatically and continues the connection to
            <span class="app-name">{app_name}</span> as soon as an
            administrator approves your agent.
        </p>
        <div class="panel">
            <p>Ask your Jentic One admin to approve
                <span class="app-name">{agent_name}</span> on the Agents page:</p>
            <div class="copy-row">
                <input id="agents-link" readonly value="{agents_url}">
                <button type="button" class="copy" id="copy-link">Copy</button>
            </div>
        </div>
        <div class="status-line" id="agent-approval-status">Waiting for approval&hellip;</div>
    </div>
    <script id="agent-approval-config" type="application/json">{config_json}</script>
    {page_script}
</body>
</html>
"""

# Inline behaviour for the pending-agent awaiting page. Kept as a plain string
# (not a .format template) so its braces need no doubling; every dynamic value
# comes from the JSON <script id="agent-approval-config"> block, the single
# escaped seam between server data and page script. Differences from the P2
# script (deliberate): no admin panel / decision leg, and a 400 from the poll
# is TERMINAL instead of re-minting — the status blob shares the consent
# handle's lifetime, so once it expires the continue leg is dead anyway and
# the honest move is "retry the connection" (a re-entry re-renders this page
# with a fresh handle + blob while the agent stays pending).
_AGENT_PENDING_SCRIPT = """<script>
(function () {
    "use strict";
    var cfg = JSON.parse(
        document.getElementById("agent-approval-config").textContent);
    var statusEl = document.getElementById("agent-approval-status");
    var settled = false;

    function onStatus(status) {
        if (settled) { return; }
        if (status === "approved") {
            settled = true;
            statusEl.textContent = "Approved — continuing…";
            // Re-enter the consent page: the handle re-validates server-side
            // and the newly-active agent renders pre-selected.
            window.location.replace(cfg.continue_url);
        } else if (status === "denied") {
            settled = true;
            statusEl.textContent =
                "An administrator denied this agent. " +
                "You can close this page.";
        }
    }

    // Poll cadence: cfg.poll_ms steady-state. A 429 backs off to the
    // server's Retry-After hint (or doubles the cadence when absent),
    // clamped to [cfg.poll_ms, 60 s]; any non-429 answer resets it.
    var pollDelay = cfg.poll_ms;
    function schedule() {
        if (!settled) { window.setTimeout(poll, pollDelay); }
    }
    function poll() {
        if (settled) { return; }
        fetch(cfg.status_url, { headers: { Accept: "application/json" } })
            .then(function (resp) {
                if (resp.status === 400) {
                    settled = true;
                    statusEl.textContent =
                        "This page has expired. Retry the connection " +
                        "from your application.";
                    return null;
                }
                if (resp.status === 429) {
                    var retryAfter = parseInt(resp.headers.get("Retry-After"), 10);
                    var hinted = isNaN(retryAfter) ? 0 : retryAfter * 1000;
                    pollDelay = Math.min(
                        Math.max(hinted, pollDelay * 2, cfg.poll_ms), 60000);
                    return null;
                }
                pollDelay = cfg.poll_ms;
                return resp.ok ? resp.json() : null;
            })
            .then(function (body) {
                if (body && body.status) { onStatus(body.status); }
                schedule();
            })
            .catch(function () { schedule(); });
    }
    poll();

    document.getElementById("copy-link").addEventListener("click", function () {
        var input = document.getElementById("agents-link");
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

#: SPA route of the agents list (the /register flow's approval queue —
#: PENDING agents render there with approve/deny). Kept in lockstep with the
#: UI's ``ROUTES.agents`` (``ui/src/shared/app/routes.ts``) and the ``/app``
#: SPA mount (``shared/web/static.py``).
_AGENTS_SPA_PATH = "/app/agents"


# ---------- approval-in-flow helpers (P2) ----------


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
    return sign_payload(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "scope": scope,
            "nonce": nonce,
            "original_state": state,
            "iat": str(int(time.time())),
        },
        approval_state_key(ctx),
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
    if client_gate_passes(client):
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

    # M1 phishing counter (mirrors the consent page's origin block): the
    # client's NAME is self-chosen at anonymous registration, so the admin
    # panel renders verifiable registration facts next to the decision
    # buttons — registered redirect-URI origins, client_id, and software_id
    # when present. All ROW-derived (never request input) and HTML-escaped.
    origins = sorted({_redirect_origin(uri) for uri in client.redirect_uris})
    client_origins = "<br>".join(html_mod.escape(origin) for origin in origins) or "&mdash;"
    software_id_row = ""
    if client.software_id:
        software_id_row = (
            f"<dt>Software ID</dt>\n                <dd>{html_mod.escape(client.software_id)}</dd>"
        )

    html = _AWAITING_APPROVAL_PAGE_TEMPLATE.format(
        app_name=html_mod.escape(client.name),
        client_origins=client_origins,
        client_id_text=html_mod.escape(client.client_id),
        software_id_row=software_id_row,
        queue_url=html_mod.escape(queue_url, quote=True),
        config_json=config_json,
        page_script=_APPROVAL_PENDING_SCRIPT,
        fonts_url=FONTS_URL,
    )
    return HTMLResponse(content=html, headers=CONSENT_SECURITY_HEADERS)


@router.get("/authorize", dependencies=[Depends(check_rate_limit)], response_model=None)
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
    sc: str | None = Query(
        default=None,
        description="Signed session-continuation blob minted by "
        "POST /oauth/session/continue (identity-ladder rung 1). Optional; an "
        "invalid or absent value leaves the flow byte-identical to before.",
    ),
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
    if not is_platform_client(client_id, ctx):
        unapproved = await get_cached_oauth_client(request, client_id, ctx)
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

    state_payload: dict[str, str | None] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "scope": scope,
        "nonce": nonce,
        "original_state": state,
        "iat": str(int(time.time())),
    }
    internal_state = sign_payload(dict(state_payload), state_signing_key(ctx), purpose="state")

    idp_url = authorize_svc.get_authorize_redirect_url(
        state=internal_state,
        nonce=nonce or secrets.token_urlsafe(16),
        redirect_uri=callback_uri,
    )

    # Identity dispatch: the explicit rung ladder lives in flow.py
    # (resolve_identity_gate) — platform-session continuation (#1299, the
    # ``sc`` blob minted by POST /oauth/session/continue) > IdP redirect >
    # local-login form (#1276). Rung 1 returns a verified SessionContinuation
    # and this endpoint owns the async redemption: single-use burn, then the
    # SAME rejoin arms as the local login submit (platform → direct code,
    # registered third-party → the shared consent handle). A failed
    # redemption (replay race) falls through to rungs 2/3 as if no
    # continuation had arrived.
    gate_result = resolve_identity_gate(
        ctx, idp_url=idp_url, state_payload=state_payload, session_state=sc
    )
    if isinstance(gate_result, SessionContinuation):
        redemption = await _redeem_session_continuation(
            request,
            ctx,
            gate_result,
            authorize_svc,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scope=scope,
            state=state,
            nonce=nonce,
        )
        if redemption is not None:
            return redemption
        gate_result = resolve_identity_gate(ctx, idp_url=idp_url, state_payload=state_payload)
    if gate_result is None or isinstance(gate_result, SessionContinuation):
        # The isinstance arm is unreachable (no session_state → no rung 1);
        # it only narrows the type for the checker.
        return _error_redirect(
            redirect_uri, "server_error", state, "no identity provider configured"
        )
    return gate_result


async def _redeem_session_continuation(
    request: Request,
    ctx: Context,
    continuation: SessionContinuation,
    authorize_svc: AuthorizeService,
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    scope: str,
    state: str | None,
    nonce: str | None,
) -> RedirectResponse | None:
    """Redeem a verified session continuation; ``None`` falls through to rungs 2/3.

    Single-use first: ``set_if_absent`` on a used-marker makes the first
    redemption win and every replay inside the TTL fall through to the normal
    login — a captured resume URL must not keep minting consent handles (or,
    on the platform arm, authorization codes). Then a LIVE user-row read
    (both arms): the blob carries only the opaque ``user_id`` (no PII in the
    GET query param — see F2 on the #1300 review), so the row read resolves
    the consent page's display email anyway, and re-checking ``active`` /
    ``must_change_password`` on it closes the ≤60 s window in which a user
    deactivated or password-fenced after the exchange could still redeem.
    The rejoin arms are the local login submit's, with the user pinned at
    exchange time instead of a just-checked password.
    """
    backend = get_consent_backend(request)
    digest = hashlib.sha256(continuation.token.encode()).hexdigest()
    if not await backend.set_if_absent(
        f"session-sc-used:{digest}", b"1", ttl_s=float(SESSION_CONTINUATION_MAX_AGE_SECONDS)
    ):
        logger.warning("oauth_session_continuation_replayed", client_id=client_id)
        return None

    try:
        user = await UserService(ctx).get_by_id(continuation.user_id)
    except UserNotFoundError:
        logger.warning("oauth_session_continuation_failed", reason="user_not_found")
        return None
    if not user.active or user.must_change_password:
        # Fail closed but fall through: the worst outcome of a fenced account
        # is the unchanged rung-3 login, where the fence is enforced anyway.
        logger.warning("oauth_session_continuation_failed", reason="user_fenced")
        return None

    if is_platform_client(client_id, ctx):
        # Platform client: consent-skip is a first-party trust decision, the
        # same terminal step as the local login submit's platform arm.
        platform_code = await authorize_svc.issue_authorization_code(
            user_id=continuation.user_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scopes=scope,
            nonce=nonce,
        )
        logger.info(
            "oauth_session_continuation_redeemed", client_id=client_id, consent="platform-skip"
        )
        redirect_params: dict[str, str] = {"code": platform_code}
        if state:
            redirect_params["state"] = state
        separator = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(
            url=f"{redirect_uri}{separator}{urlencode(redirect_params)}", status_code=302
        )

    # Registered third-party client: the entry gate above already re-checked
    # D7 for THIS request; belt and braces here because the redemption must
    # fail closed if the cached row somehow read differently.
    oauth_client = await get_cached_oauth_client(request, client_id, ctx)
    if oauth_client is None or not client_gate_passes(oauth_client):
        logger.warning(
            "oauth_client_gate_failed_midflow", client_id=client_id, stage="session_redeem"
        )
        return None

    # The SAME consent handle the IdP callback and the local login submit
    # write (one writer owns the shape): subject = the platform user pinned
    # at exchange time. The consent page renders that identity with its
    # "Not you?" escape, so an account mismatch is recoverable.
    consent_handle = await write_local_consent_handle(
        request,
        local_user_id=continuation.user_id,
        user_email=user.email,
        redirect_uri=redirect_uri,
        original_state=state,
        client_id=client_id,
        code_challenge=code_challenge,
        scope=scope,
        nonce=nonce,
        oauth_client=oauth_client,
    )
    logger.info("oauth_session_continuation_redeemed", client_id=client_id, consent="required")
    return RedirectResponse(url=f"/oauth/consent?ch={consent_handle}", status_code=302)


@router.get(
    "/oauth/approval/status",
    dependencies=[Depends(check_approval_status_rate_limit)],
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
    params = verify_payload(
        st,
        approval_state_key(ctx),
        purpose="approval",
        max_age=APPROVAL_STATE_MAX_AGE_SECONDS,
    )
    client = await get_cached_oauth_client(request, str(params.get("client_id") or ""), ctx)
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
    params = verify_payload(
        body.state,
        approval_state_key(ctx),
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
    dependencies=[Depends(check_rate_limit)],
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
        params = verify_payload(
            state,
            state_signing_key(ctx),
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

    oauth_client = await get_cached_oauth_client(request, client_id or "", ctx)
    if oauth_client is not None and not client_gate_passes(oauth_client):
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

        consent_handle = await write_idp_consent_handle(
            request,
            claims=claims,
            redirect_uri=original_redirect_uri,
            original_state=original_state,
            client_id=client_id,
            code_challenge=code_challenge,
            scope=scope,
            nonce=nonce,
            oauth_client=oauth_client,
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
    backend = get_consent_backend(request)
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
    backend = get_consent_backend(request)
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


# ---------- inline agent creation on the consent page (P4) ----------

#: Server-side ceiling for the inline agent name — matches the SPA creation
#: path's ``AgentCreateRequest`` (min_length=1, max_length=255) and the
#: ``agents.name`` column (String(255)).
_AGENT_NAME_MAX_LENGTH = 255

_AGENT_NAME_ERROR_MESSAGE = f"Enter an agent name (1\u2013{_AGENT_NAME_MAX_LENGTH} characters)."


def _consent_user_key(params: dict[str, object]) -> str | None:
    """A stable, non-PII identifier for the human the consent handle authenticated.

    The agent-create blob is bound to this value so a blob minted for one
    subject can never drive a create for another (a mid-window handle rewrite
    or a spliced form must fail closed). Local-login handles pin the
    provisioned ``user_id``; IdP handles pin the external subject (the user
    row may not exist yet — deferred provisioning). ``None`` means the handle
    names no provisionable subject at all, so no create form is offered.
    """
    local_user_id = params.get("local_user_id")
    if local_user_id:
        return f"local:{local_user_id}"
    claims_data = params.get("claims")
    if isinstance(claims_data, dict) and claims_data.get("external_subject"):
        return f"idp:{claims_data['external_subject']}"
    return None


def _mint_agent_create_state(ctx: Context, *, consent_token: str, user_key: str) -> str:
    """Sign the agent-create blob: bound to the consent handle AND the subject.

    The handle rides in as a digest (never the raw handle — the blob lives in
    page HTML and must not become a second copy of the capability), plus the
    subject key and ``iat``. Same signer/mechanism as the other flow blobs,
    fifth distinct purpose + derived key (mutual rejection with
    ``state``/``approval``/``login``/``session``).
    """
    return sign_payload(
        {
            "ch_digest": hashlib.sha256(consent_token.encode()).hexdigest(),
            "user_key": user_key,
            # Per-mint entropy: without it two mints inside the same second
            # are byte-identical (same payload → same HMAC), so the fresh
            # blob a validation re-render embeds would already be burned.
            "n": secrets.token_urlsafe(8),
            "iat": str(int(time.time())),
        },
        agent_create_signing_key(ctx),
        purpose="agent-create",
    )


async def _consume_agent_create_state(create_state: str, request: Request) -> bool:
    """Atomically spend the agent-create blob; ``False`` on replay.

    ``set_if_absent`` on a used-marker makes the first submit win and every
    replay inside the TTL lose — a captured form must not keep creating
    agents. A validation re-render mints a FRESH blob, so the burn here never
    strands a user mid-retry.
    """
    backend = get_consent_backend(request)
    digest = hashlib.sha256(create_state.encode()).hexdigest()
    return await backend.set_if_absent(
        f"agent-create-used:{digest}", b"1", ttl_s=float(CONSENT_STATE_MAX_AGE_SECONDS)
    )


async def _claim_agent_create_slot(user_key: str, request: Request) -> bool:
    """Atomically claim the one inline-create slot per subject; ``False`` = lost.

    Closes the parallel-submit race the per-blob burn cannot (security
    review): N GET renders mint N distinct blobs, and N concurrent submits
    each burn their OWN blob and pass the plain-read ``owner_has_any_agents``
    check before any insert commits — N agents. Same ``set_if_absent`` burn
    pattern, keyed by the SUBJECT instead of the blob, so exactly one submit
    per consenting user ever reaches ``AgentService.create``; losers re-enter
    consent, which by then renders the picker or the awaiting page.
    """
    backend = get_consent_backend(request)
    return await backend.set_if_absent(
        f"agent-create-done:{user_key}", b"1", ttl_s=float(CONSENT_STATE_MAX_AGE_SECONDS)
    )


def _mint_agent_status_state(ctx: Context, *, consent_token: str, agent_id: str) -> str:
    """Sign the pending-agent status blob: bound to the consent handle AND the agent.

    Minted only after the flow itself verified the agent belongs to the
    handle's subject (at creation, or at the pending re-entry render), so the
    signed ``agent_id`` is the ONLY agent this blob can ever poll — a blob
    can never be pointed at someone else's agent, and the poll response is a
    bare tri-state, so possession reveals nothing an attacker could not
    already see on the page carrying it. Repeatable by design (the page polls
    for minutes), unlike the single-use ``agent-create`` form blob — hence
    the sixth distinct purpose + derived key (mutual rejection with
    ``state``/``approval``/``login``/``session``/``agent-create``).
    """
    return sign_payload(
        {
            "ch_digest": hashlib.sha256(consent_token.encode()).hexdigest(),
            "agent_id": agent_id,
            "iat": str(int(time.time())),
        },
        agent_status_signing_key(ctx),
        purpose="agent-status",
    )


def _agent_tri_state(status: str | None) -> Literal["pending", "approved", "denied"]:
    """Collapse an agent row's lifecycle status into the poll tri-state.

    ``approved`` only for ACTIVE (the picker's own predicate, so the page
    never auto-continues into a consent that would not list the agent);
    ``denied`` only for the terminal REJECTED. Everything else — PENDING,
    but also DISABLED/ARCHIVED and a missing row — reads as ``pending``: the
    endpoint must not become a lifecycle or deletion oracle.
    """
    if status == ActorStatus.ACTIVE.value:
        return "approved"
    if status == ActorStatus.REJECTED.value:
        return "denied"
    return "pending"


def _render_no_agents_page(app_name: str, *, unavailable: bool = False) -> HTMLResponse:
    """The terminal zero-agents empty state (see template).

    ``unavailable=True`` is the out-of-service arm — the user DOES own agents
    but every one of them sits disabled/archived/rejected, so "you don't have
    one yet" would be a lie; the copy points at the administrator instead.
    """
    html = _NO_AGENTS_PAGE_TEMPLATE.format(
        app_name=html_mod.escape(app_name),
        headline_suffix=(
            _NO_AGENTS_UNAVAILABLE_HEADLINE if unavailable else _NO_AGENTS_FIRST_RUN_HEADLINE
        ),
        description=(
            _NO_AGENTS_UNAVAILABLE_DESCRIPTION if unavailable else _NO_AGENTS_FIRST_RUN_DESCRIPTION
        ),
        fonts_url=FONTS_URL,
        page_css=AUTH_PAGE_CSS,
        logo_block=LOGO_BLOCK_HTML,
    )
    return HTMLResponse(content=html, headers=CONSENT_SECURITY_HEADERS)


def _render_agent_create_page(
    params: dict[str, object],
    *,
    consent_token: str,
    create_state: str,
    approval_note: str,
    agent_name: str = "",
    error: str | None = None,
) -> HTMLResponse:
    """Render the inline create-agent form (P4) with the consent-page posture.

    ``approval_note`` is one of the static ``_CREATE_NOTE_*`` constants — the
    per-arm footer wording (never request-derived), so the form never promises
    an immediately-usable agent to a user whose creation will land PENDING.
    """
    app_name = str(params.get("client_name") or "Unknown Application")
    user_email = str(params.get("user_email") or "unknown")
    redirect_uri = str(params.get("redirect_uri") or "")
    error_block = f'<div class="error" role="alert">{html_mod.escape(error)}</div>' if error else ""
    html = _CREATE_AGENT_PAGE_TEMPLATE.format(
        app_name=html_mod.escape(app_name),
        user_email=html_mod.escape(user_email),
        redirect_origin=html_mod.escape(_redirect_origin(redirect_uri)),
        error_block=error_block,
        approval_note=approval_note,
        agent_name=html_mod.escape(agent_name, quote=True),
        consent_token=html_mod.escape(consent_token, quote=True),
        create_state=html_mod.escape(create_state, quote=True),
        fonts_url=FONTS_URL,
        page_css=AUTH_PAGE_CSS,
        logo_block=LOGO_BLOCK_HTML,
    )
    return HTMLResponse(content=html, headers=CONSENT_SECURITY_HEADERS)


def _render_agent_awaiting_page(
    params: dict[str, object],
    request: Request,
    ctx: Context,
    *,
    consent_token: str,
    agent_id: str,
    agent_name: str,
) -> HTMLResponse:
    """Render the pending-agent awaiting-approval page (P4 hybrid).

    Mirrors the P2 approval-pending page: every dynamic value reaching the
    page script rides in the one escaped JSON block, and the agent's
    user-chosen name only ever lands in HTML-escaped text nodes. The continue
    leg is the fixed same-origin consent path for THIS handle — re-entering
    it re-validates handle liveness and (on the approve submit) the D7 client
    gate server-side.
    """
    app_name = str(params.get("client_name") or "Unknown Application")
    user_email = str(params.get("user_email") or "unknown")
    status_state = _mint_agent_status_state(ctx, consent_token=consent_token, agent_id=agent_id)

    base_url = ctx.config.auth.canonical_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    agents_url = f"{base_url}{_AGENTS_SPA_PATH}"

    page_config = {
        "status_url": f"/oauth/consent/agent/status?{urlencode({'st': status_state})}",
        "continue_url": f"/oauth/consent?{urlencode({'ch': consent_token})}",
        "poll_ms": _APPROVAL_POLL_INTERVAL_MS,
    }
    # \u003c-escape (P2 discipline) so no config value can ever close the
    # JSON <script> block, even though every one here is server-minted.
    config_json = json.dumps(page_config).replace("<", "\\u003c")

    html = _AGENT_AWAITING_PAGE_TEMPLATE.format(
        app_name=html_mod.escape(app_name),
        agent_name=html_mod.escape(agent_name),
        user_email=html_mod.escape(user_email),
        agents_url=html_mod.escape(agents_url, quote=True),
        config_json=config_json,
        page_script=_AGENT_PENDING_SCRIPT,
        fonts_url=FONTS_URL,
        page_css=AUTH_PAGE_CSS,
        logo_block=LOGO_BLOCK_HTML,
    )
    return HTMLResponse(content=html, headers=CONSENT_SECURITY_HEADERS)


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


@router.get("/oauth/consent", response_class=HTMLResponse, dependencies=[Depends(check_rate_limit)])
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
            headers=CONSENT_SECURITY_HEADERS,
        )

    app_name = str(params.get("client_name") or "Unknown Application")
    app_description = str(params.get("client_description") or "This application")
    user_email = str(params.get("user_email") or "unknown")
    scope = str(params.get("scope") or "openid")

    client_id = str(params.get("client_id") or "")
    oauth_client: OAuthClientView | None = None
    if client_id and not is_platform_client(client_id, ctx):
        oauth_client = await get_cached_oauth_client(request, client_id, ctx)
    if oauth_client is not None and oauth_client.consent_model == OAuthConsentModel.AGENT.value:
        return await _render_agent_consent_page(
            params,
            request,
            oauth_client=oauth_client,
            consent_token=ch,
            authorize_svc=authorize_svc,
            ctx=ctx,
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
        restart_url=html_mod.escape(_restart_authorize_url(params), quote=True),
        fonts_url=FONTS_URL,
        page_css=AUTH_PAGE_CSS,
        logo_block=LOGO_BLOCK_HTML,
    )
    return HTMLResponse(content=html, headers=CONSENT_SECURITY_HEADERS)


async def _render_agent_consent_page(
    params: dict[str, object],
    request: Request,
    *,
    oauth_client: OAuthClientView,
    consent_token: str,
    authorize_svc: AuthorizeService,
    ctx: Context,
) -> HTMLResponse:
    """The agent-picker consent variant for ``consent_model='agent'`` clients.

    Lists only the consenting user's own ``status='active'`` agents; the
    zero-active-agents arm fans out (P4 hybrid):

    - handle names no provisionable subject (malformed) → the terminal empty
      state, first-run copy;
    - the user's newest agent sits in PENDING → the awaiting-approval page
      (poll + auto-continue), so a flow re-entry mid-approval is not a dead
      end;
    - the user owns only disabled/archived/rejected agents → the terminal
      empty state with the out-of-service copy (the create form must not
      sidestep the admin action that took them out of service);
    - a genuinely first-run user (zero agent rows, ever) → the inline
      create-agent form, its footer worded per the ``agents:write`` arm the
      submit will take (neutral when the user is not provisioned yet — the
      definitive check runs post-provisioning on the POST).

    The user identity is resolved read-only: rendering consent must not
    create a user row (the Deny contract); provisioning happens only on the
    create submit, an affirmative user action.
    """
    app_name = str(params.get("client_name") or "Unknown Application")
    app_description = str(params.get("client_description") or "This application")
    user_email = str(params.get("user_email") or "unknown")
    scope = str(params.get("scope") or "")
    redirect_uri = str(params.get("redirect_uri") or "")

    claims = _claims_from_params(params)
    raw_local_user_id = params.get("local_user_id")
    if raw_local_user_id:
        # Local-login rejoin (#1276): the user is already resolved — the
        # handle carries the authenticated user id, no claims to re-resolve.
        user_id: str | None = str(raw_local_user_id)
    else:
        user_id = await authorize_svc.resolve_existing_user_id(claims) if claims else None
    agents = await authorize_svc.list_consentable_agents(user_id) if user_id else []
    if not agents:
        # The resolved-user arms come FIRST: the awaiting page and the
        # out-of-service empty state depend only on the user's agent rows,
        # not on whether a create blob could be minted for this handle (a
        # re-entry with, say, an unverified email must still park a pending
        # agent's owner on the awaiting page rather than lie "first run").
        if user_id is not None:
            pending = await authorize_svc.newest_pending_agent(user_id)
            if pending is not None:
                # Mid-approval re-entry (or the inline create's pending arm
                # revisited): park on the awaiting page for the newest
                # pending agent instead of a dead end — the poll blob is
                # bound to this handle + this agent, both server-resolved.
                return _render_agent_awaiting_page(
                    params,
                    request,
                    ctx,
                    consent_token=consent_token,
                    agent_id=pending.id,
                    agent_name=pending.name,
                )
            if await authorize_svc.owner_has_any_agents(user_id):
                # Zero ACTIVE agents, none pending, but non-active rows exist
                # (disabled, archived, rejected): not a first-run user.
                # Offering the create form here would let the owner mint a
                # fresh ACTIVE agent and sidestep the admin action that took
                # the others out of service — keep the terminal empty state
                # (fail closed), with copy that owns up to it.
                return _render_no_agents_page(app_name, unavailable=True)
        user_key = _consent_user_key(params)
        if user_key is None:
            # No provisionable subject on the handle (no local user, no
            # verified IdP claims): nothing to create for — the pre-P4
            # terminal empty state.
            return _render_no_agents_page(app_name)
        if user_id is not None:
            # Existing user, zero agent rows ever: the form footer tells the
            # truth about the arm the submit will take (mirror of the POST's
            # post-provisioning check — same effective-permission math).
            approval_note = (
                _CREATE_NOTE_ACTIVE
                if await authorize_svc.user_can_create_active_agent(user_id)
                else _CREATE_NOTE_PENDING
            )
        else:
            # Deferred provisioning: the user row does not exist yet, so the
            # arm is undecidable here — neutral wording, decided on the POST.
            approval_note = _CREATE_NOTE_NEUTRAL
        create_state = _mint_agent_create_state(ctx, consent_token=consent_token, user_key=user_key)
        return _render_agent_create_page(
            params,
            consent_token=consent_token,
            create_state=create_state,
            approval_note=approval_note,
        )

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
        restart_url=html_mod.escape(_restart_authorize_url(params), quote=True),
        fonts_url=FONTS_URL,
        page_css=AUTH_PAGE_CSS,
        logo_block=LOGO_BLOCK_HTML,
    )
    return HTMLResponse(content=html, headers=CONSENT_SECURITY_HEADERS)


@router.post("/oauth/consent", dependencies=[Depends(check_rate_limit)])
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
    if not is_platform_client(client_id, ctx):
        # Mid-flow D7 re-check (see oauth_callback): a client denied between
        # the consent page render and this submit must not provision a user
        # row or mint a code — even on the Deny arm we return the human error
        # rather than an OAuth redirect the denied client could observe.
        oauth_client = await get_cached_oauth_client(request, client_id, ctx)
        if oauth_client is None or not client_gate_passes(oauth_client):
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
    local_user_id = params.get("local_user_id")
    if local_user_id:
        # Local-login rejoin (#1276): the user authenticated against the
        # first-party account store, so the row already exists — there is
        # nothing to provision and the Deny-leaves-no-residue contract holds
        # trivially (login never creates rows).
        user_id = str(local_user_id)
    elif not isinstance(claims_data, dict):
        logger.warning("oauth_consent_missing_claims", client_id=client_id)
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)
    else:
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
        if not is_platform_client(client_id, ctx):
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


@router.post(
    "/oauth/consent/agent",
    operation_id="consentAgentCreate",
    summary="Create the consenting user's first agent inline (consent page)",
    response_model=None,
    dependencies=[Depends(check_rate_limit)],
)
async def consent_agent_create(
    request: Request,
    consent_token: str = Form(..., json_schema_extra=SENSITIVE),
    create_state: str = Form(..., json_schema_extra=SENSITIVE),
    agent_name: str = Form(...),
    ctx: Context = Depends(get_ctx),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
    agent_svc: AgentService = Depends(get_agent_service),
) -> HTMLResponse | RedirectResponse:
    """Create the consenting user's first agent from the zero-agents consent page (P4).

    The form is rendered only when the consenting user owns zero agents in
    any status (the G12(b) first-run dead-end). This submit verifies the signed
    single-use ``agent-create`` blob (bound to the consent handle AND the
    authenticated subject — no ambient credential is honored, so a cross-site
    form cannot drive it: it would need both the unguessable handle and a
    blob minted for that very handle), re-validates the handle and the D7
    client gate, provisions the user row if deferred provisioning left none
    (an affirmative user action, unlike rendering), re-checks the zero-agents
    predicate (an agent appearing in between skips creation — idempotent),
    creates the agent through the same ``AgentService.create`` path as the
    SPA (owner = the consenting user, default agent scopes, same audit +
    event), and 303-redirects back into ``GET /oauth/consent`` where the new
    agent renders pre-selected.

    Failure arms: expired/tampered/replayed blob and expired handle → the
    consent flow's standard ``invalid_consent`` error redirect; a gated
    client → ``access_denied``; an invalid agent name → the form re-rendered
    with the error inline and a fresh blob.

    Creation posture (security review — the hybrid): the arm is decided
    server-side AFTER the subject is resolved/provisioned, against the same
    effective-permission math as POST /agents' ``agents:write`` gate. A
    permissioned user gets the original behaviour (ACTIVE + 303 re-entry); an
    unpermissioned one gets a PENDING agent (the POST /register posture) and
    the awaiting-approval page. A per-subject ``set_if_absent`` slot claim
    makes N parallel submits (N distinct blobs from N renders) create exactly
    one agent — losers re-enter consent, which renders the picker or the
    awaiting page as appropriate.
    """
    try:
        blob = verify_payload(
            create_state,
            agent_create_signing_key(ctx),
            purpose="agent-create",
            max_age=CONSENT_STATE_MAX_AGE_SECONDS,
        )
    except InvalidGrantError:
        logger.warning("oauth_consent_agent_create_invalid_state")
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    if not await _consume_agent_create_state(create_state, request):
        logger.warning("oauth_consent_agent_create_replayed")
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    params = await _load_consent_handle(consent_token, request)
    if params is None:
        # Consent handle expired or unknown — same arm as the consent submit.
        logger.warning("oauth_consent_invalid_handle", stage="agent_create")
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    user_key = _consent_user_key(params)
    if (
        blob.get("ch_digest") != hashlib.sha256(consent_token.encode()).hexdigest()
        or user_key is None
        or blob.get("user_key") != user_key
    ):
        # The blob was minted for a different handle or a different subject —
        # a spliced form fails closed with the standard error.
        logger.warning("oauth_consent_agent_create_binding_mismatch")
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    client_id = str(params.get("client_id") or "")
    oauth_client: OAuthClientView | None = None
    if not is_platform_client(client_id, ctx):
        # Mid-flow D7 re-check (see consent_submit): a client denied between
        # the form render and this submit must not cause resource creation.
        oauth_client = await get_cached_oauth_client(request, client_id, ctx)
        if oauth_client is None or not client_gate_passes(oauth_client):
            logger.warning(
                "oauth_client_gate_failed_midflow", client_id=client_id, stage="agent_create"
            )
            return RedirectResponse(url="/error?error=access_denied", status_code=302)
    if oauth_client is None or oauth_client.consent_model != OAuthConsentModel.AGENT.value:
        # Only the agent-picker consent variant ever renders the create form;
        # platform and consent_model='user' clients have no zero-agents arm.
        logger.warning("oauth_consent_agent_create_wrong_consent_model", client_id=client_id)
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    # HTTP-level name validation BEFORE any side effect (no user row is
    # provisioned for a submit that only re-renders the form). Business
    # validation beyond the length ceiling stays in the service layer.
    name = agent_name.strip()
    if not name or len(name) > _AGENT_NAME_MAX_LENGTH:
        fresh = _mint_agent_create_state(ctx, consent_token=consent_token, user_key=user_key)
        return _render_agent_create_page(
            params,
            consent_token=consent_token,
            create_state=fresh,
            approval_note=await _approval_note_for_params(params, authorize_svc),
            agent_name=name,
            error=_AGENT_NAME_ERROR_MESSAGE,
        )

    claims = _claims_from_params(params)
    raw_local_user_id = params.get("local_user_id")
    if raw_local_user_id:
        user_id = str(raw_local_user_id)
    else:
        resolved = await authorize_svc.resolve_existing_user_id(claims) if claims else None
        if resolved is None:
            if claims is None:
                # _consent_user_key above guarantees claims exist on this arm;
                # belt and braces for a malformed handle.
                logger.warning("oauth_consent_missing_claims", client_id=client_id)
                return RedirectResponse(url="/error?error=invalid_consent", status_code=302)
            # Deferred provisioning: creating an agent is an affirmative user
            # action, so the user row is provisioned here (same admission
            # policy + audit as the consent approve arm — never at render).
            try:
                resolved = await authorize_svc.provision_from_claims(claims)
            except UserNotAdmittedError:
                logger.warning("oauth_user_not_admitted", client_id=client_id)
                return RedirectResponse(url="/error?error=access_denied", status_code=302)
            except InvalidGrantError:
                logger.warning("oauth_provision_failed", client_id=client_id, exc_info=True)
                return RedirectResponse(url="/error?error=server_error", status_code=302)
        user_id = resolved

    # Idempotency/race re-check: if an agent appeared between the form render
    # and this submit (another tab, an admin, a parallel submit), create
    # nothing — just re-enter consent. The predicate is ownership of ANY
    # agent row, in any status, matching the render arm: a user whose agents
    # were disabled or archived by an admin is not first-run, and this
    # submit must not mint them a fresh ACTIVE agent past that action.
    if not await authorize_svc.owner_has_any_agents(user_id):
        # The plain read above cannot see a parallel submit that has not
        # committed yet — the atomic per-subject slot claim can (security
        # review): exactly one of N concurrent submits proceeds to create.
        if not await _claim_agent_create_slot(user_key, request):
            logger.info(
                "oauth_consent_agent_create_lost_race", client_id=client_id, user_id=user_id
            )
            return RedirectResponse(
                url=f"/oauth/consent?{urlencode({'ch': consent_token})}", status_code=303
            )
        # The hybrid arm split (security review): decided HERE, after the
        # subject is provisioned, with the same effective-permission math as
        # POST /agents' agents:write gate — this public mid-flow door must
        # not out-privilege the SPA's.
        can_create_active = await authorize_svc.user_can_create_active_agent(user_id)
        create_status = ActorStatus.ACTIVE if can_create_active else ActorStatus.PENDING
        # Owner is ALWAYS the consenting user resolved from the server-side
        # handle — the form carries no owner input. Scopes=None applies the
        # platform's DEFAULT_AGENT_SCOPES on the ACTIVE arm, exactly like the
        # SPA path (the PENDING arm defers scopes to approve(), exactly like
        # /register); the service records the same REGISTER audit +
        # agent.created event either way.
        identity = Identity(
            sub=user_id,
            email=str(params.get("user_email") or ""),
            actor_type=ActorType.USER,
            origin=derive_origin(request.headers.get("user-agent")),
        )
        try:
            view = await agent_svc.create(
                AgentCreatePayload(name=name, description=None, scopes=None),
                owner_id=user_id,
                identity=identity,
                status=create_status,
            )
        except DatabaseIntegrityError:
            # The owner FK refused (user row vanished mid-flow) — the shared
            # browser-facing error, never a raw 500.
            logger.warning("oauth_consent_agent_create_failed", client_id=client_id)
            return RedirectResponse(url="/error?error=server_error", status_code=302)
        logger.info(
            "oauth_consent_agent_created",
            client_id=client_id,
            agent_id=view.id,
            user_id=user_id,
            status=create_status.value,
        )
        if not can_create_active:
            # PENDING arm: park on the awaiting page — it polls the status
            # endpoint (blob bound to this handle + this agent) and
            # auto-continues into consent on approval.
            return _render_agent_awaiting_page(
                params,
                request,
                ctx,
                consent_token=consent_token,
                agent_id=view.id,
                agent_name=view.name,
            )
    else:
        logger.info(
            "oauth_consent_agent_create_skipped_existing", client_id=client_id, user_id=user_id
        )

    # 303 (POST → GET) back into the consent page: with exactly one agent the
    # picker pre-checks it, so the user approves in the same breath (a race
    # loser or a skipped-existing submit lands on whatever the render arm
    # says — picker, awaiting page, or empty state). The handle was just
    # re-validated against the state backend, so this stays a fixed
    # same-origin path — never request-derived beyond the handle id.
    return RedirectResponse(
        url=f"/oauth/consent?{urlencode({'ch': consent_token})}", status_code=303
    )


async def _approval_note_for_params(
    params: dict[str, object], authorize_svc: AuthorizeService
) -> str:
    """The create-form footer note for a validation re-render (read-only).

    Mirrors the GET render's arm detection: local/already-resolved users get
    the truthful ACTIVE/PENDING wording, a not-yet-provisioned IdP subject
    the neutral one. Resolution is read-only — a re-render must not create a
    user row.
    """
    raw_local_user_id = params.get("local_user_id")
    if raw_local_user_id:
        user_id: str | None = str(raw_local_user_id)
    else:
        claims = _claims_from_params(params)
        user_id = await authorize_svc.resolve_existing_user_id(claims) if claims else None
    if user_id is None:
        return _CREATE_NOTE_NEUTRAL
    if await authorize_svc.user_can_create_active_agent(user_id):
        return _CREATE_NOTE_ACTIVE
    return _CREATE_NOTE_PENDING


@router.get(
    "/oauth/consent/agent/status",
    operation_id="consentAgentStatus",
    summary="Poll pending-agent approval status (consent awaiting page)",
    dependencies=[Depends(check_approval_status_rate_limit)],
    responses={
        400: {"description": "Malformed, tampered, or expired agent-status blob."},
    },
)
async def consent_agent_status_endpoint(
    response: Response,
    st: str = Query(
        ..., description="Signed agent-status blob minted by the consent flow's pending arm"
    ),
    ctx: Context = Depends(get_ctx),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
) -> ConsentAgentStatusResponse:
    """Minimal tri-state poll for the pending-agent awaiting page (P4 hybrid).

    Anonymous but keyed by the signed ``agent-status`` blob — never a bare
    agent id, so the endpoint cannot be used to enumerate or probe agents:
    the id it reports on is the one SIGNED into the blob, which only the
    consent flow mints, and only for an agent it verified belongs to the
    handle's subject. The response carries ONLY the tri-state — no name,
    owner, or scopes — and non-terminal lifecycle states (disabled, archived,
    a vanished row) all read as ``pending``, so possession of a blob is not a
    lifecycle oracle either. Any verification failure (bad signature, wrong
    purpose, expired ``iat``, malformed blob) is a 400 ``invalid_grant``; the
    page treats a 400 as terminal ("retry the connection") because the blob
    shares the consent handle's lifetime — a retry re-enters the flow, which
    re-parks on a fresh awaiting page while the agent stays pending. Shares
    the approval-status poll's own per-IP rate bucket (same cadence, same
    caller shape).
    """
    params = verify_payload(
        st,
        agent_status_signing_key(ctx),
        purpose="agent-status",
        max_age=CONSENT_STATE_MAX_AGE_SECONDS,
    )
    status = await authorize_svc.get_agent_status(str(params.get("agent_id") or ""))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return ConsentAgentStatusResponse(status=_agent_tri_state(status))


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
