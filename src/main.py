"""
Jentic Mini — main.py
"""

import html as _html
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.openapi.docs import get_redoc_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.auth import APIKeyMiddleware, is_proxy_trusted_peer
from src.brokers.pipedream import PipedreamOAuthBroker
from src.config import (
    APP_VERSION,
    JENTIC_ROOT_PATH,
    JENTIC_TRUSTED_PROXY_HEADER,
    JENTIC_TRUSTED_PROXY_NETS,
    normalise_root_path,
)
from src.db import run_migrations, setup_state
from src.negotiate import negotiate_middleware
from src.oauth_broker import registry as oauth_broker_registry
from src.routers import access_requests as access_requests_router
from src.routers import agents_admin as agents_admin_router
from src.routers import apis as apis_router
from src.routers import broker as broker_router
from src.routers import capability as capability_router
from src.routers import catalog as catalog_router
from src.routers import credentials as creds_router
from src.routers import debug as debug_router
from src.routers import default_key as default_key_router
from src.routers import import_ as import_router
from src.routers import jobs as jobs_router
from src.routers import notes as notes_router
from src.routers import oauth_agent as oauth_agent_router
from src.routers import oauth_brokers as oauth_brokers_router
from src.routers import overlays as overlays_router
from src.routers import search as search_router
from src.routers import toolkits as toolkits_router
from src.routers import traces as traces_router
from src.routers import user as user_router
from src.routers import workflows as workflows_router
from src.routers.apis import rebuild_index_on_startup
from src.routers.catalog import refresh_catalog_if_stale
from src.routers.toolkits import policy_router as toolkits_policy_router
from src.routers.workflows import backfill_workflow_involved_apis
from src.startup import backfill_credential_routes, seed_broker_apps, self_register
from src.utils import build_absolute_url, build_canonical_url, route_path


logging.basicConfig(level=(os.getenv("LOG_LEVEL") or "info").upper())
logging.getLogger("aiosqlite").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
log = logging.getLogger("jentic")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Jentic starting — running migrations")
    run_migrations()
    log.info("Jentic backfilling credential routes")
    await backfill_credential_routes()
    log.info("Jentic building BM25 index")
    await rebuild_index_on_startup()
    log.info("Jentic self-registering")
    await self_register(app)
    log.info("Jentic refreshing catalog manifest")
    await refresh_catalog_if_stale()
    log.info("Jentic backfilling workflow associations")
    await backfill_workflow_involved_apis()
    log.info("Jentic seeding broker app mappings")
    await seed_broker_apps()
    log.info("Jentic loading OAuth brokers")
    _pd_brokers = await PipedreamOAuthBroker.from_db()
    for _b in _pd_brokers:
        oauth_broker_registry.register(_b)
    log.info("Jentic loaded %d OAuth broker(s)", len(_pd_brokers))
    log.info("Jentic ready")
    yield
    log.info("Jentic shutting down")


class ForwardedPrefixMiddleware:
    """Resolve the active root_path on each ASGI scope.

    Sources, in precedence order:
    1. ``JENTIC_ROOT_PATH`` env var — already on ``scope["root_path"]`` from
       the FastAPI constructor.
    2. ``X-Forwarded-Prefix`` header — read per request when the env var is
       unset. When ``JENTIC_TRUSTED_PROXY_NETS`` is set the header is only
       accepted from peers inside the CIDR allowlist; requests from outside
       are ignored with a warning. When ``JENTIC_TRUSTED_PROXY_NETS`` is
       unset the header is accepted unconditionally (preserves behaviour for
       deployments that have not yet configured a trusted-proxy CIDR).
       Invalid values are silently ignored (treated as no mount).

    Path stripping is intentionally left to Starlette's routing machinery
    (``get_route_path``) so ``Mount`` / ``StaticFiles`` cooperation stays
    intact — pre-stripping here causes ``StaticFiles`` to look in the wrong
    directory because ``Mount.matches`` recomputes ``root_path`` assuming
    the prefix is still on ``scope["path"]``. Custom middleware that compare
    against unprefixed constants use :func:`src.utils.route_path` instead.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not scope.get("root_path"):
            for key, value in scope.get("headers", []):
                if key == b"x-forwarded-prefix":
                    if JENTIC_TRUSTED_PROXY_NETS:
                        peer_ip = (scope.get("client") or ("", 0))[0]
                        if not is_proxy_trusted_peer(peer_ip):
                            logging.getLogger("jentic.auth").warning(
                                "FORWARDED_PREFIX untrusted_peer=%s ignored", peer_ip
                            )
                            break
                    try:
                        scope["root_path"] = normalise_root_path(value.decode("latin-1"))
                    except RuntimeError:
                        # Hostile / malformed header → treat as no mount.
                        pass
                    break

        await self.app(scope, receive, send)


# Tag order controls Swagger UI section order.
# Within each section, operations appear in router-registration order.
_TAGS_METADATA = [
    {
        "name": "search",
        "description": "**Start here.** Full-text and semantic search across all registered APIs and operations.",
    },
    {
        "name": "inspect",
        "description": "Inspect capability details, list APIs and operations.",
    },
    {
        "name": "execute",
        "description": (
            "Transparent request broker — runs API operations and Arazzo workflows. "
            "Prefix any registered host to route through the broker: "
            "`POST /api.stripe.com/v1/payment_intents`. "
            "Credential injection, policy enforcement, and simulate mode built-in."
        ),
    },
    {
        "name": "observe",
        "description": "Read async job handles and execution traces.",
    },
    {
        "name": "toolkits",
        "description": "Manage toolkits: scoped credential bundles with access keys, permissions, and access requests.",
    },
    {
        "name": "credentials",
        "description": "Manage upstream API credentials in the vault (humans/admin only). Values are write-only — never returned after creation.",
    },
    {
        "name": "user",
        "description": "Human account management: create account, login, logout, and agent key generation.",
    },
    {
        "name": "catalog",
        "description": "Register APIs, upload specs, manage overlays and notes.",
    },
]

app = FastAPI(
    title="Jentic Mini",
    root_path=JENTIC_ROOT_PATH,
    openapi_tags=_TAGS_METADATA,
    description=(
        "**Jentic Mini** is the open-source, self-hosted implementation of the Jentic API — "
        "fully API-compatible with the [Jentic hosted and VPC editions](https://jentic.com).\n\n"
        "## What is Jentic Mini?\n"
        "Jentic Mini gives any agent a local execution layer: search a catalog of registered APIs, "
        "broker authenticated requests without exposing credentials to the agent, enforce access "
        "policies, and observe every execution. It is designed to be dropped in as a self-hosted "
        "alternative to the Jentic cloud service.\n\n"
        "## Hosted vs Self-hosted\n"
        "The **Jentic hosted and VPC editions** offer deeper implementations across three areas:\n\n"
        "| Capability | Jentic Mini (this) | Jentic hosted / VPC |\n"
        "|------------|-------------------|---------------------|\n"
        "| **Search** | BM25 full-text search | Advanced semantic search (~64% accuracy improvement over BM25) |\n"
        "| **Request brokering** | In-process credential injection | Scalable AWS Lambda-based broker with encryption at rest and in-transit, SOC 2-grade security, and 3rd-party credential vault integrations (HashiCorp Vault, AWS Secrets Manager, etc.) |\n"
        "| **Simulation** | Basic simulate mode | Full sandbox for simulating API calls and toolkit behaviour (enterprise-only) |\n"
        "| **Catalog** | Local registry only | Central catalog — aggregates the collective know-how of agents across API definitions and Arazzo workflows |\n\n"
        "## Authentication\n"
        "**Agents (OAuth identity)** — `Authorization: Bearer` with `at_…` access tokens "
        "(from `POST /oauth/token`) or `rat_…` registration tokens (received from `POST /register`, "
        "only usable for polling registration status). "
        "New agents should use the registration flow; see `GET /.well-known/oauth-authorization-server` for OAuth metadata.\n"
        "**Agents (toolkit key)** — `X-Jentic-API-Key: tk_xxx`. Legacy path; still supported but new agents "
        "should use OAuth registration via `POST /register`.\n"
        "**Humans** — [log in here](/login) for a session cookie (required for admin operations).\n\n"
        "## Tag groups\n"
        "| Tag | Who uses it | Purpose |\n"
        "|-----|-------------|----------|\n"
        "| **search** | Agents | Full-text search — the main entrypoint |\n"
        "| **inspect** | Agents | Inspect capabilities, list APIs and operations |\n"
        "| **execute** | Agents | Transparent request broker — runs API operations and Arazzo workflows. "
        "Credential injection, policy enforcement, and simulate mode built-in. |\n"
        "| **toolkits** | Agents/Humans | Toolkits, access keys, permissions, access requests |\n"
        "| **observe** | Agents | Read execution traces |\n"
        "| **catalog** | Humans/admin | Register APIs, upload specs, overlays, notes |\n"
        "| **credentials** | Humans only | Manage the credentials vault |\n\n"
        "Agents with a toolkit key need: **search**, **inspect**, **execute**, **toolkits** (read), **observe**."
    ),
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
    debug=False,
    servers=[
        {
            "url": "https://{hostname}:{port}",
            "description": "Self-hosted instance (HTTPS)",
            "variables": {
                "hostname": {"default": "localhost", "description": "Server hostname"},
                "port": {"default": "8900", "description": "Server port"},
            },
        },
        {
            "url": "http://{hostname}:{port}",
            "description": "Local development only (HTTP)",
            "variables": {
                "hostname": {"default": "localhost", "description": "Server hostname"},
                "port": {"default": "8900", "description": "Server port"},
            },
        },
    ],
    contact={
        "name": "Jentic Mini Support",
        "url": "https://github.com/jentic/jentic-mini",
        "email": "hello@jentic.com",
    },
    license_info={"name": "Apache 2.0", "identifier": "Apache-2.0"},
)

app.add_middleware(APIKeyMiddleware)
app.middleware("http")(negotiate_middleware)

# ── Static dir — defined early so route handlers can reference it ──────────────
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


# Matches any <base ...> tag regardless of the number of attributes.
# Using href="..." as the anchor would silently become a no-op if Vite ever
# adds a second attribute (e.g. crossorigin). The \s after 'base' prevents
# matching <basefont>. Substitutes only the first occurrence.
_BASE_TAG_RE = re.compile(rb"<base\s[^>]*>")


def _inject_base_href(html: bytes, root_path: str) -> bytes:
    """Substitute the SPA's <base href="..."> with one that includes root_path.

    When ``root_path`` is empty, the bytes are returned unchanged. Otherwise the
    first ``<base ...>`` tag is replaced with ``<base href="{root_path}/" />``
    (trailing slash intentional — browsers resolve relative URLs against
    ``<base href>`` differently with vs. without it). HTML without any
    ``<base>`` tag is returned unchanged.
    """
    if not root_path:
        return html
    safe = _html.escape(root_path, quote=True)
    return _BASE_TAG_RE.sub(f'<base href="{safe}/" />'.encode(), html, count=1)


def _render_index(request: Request) -> HTMLResponse:
    """Read SPA index.html, inject the per-request base href, return HTML."""
    body = _inject_base_href(
        (STATIC_DIR / "index.html").read_bytes(),
        request.scope.get("root_path", ""),
    )
    return HTMLResponse(body, media_type="text/html")


app.include_router(capability_router.router, tags=["inspect"])
app.include_router(workflows_router.router)
app.include_router(import_router.router, tags=["catalog"])
app.include_router(catalog_router.router, tags=["catalog"])
app.include_router(jobs_router.router)
app.include_router(traces_router.router, tags=["observe"])
app.include_router(
    overlays_router.router, tags=["catalog"]
)  # must be before apis (path converter conflict)
app.include_router(apis_router.router, tags=["catalog"])
app.include_router(search_router.router, tags=["search"])
app.include_router(creds_router.router, tags=["credentials"])
app.include_router(creds_router.audit_router)
app.include_router(toolkits_router.router, tags=["toolkits"])
app.include_router(toolkits_policy_router, prefix="/toolkits", tags=["toolkits"])
app.include_router(access_requests_router.router, prefix="/toolkits", tags=["toolkits"])
app.include_router(notes_router.router, tags=["catalog"])
app.include_router(debug_router.router, include_in_schema=False)
app.include_router(user_router.router)
app.include_router(default_key_router.router)
app.include_router(oauth_agent_router.router)
app.include_router(agents_admin_router.router)
app.include_router(oauth_brokers_router.router, tags=["credentials"])


# ── Meta routes: health + root — MUST be before broker catch-all ─────────────


class HealthSetupRequired(BaseModel):
    """Health response when no admin account exists yet.

    Carries the URLs an agent or human needs to bootstrap: the OAuth metadata
    document for agent DCR, the canonical token / registration endpoints, and
    the human-facing setup_url for admin-account creation.
    """

    status: Literal["setup_required"] = Field(
        description="Bootstrap state — no admin account exists",
    )
    account_created: Literal[False]
    message: str
    next_step: str
    setup_url: str = Field(description="Human-facing URL to create the admin account")
    oauth_authorization_server_metadata: str = Field(
        description="Discovery document URL for agent DCR (RFC 8414)",
    )
    registration_endpoint: str
    token_endpoint: str
    version: str


class HealthOk(BaseModel):
    """Health response when the instance is fully set up."""

    status: Literal["ok"] = Field(description="Instance is operational")
    version: str
    apis_registered: int = Field(ge=0)


HealthOut = HealthSetupRequired | HealthOk


@app.get(
    "/health",
    tags=["meta"],
    response_model=HealthOut,
    responses={
        200: {
            "description": "Setup state. Schema varies by status — discriminate on the `status` field."
        }
    },
)
async def health(request: Request) -> HealthOut:
    """Returns current setup state with explicit instructions for agents and UI.

    Response varies based on setup progress:
    - status='setup_required': No admin account yet → OAuth metadata URLs for agent DCR; human setup_url
    - status='ok': Admin account exists → includes version and apis_registered count

    This endpoint is always public (no auth required) so agents can check setup state before
    attempting authenticated calls. UI uses this to determine whether to show setup wizard.

    Returns:
        Setup status, version, and context-specific next steps or operational metrics.
    """
    state = await setup_state()
    # Pin the agent-identity discovery URLs to the canonical base so a spoofed
    # Host:/X-Forwarded-Host: cannot point clients at a non-canonical token
    # endpoint. The setup_url remains request-derived because it is a UX hint
    # for the same caller, not a security claim.
    issuer = build_canonical_url(request, "").rstrip("/")

    # Trusted-proxy mode: skip local account setup — users are provisioned JIT by the proxy.
    proxy_active = bool(JENTIC_TRUSTED_PROXY_HEADER and JENTIC_TRUSTED_PROXY_NETS)
    if not state["account_created"] and not proxy_active:
        return {
            "status": "setup_required",
            "account_created": False,
            "message": "Create an admin account to finish setup. Agents use OAuth registration in parallel.",
            "next_step": (
                "Agents: GET /.well-known/oauth-authorization-server, then POST /register with client_name and jwks. "
                "Humans: open setup_url to create the admin account, then approve agents and grant toolkits."
            ),
            "setup_url": build_absolute_url(request, "/user/create"),
            "oauth_authorization_server_metadata": f"{issuer}/.well-known/oauth-authorization-server",
            "registration_endpoint": f"{issuer}/register",
            "token_endpoint": f"{issuer}/oauth/token",
            "version": APP_VERSION,
        }

    # Fully set up
    async with __import__("aiosqlite").connect(
        __import__("src.db", fromlist=["DB_PATH"]).DB_PATH
    ) as db:
        async with db.execute("SELECT COUNT(*) FROM apis") as cur:
            (api_count,) = await cur.fetchone()

    return {
        "status": "ok",
        "version": APP_VERSION,
        "apis_registered": api_count,
    }


# ── Version / update-check ─────────────────────────────────────────────────
_version_cache: dict = {"ts": 0.0, "latest": None, "release_url": None}
_VERSION_CACHE_TTL = 6 * 3600  # check GitHub at most once every 6 hours


@app.get("/version", tags=["meta"])
async def get_version():
    """Returns current version and latest GitHub release (cached 6 h).
    Set JENTIC_TELEMETRY=off to disable the outbound GitHub check.
    """
    if os.getenv("JENTIC_TELEMETRY", "").lower() == "off":
        return {"current": APP_VERSION, "latest": None, "release_url": None}

    now = time.time()
    if now - _version_cache["ts"] > _VERSION_CACHE_TTL:
        # Mark attempt immediately so concurrent requests don't pile up
        _version_cache["ts"] = now
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    "https://api.github.com/repos/jentic/jentic-mini/releases/latest",
                    headers={"Accept": "application/vnd.github+json"},
                    timeout=5.0,
                )
                if r.status_code == 200:
                    data = r.json()
                    tag = data.get("tag_name") or ""
                    _version_cache["latest"] = tag.lstrip("v") or None
                    _version_cache["release_url"] = data.get("html_url")
                # 404 = no releases yet; 403/429 = rate limited — stay silent
        except Exception:
            pass  # network error — return what we have

    return {
        "current": APP_VERSION,
        "latest": _version_cache["latest"],
        "release_url": _version_cache["release_url"],
    }


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.png", include_in_schema=False)
async def favicon():
    path = STATIC_DIR / "favicon.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Favicon not found")
    return FileResponse(path, media_type="image/png")


@app.get("/llms.txt", tags=["meta"], include_in_schema=False)
async def llms_txt():
    """Machine-readable summary for LLMs (https://llmstxt.org/)."""
    path = Path(__file__).resolve().parent.parent / "llms.txt"
    if path.exists():
        return FileResponse(path, media_type="text/plain; charset=utf-8")
    return Response(content="# Jentic Mini\n", media_type="text/plain; charset=utf-8")


@app.get("/", tags=["meta"], include_in_schema=False)
async def root(request: Request):
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return _render_index(request)
    return {"message": "Jentic API is running. See /docs for API documentation."}


# ── Docs served locally (no CDN, works offline / on patchy connections) ───────
@app.get("/docs", include_in_schema=False)
async def swagger_ui(request: Request):
    rp = request.scope.get("root_path", "")
    # Defense-in-depth: HTML-escape rp for attribute contexts, JSON-encode for
    # the inline JS string. The validator already restricts root_path to
    # [A-Za-z0-9._~-] segments so these calls are no-ops today; they decouple
    # each sink's safety from the validator should the character set widen.
    rp_attr = _html.escape(rp, quote=True)
    rp_js_url = json.dumps(f"{rp}/openapi.json")
    # Custom Swagger UI with persistAuthorization + auth banner. Every absolute
    # path the browser resolves (CSS / JS asset URLs, the login link, the
    # OpenAPI URL) is prefixed with the active root_path so the page works
    # under any mount.
    html = f"""<!DOCTYPE html>
<html>
<head>
  <title>Jentic — Swagger UI</title>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" type="text/css" href="{rp_attr}/static/swagger-ui.css" >
  <style>
    .auth-banner {{
      background: #1a1a2e; border-left: 4px solid #667eea;
      color: #e0e0e0; padding: 12px 20px; font-family: monospace;
      font-size: 14px; margin: 0;
    }}
    .auth-banner strong {{ color: #667eea; }}
    .auth-banner code {{ background: #2d2d2d; padding: 2px 6px; border-radius: 3px; }}
  </style>
</head>
<body>
<div class="auth-banner">
  🔑 <strong>Authentication.</strong>
  <strong>Agents (toolkit):</strong> <em>JenticApiKey</em> — your <code>tk_xxx</code> in <code>X-Jentic-API-Key</code>.
  <strong>Agents (OAuth):</strong> <em>AgentOauthAccessToken</em> — <code>Authorization: Bearer</code> with <code>at_…</code>; <em>AgentOauthRegistrationToken</em> — <code>Authorization: Bearer</code> with <code>rat_…</code> where applicable.
  <strong>Humans:</strong> <em>HumanLogin</em> (username + password) — or <a href="{rp_attr}/login" style="color:#a5b4fc">log in here</a> for a browser session.
  OAuth agents: <code>GET /.well-known/oauth-authorization-server</code> then <code>POST /register</code>. Toolkit keys are issued from the UI.
</div>
<div id="swagger-ui"></div>
<script src="{rp_attr}/static/swagger-ui-bundle.js"> </script>
<script>
  window.onload = function() {{
    SwaggerUIBundle({{
      url: {rp_js_url},
      dom_id: '#swagger-ui',
      presets: [ SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset ],
      layout: "BaseLayout",
      persistAuthorization: true,
      tryItOutEnabled: true,
      requestInterceptor: function(req) {{ return req; }},
    }})
  }}
</script>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/redoc", include_in_schema=False)
async def redoc(request: Request):
    rp = _html.escape(request.scope.get("root_path", ""), quote=True)
    return get_redoc_html(
        openapi_url=f"{rp}/openapi.json",
        title="Jentic — Redoc",
        redoc_js_url=f"{rp}/static/redoc.standalone.js",
    )


# ── Broker catch-all — MUST be registered last ────────────────────────────────
# Paths whose first segment contains "." route to the broker.
# All Jentic-internal routes above take priority by registration order.
# ── Static files — MUST be before broker catch-all ────────────────────────────
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    # Also serve Vite build assets at /assets (Vite default output path)
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

# ── SPA middleware — serve index.html for browser navigations to SPA routes ───
# API clients (Accept: application/json) get the API response.
# Browsers (Accept: text/html) get the React SPA.
_SPA_PATHS = {
    "/approve",
    "/search",
    "/catalog",
    "/workflows",
    "/toolkits",
    "/credentials",
    "/traces",
    "/jobs",
    "/oauth-brokers",
    "/setup",
    "/login",
    "/agents",
}


@app.middleware("http")
async def spa_middleware(request: Request, call_next):
    if request.method == "GET":
        path = route_path(request.scope)
        accept = request.headers.get("accept", "")
        wants_html = any(part.strip().startswith("text/html") for part in accept.split(","))
        # Exclude connect-callback from SPA interception (real GET endpoint, browser redirect)
        is_spa_excluded = bool(re.match(r"^/oauth-brokers/[^/]+/connect-callback", path))
        if (
            wants_html
            and not is_spa_excluded
            and any(path == p or path.startswith(p + "/") for p in _SPA_PATHS)
        ):
            index_path = STATIC_DIR / "index.html"
            if index_path.exists():
                resp = _render_index(request)
                resp.headers["Vary"] = "Accept"
                resp.headers["Cache-Control"] = "no-store"
                return resp
            return Response(content="UI not built", status_code=404, media_type="text/plain")
    return await call_next(request)


# ── Reverse-proxy prefix middleware — registered last so it ends up Starlette's
#    outermost middleware. Resolves scope["root_path"] from JENTIC_ROOT_PATH or
#    X-Forwarded-Prefix and strips it from scope["path"] before any downstream
#    middleware reads request.url.path.
app.add_middleware(ForwardedPrefixMiddleware)


# ── Broker catch-all — MUST be registered last ────────────────────────────────
app.include_router(broker_router.router)


# ── Custom OpenAPI schema with API key security scheme ────────────────────────

# Paths that are open (no tk_xxx key required) — shown as unlocked in Swagger UI.
# Broker paths (/{host}/...) are open passthrough but handled dynamically by the
# broker router and don't appear as static paths in the schema.
_OPEN_OPERATIONS: set[tuple[str, str]] = {
    # path, method
    ("/health", "get"),
    ("/user/create", "post"),
    ("/user/login", "post"),
    ("/user/token", "post"),
    ("/.well-known/oauth-authorization-server", "get"),
    ("/register", "post"),
    ("/oauth/token", "post"),
    # Search + inspect: public read-only discovery
    ("/search", "get"),
    ("/apis", "get"),
    ("/apis/{api_id}", "get"),
    ("/apis/{api_id}/overlays", "get"),
    ("/apis/{api_id}/overlays/{overlay_id}", "get"),
    # Workflow execution is open passthrough (upstream auth is upstream's problem)
    ("/workflows", "get"),
    ("/workflows/{slug}", "get"),
    ("/workflows/{slug}", "post"),
}

# Human-only operations - require human session, reject agent keys
_HUMAN_ONLY_OPERATIONS: set[tuple[str, str]] = {
    # Default toolkit key (legacy, human-only rotation)
    ("/default-api-key/generate", "post"),
    # Credentials write
    ("/credentials", "post"),
    ("/credentials/{cid}", "patch"),
    ("/credentials/{cid}", "delete"),
    # Toolkit write
    ("/toolkits", "post"),
    ("/toolkits/{toolkit_id}", "patch"),
    ("/toolkits/{toolkit_id}", "delete"),
    ("/toolkits/{toolkit_id}/keys", "post"),
    ("/toolkits/{toolkit_id}/keys/{key_id}", "patch"),
    ("/toolkits/{toolkit_id}/keys/{key_id}", "delete"),
    ("/toolkits/{toolkit_id}/credentials", "post"),
    ("/toolkits/{toolkit_id}/credentials/{credential_id}", "delete"),
    ("/toolkits/{toolkit_id}/credentials/{cred_id}/permissions", "put"),
    ("/toolkits/{toolkit_id}/credentials/{cred_id}/permissions", "patch"),
    # Access request approvals
    ("/toolkits/{toolkit_id}/access-requests/{req_id}/approve", "post"),
    ("/toolkits/{toolkit_id}/access-requests/{req_id}/deny", "post"),
    # Catalog admin
    ("/apis", "post"),
    ("/apis/{api_id}", "delete"),
    ("/apis/{api_id}/overlays", "post"),
    ("/apis/{api_id}/overlays/{overlay_id}", "delete"),
    # OAuth broker admin
    ("/oauth-brokers", "post"),
    ("/oauth-brokers/{broker_id}", "patch"),
    ("/oauth-brokers/{broker_id}", "delete"),
    ("/oauth-brokers/{broker_id}/accounts/{account_id}/reconnect-link", "post"),
    ("/oauth-brokers/{broker_id}/accounts/{account_id}", "patch"),
    # User management
    ("/user/logout", "post"),
    ("/agents", "get"),
    ("/agents/{agent_id}", "get"),
    ("/agents/{agent_id}/approve", "post"),
    ("/agents/{agent_id}/deny", "post"),
    ("/agents/{agent_id}/disable", "post"),
    ("/agents/{agent_id}/enable", "post"),
    ("/agents/{agent_id}/jwks", "put"),
    ("/agents/{agent_id}", "delete"),
    ("/agents/{agent_id}/grants", "post"),
    ("/agents/{agent_id}/grants", "get"),
    ("/agents/{agent_id}/grants/{toolkit_id}", "delete"),
}


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,  # controls section order in Swagger UI
    )

    # Add servers. When JENTIC_ROOT_PATH is set, prepend a prefix-relative entry
    # so Swagger UI "Try it out" and code-generators use the correct base path.
    # Note: the schema is cached after the first call (app.openapi_schema), so
    # this entry reflects the *static* JENTIC_ROOT_PATH only. Mode C deployments
    # (X-Forwarded-Prefix per request, JENTIC_ROOT_PATH unset) will always see
    # app.servers here — Swagger UI "Try it out" may not work under a Mode C
    # prefix mount. Set JENTIC_ROOT_PATH for full Swagger UI compatibility.
    schema["servers"] = (
        [{"url": JENTIC_ROOT_PATH, "description": "This instance (path-prefix mount)"}]
        + app.servers
        if JENTIC_ROOT_PATH
        else app.servers
    )

    # Add contact and license
    schema["info"]["contact"] = app.contact
    schema["info"]["license"] = app.license_info

    schema.setdefault("components", {})

    # Agent OAuth tokens (at_… and rat_…) use RFC 7523 JWT Bearer assertion and RFC 7591
    # Dynamic Client Registration flows. OpenAPI doesn't natively support these OAuth grant
    # types, so we document them as generic HTTP bearer schemes with descriptive text.
    schema["components"]["securitySchemes"] = {
        "JenticApiKey": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Jentic-API-Key",
            "description": "Toolkit API key (`tk_xxx`) issued for a toolkit (legacy agent path).",
        },
        "AgentOauthAccessToken": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "opaque",
            "description": (
                "Agent access token (`at_…`) from `POST /oauth/token`. "
                "Used for all agent-authenticated operations."
            ),
        },
        "AgentOauthRegistrationToken": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "opaque",
            "description": (
                "Short-lived registration token (`rat_…`) from `POST /register`. "
                "Only valid for `GET /register/{client_id}` to poll approval status."
            ),
        },
        "HumanLogin": {
            "type": "oauth2",
            "description": "Human admin session. Fill in username + password to get a Bearer JWT.",
            "flows": {
                "password": {
                    "tokenUrl": "/user/token",
                    "scopes": {},
                }
            },
        },
    }
    # Global default: toolkit key OR agent access token OR human session
    schema["security"] = [
        {"JenticApiKey": []},
        {"AgentOauthAccessToken": []},
        {"HumanLogin": []},
    ]

    # Set explicit security on all operations (required for SEC dimension scoring)
    # Tiers:
    # 1. Public (no auth): security: []
    # 2. Human-only: security: [{"HumanLogin": []}]
    # 3. Agent-accessible: toolkit key, agent access token, or human session
    # 4. Registration read: also accepts AgentOauthRegistrationToken
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if not isinstance(operation, dict):
                continue
            if (path, method.lower()) in _OPEN_OPERATIONS:
                # Public operations: no auth required
                operation["security"] = []
            elif (path, method.lower()) in _HUMAN_ONLY_OPERATIONS:
                # Human-only operations: require human session, reject agent keys
                operation["security"] = [{"HumanLogin": []}]
            elif path == "/register/{client_id}" and method.lower() == "get":
                # Registration read: rat_, at_, or human session
                operation["security"] = [
                    {"AgentOauthRegistrationToken": []},
                    {"AgentOauthAccessToken": []},
                    {"HumanLogin": []},
                ]
            elif path == "/oauth/revoke" and method.lower() == "post":
                # Token revocation (RFC 7009): at_ or human session — toolkit keys
                # cannot revoke OAuth tokens (the client that holds the token revokes it).
                operation["security"] = [
                    {"AgentOauthAccessToken": []},
                    {"HumanLogin": []},
                ]
            else:
                # Agent-accessible: toolkit key, agent access token, or human session
                operation["security"] = [
                    {"JenticApiKey": []},
                    {"AgentOauthAccessToken": []},
                    {"HumanLogin": []},
                ]

    # Reorder paths: group by root resource prefix, then depth (least → most specific),
    # then alphabetically within the same depth. This produces the natural logical order:
    #   /apis → /apis/{id} → /apis/{id}/openapi.json → /apis/{id}/operations → …
    # Routing requires specific-suffix routes registered before catch-alls, but docs
    # should read from least specific to most specific.
    def _path_sort_key(p: str) -> tuple:
        parts = [s for s in p.split("/") if s]
        root = parts[0] if parts else ""
        depth = len(parts)
        return (root, depth, p)

    schema["paths"] = dict(
        sorted(schema.get("paths", {}).items(), key=lambda kv: _path_sort_key(kv[0]))
    )

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi
