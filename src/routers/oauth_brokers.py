"""
/oauth-brokers — manage OAuth broker configurations.

OAuth brokers handle delegated OAuth credential management for APIs where
Jentic doesn't yet have production OAuth app approvals. The broker either
returns a raw token (if the provider exposes it) or proxies requests through
their infrastructure with OAuth injected server-side.

Current broker types:
  pipedream — Pipedream Connect (3,000+ APIs, managed OAuth via proxy)

Future:
  jentic    — Jentic's own OAuth service (once app approvals are in place)
"""

import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

import src.vault as vault
from src.auth import build_human_only_error, require_human_session
from src.brokers.pipedream import PipedreamOAuthBroker, api_host_to_pd_slug, broker_credential_id
from src.db import get_db
from src.oauth_broker import registry as oauth_broker_registry
from src.openapi_helpers import agent_hints
from src.utils import build_absolute_url, build_canonical_url
from src.validators import NormModel, validate_relative_redirect


log = logging.getLogger("jentic.routers.oauth_brokers")
audit_log = logging.getLogger("jentic.audit")

router = APIRouter(prefix="/oauth-brokers", tags=["credentials"])


async def revoke_pipedream_account_upstream(broker_id: str, account_id: str) -> bool:
    """Revoke a Pipedream-connected account upstream (best-effort).

    Used by both `DELETE /oauth-brokers/{id}/accounts/{aid}` and the
    `DELETE /credentials/{cid}` Pipedream cascade so a single function owns
    the upstream revoke contract. Returns True on success, False on any
    failure (we never block local cleanup on an upstream miss).
    """
    live_broker = next(
        (b for b in oauth_broker_registry.brokers if getattr(b, "broker_id", None) == broker_id),
        None,
    )
    if live_broker is None:
        try:
            brokers = await PipedreamOAuthBroker.from_db()
        except Exception:
            log.warning(
                "revoke_pipedream_account_upstream: failed to load brokers from DB", exc_info=True
            )
            return False
        live_broker = next((b for b in brokers if b.broker_id == broker_id), None)

    if live_broker is None:
        return False

    try:
        pd_token = await live_broker._get_access_token()
        pd_url = (
            f"https://api.pipedream.com/v1/connect/{live_broker.project_id}/accounts/{account_id}"
        )
        req = urllib.request.Request(pd_url, method="DELETE")
        req.add_header("Authorization", f"Bearer {pd_token}")
        req.add_header("X-PD-Environment", live_broker.environment)
        try:
            with urllib.request.urlopen(req, timeout=10):
                pass
            log.info("Pipedream account %s revoked via API", account_id)
            return True
        except urllib.error.HTTPError as http_err:
            body = http_err.read().decode("utf-8", errors="replace")
            log.warning(
                "Failed to revoke Pipedream account %s: HTTP %s %s — body: %s",
                account_id,
                http_err.code,
                http_err.reason,
                body,
            )
            return False
    except Exception as exc:
        log.warning("Failed to revoke Pipedream account %s: %s", account_id, exc)
        return False


# ── Request / Response models ─────────────────────────────────────────────────

_PIPEDREAM_CONFIG_EXAMPLE = {
    "client_id": "oa_abc123",
    "client_secret": "pd_secret_xxxx",
    "project_id": "proj_abc123",
    "support_email": "support@example.com",
}

_SUPPORTED_TYPES = ("pipedream",)

# Annotated path/query helpers with pre-filled Swagger examples
BrokerIdPath = Annotated[str, Path(description="The broker ID", examples=["pipedream"])]
ExternalUserIdQuery = Annotated[
    str | None, Query(description="Filter by external user ID", examples=["default"])
]

_CREATE_EXAMPLE = {
    "type": "pipedream",
    "config": _PIPEDREAM_CONFIG_EXAMPLE,
}

_CREATE_DESCRIPTION = """\
Register a delegated OAuth broker. Currently supported type: `pipedream`.

---

### Pipedream — one-time setup

Before registering, complete these steps in the Pipedream UI:

**1.** Go to [pipedream.com](https://pipedream.com) and sign in or create an account.

**2.** Go to **Settings** (main menu) → **API** → click **+ New OAuth Client**.
Name it "Jentic". Store the **client ID** and **client secret** safely — the secret is not shown again.

**3.** Go to **Projects** (main menu) and click **+ New Project**. Name it "Jentic".

**4.** Go to **Projects → Jentic → Settings** and note the **project ID** (format: `proj_xxx`).

That's it. Register the broker below — Jentic automatically configures the Connect
application name, support email, and logo in Pipedream on your behalf, so you don't
need to touch the Connect → Configuration screen manually.

---

### Registration

```json
{
  "type": "pipedream",
  "config": {
    "client_id": "oa_abc123",
    "client_secret": "pd_secret_xxxx",
    "project_id": "proj_abc123",
    "support_email": "support@example.com"
  }
}
```

`support_email` is optional but recommended — it is displayed to end users in the
Pipedream OAuth consent UI.

`client_secret` is write-only — Fernet-encrypted at rest, never returned.

---

### After registration

Once registered, connect individual apps with `POST /oauth-brokers/{id}/connect-link`
(pass `app` as the Pipedream app slug, e.g. `gmail`, `google_calendar`, `slack`).
After the user completes OAuth, call `POST /oauth-brokers/{id}/sync` to pull the
connected account into Jentic. From that point, requests to that API's host are
automatically proxied with the user's OAuth token injected server-side.
"""


class OAuthBrokerCreate(NormModel):
    """Register a new OAuth broker for delegated credential management via Pipedream or other providers."""

    id: str | None = Field(
        None, description="Optional custom broker ID. Auto-generated from type if omitted."
    )
    type: str = Field(..., description="Broker backend type. Currently supported: `pipedream`.")
    config: dict[str, Any] = Field(
        ...,
        description=(
            "Provider-specific configuration. "
            "For `pipedream`: `client_id`, `client_secret`, `project_id` "
            "(all from Pipedream workspace → API settings → OAuth clients). "
            "Optional: `environment` (`production` or `development`, default `production`), "
            "`support_email`, "
            '`default_external_user_id` (user identity for initial account sync, default `"default"`).'
        ),
        examples=[_PIPEDREAM_CONFIG_EXAMPLE],
    )

    model_config = {"json_schema_extra": {"example": _CREATE_EXAMPLE}}


class OAuthBrokerOut(BaseModel):
    """OAuth broker configuration with discovered accounts count. Config excludes sensitive fields."""

    id: str = Field(
        examples=["broker_abc123xyz"], description="Broker ID (format: broker_{12chars})"
    )
    type: str = Field(
        examples=["pipedream"], description="Broker type: 'pipedream' or other provider"
    )
    config: dict[str, Any] = Field(
        description="Public broker configuration (excludes encrypted secret fields)"
    )
    created_at: float = Field(
        examples=[1609459200], description="Unix timestamp when broker was registered"
    )
    accounts_discovered: int = Field(
        default=0, examples=[3], description="Number of OAuth accounts discovered from this broker"
    )


class SyncRequest(NormModel):
    """Request body for syncing discovered OAuth accounts from a broker into Jentic credentials."""

    external_user_id: str = Field(
        "default",
        description=(
            "The user identity to sync accounts for. In a single-user setup this is "
            "always `default`. In multi-user deployments, pass the Jentic user ID "
            "that was used when the user completed OAuth in Pipedream's hosted UI."
        ),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _row_to_out(row: tuple, accounts_discovered: int = 0) -> OAuthBrokerOut:
    broker_id, broker_type, client_id, project_id, default_external_user_id, created_at = row
    return OAuthBrokerOut(
        id=broker_id,
        type=broker_type,
        config={
            "client_id": client_id,
            "project_id": project_id,
            "default_external_user_id": default_external_user_id or "default",
        },
        created_at=created_at,
        accounts_discovered=accounts_discovered,
    )


def _make_broker_id(broker_type: str, existing_ids: list[str]) -> str:
    base = broker_type
    if base not in existing_ids:
        return base
    n = 2
    while f"{base}-{n}" in existing_ids:
        n += 1
    return f"{base}-{n}"


def _extract_pipedream_config(config: dict) -> tuple[str, str, str, str | None, str, str]:
    """Extract and validate Pipedream config fields.

    Returns (client_id, client_secret, project_id, support_email, environment, default_external_user_id).
    app_name and logo_url are set by the backend — not exposed in the API.
    """
    missing = [f for f in ("client_id", "client_secret", "project_id") if not config.get(f)]
    if missing:
        raise HTTPException(
            400,
            f"Missing required Pipedream config fields: {', '.join(missing)}. "
            "Expected: client_id, client_secret, project_id",
        )
    support_email = config.get("support_email") or None
    environment = config.get("environment") or "production"
    default_external_user_id = config.get("default_external_user_id") or "default"
    return (
        config["client_id"],
        config["client_secret"],
        config["project_id"],
        support_email,
        environment,
        default_external_user_id,
    )


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=OAuthBrokerOut,
    summary="Register an OAuth broker",
    description=_CREATE_DESCRIPTION,
    dependencies=[Depends(require_human_session)],
    openapi_extra={
        "requestBody": {
            "description": "Broker configuration: type (e.g. 'pipedream'), provider-specific config, and encrypted credentials"
        }
    },
)
async def create_oauth_broker(body: OAuthBrokerCreate):
    if body.type not in _SUPPORTED_TYPES:
        raise HTTPException(400, f"Unsupported broker type: '{body.type}'. Supported: pipedream")

    client_id, client_secret, project_id, support_email, environment, default_external_user_id = (
        _extract_pipedream_config(body.config)
    )

    async with get_db() as db:
        async with db.execute("SELECT id FROM oauth_brokers") as cur:
            existing_ids = [r[0] for r in await cur.fetchall()]

        broker_id = (
            body.id
            if body.id and body.id not in existing_ids
            else _make_broker_id(body.type, existing_ids)
        )
        secret_enc = vault.encrypt(client_secret)

        await db.execute(
            """INSERT INTO oauth_brokers
               (id, type, client_id, client_secret_enc, project_id,
                environment, default_external_user_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                broker_id,
                body.type,
                client_id,
                secret_enc,
                project_id,
                environment,
                default_external_user_id,
                time.time(),
            ),
        )
        await db.commit()

    broker = PipedreamOAuthBroker(
        broker_id=broker_id,
        client_id=client_id,
        client_secret=client_secret,
        project_id=project_id,
        environment=environment,
        default_external_user_id=default_external_user_id,
    )
    oauth_broker_registry.register(broker)

    # Configure the Pipedream project (name, email, logo) — best-effort, non-fatal
    try:
        await broker.configure_project(
            app_name="Jentic Mini",
            support_email=support_email,
            logo_url="https://jentic.com/favicon.svg",
        )
    except Exception as exc:
        log.warning("Project configuration failed for broker %s: %s", broker_id, exc)

    accounts_discovered = 0
    try:
        accounts_discovered = await broker.discover_accounts(default_external_user_id)
    except Exception as exc:
        log.warning("Account sync failed for broker %s: %s", broker_id, exc)

    log.info("OAuth broker '%s' registered (%d account mappings)", broker_id, accounts_discovered)

    return OAuthBrokerOut(
        id=broker_id,
        type=body.type,
        config={
            "client_id": client_id,
            "project_id": project_id,
            "default_external_user_id": default_external_user_id,
        },
        created_at=time.time(),
        accounts_discovered=accounts_discovered,
    )


class OAuthBrokerUpdate(NormModel):
    """Update OAuth broker configuration. Only provided fields are changed; secrets remain encrypted."""

    config: dict[str, Any] = Field(
        ...,
        description=(
            "Updated provider-specific configuration. "
            "For `pipedream`: `client_id`, `client_secret`, `project_id` are all accepted. "
            "Fields not supplied are left unchanged. "
            "`client_secret` is write-only — Fernet-encrypted at rest, never returned."
        ),
        examples=[_PIPEDREAM_CONFIG_EXAMPLE],
    )


@router.patch(
    "/{broker_id}",
    response_model=OAuthBrokerOut,
    summary="Update an OAuth broker configuration",
    dependencies=[Depends(require_human_session)],
    openapi_extra={
        "requestBody": {
            "description": "Provider-specific config fields to update: client_id, client_secret, project_id — only provided fields are changed, secrets re-encrypted"
        }
    },
)
async def update_oauth_broker(broker_id: BrokerIdPath, body: OAuthBrokerUpdate):
    """Update client_id, client_secret, and/or project_id for an existing broker.

    Only supplied fields are changed. client_secret is re-encrypted if provided.
    """
    async with get_db() as db:
        async with db.execute(
            "SELECT id, type, client_id, client_secret_enc, project_id, environment, "
            "default_external_user_id, created_at FROM oauth_brokers WHERE id=?",
            (broker_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, f"OAuth broker '{broker_id}' not found")

        (
            _,
            broker_type,
            old_client_id,
            old_secret_enc,
            old_project_id,
            environment,
            ext_user_id,
            created_at,
        ) = row

        new_client_id = body.config.get("client_id") or old_client_id
        new_project_id = body.config.get("project_id") or old_project_id
        support_email = body.config.get("support_email") or None

        if body.config.get("client_secret"):
            new_secret_enc = vault.encrypt(body.config["client_secret"])
            new_client_secret = body.config["client_secret"]
        else:
            new_secret_enc = old_secret_enc
            new_client_secret = vault.decrypt(old_secret_enc)

        await db.execute(
            "UPDATE oauth_brokers SET client_id=?, client_secret_enc=?, project_id=? WHERE id=?",
            (new_client_id, new_secret_enc, new_project_id, broker_id),
        )
        await db.commit()

    # Re-register broker in registry with updated credentials
    broker = PipedreamOAuthBroker(
        broker_id=broker_id,
        client_id=new_client_id,
        client_secret=new_client_secret,
        project_id=new_project_id,
        environment=environment,
        default_external_user_id=ext_user_id or "default",
    )
    oauth_broker_registry.deregister(broker_id)
    oauth_broker_registry.register(broker)

    try:
        await broker.configure_project(
            app_name="Jentic Mini",
            support_email=support_email,
            logo_url="https://jentic.com/favicon.svg",
        )
    except Exception as exc:
        log.warning("Project configuration failed after update for broker %s: %s", broker_id, exc)

    log.info("OAuth broker '%s' updated", broker_id)
    return OAuthBrokerOut(
        id=broker_id,
        type=broker_type,
        config={
            "client_id": new_client_id,
            "project_id": new_project_id,
            "default_external_user_id": ext_user_id or "default",
        },
        created_at=created_at,
    )


@router.get(
    "",
    summary="List registered OAuth brokers",
    tags=["inspect"],
    openapi_extra=agent_hints(
        when_to_use="Use to discover available OAuth brokers (Pipedream, future: Jentic native) for delegated OAuth credential management. Returns list of registered brokers with type, client_id, project_id, and default_external_user_id. Client_secret is never included. Accessible to both agents (toolkit key) and humans (session). Use before connecting apps or syncing accounts.",
        prerequisites=["Requires authentication (toolkit key or human session)"],
        avoid_when="Do not use to retrieve individual broker details — use GET /oauth-brokers/{broker_id} instead. Do not use to manage OAuth accounts — use POST /oauth-brokers/{broker_id}/connect-link to initiate OAuth.",
        related_operations=[
            "POST /oauth-brokers — register a new OAuth broker (Pipedream)",
            "GET /oauth-brokers/{broker_id} — get detailed broker configuration and account statistics",
            "POST /oauth-brokers/{broker_id}/connect-link — initiate OAuth flow for an API via broker",
            "POST /oauth-brokers/{broker_id}/sync — pull connected accounts into Jentic after OAuth",
        ],
    ),
)
async def list_oauth_brokers():
    """Return all registered OAuth brokers as a flat list. `client_secret` is never included.

    Accessible to both agents (toolkit key) and humans (session).
    """
    async with get_db() as db:
        async with db.execute(
            "SELECT id, type, client_id, project_id, default_external_user_id, created_at FROM oauth_brokers"
        ) as cur:
            rows = await cur.fetchall()

    return [_row_to_out(r) for r in rows]


@router.get(
    "/{broker_id}",
    summary="Get an OAuth broker",
    tags=["inspect"],
)
async def get_oauth_broker(broker_id: BrokerIdPath):
    """
    Retrieve OAuth broker configuration and metadata.

    Returns broker type, client ID, project ID, and connected account statistics.
    Use this to verify a broker is registered before creating connect links or syncing accounts.

    For connected account details, use `GET /oauth-brokers/{broker_id}/accounts`.
    """
    async with get_db() as db:
        async with db.execute(
            "SELECT id, type, client_id, project_id, default_external_user_id, created_at "
            "FROM oauth_brokers WHERE id=?",
            (broker_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"OAuth broker '{broker_id}' not found")
    return _row_to_out(row)


class ConnectLinkRequest(NormModel):
    """Generate a Pipedream Connect Link for authorizing a new OAuth account. Returns URL for user authorization."""

    app: str = Field(
        ...,
        description=(
            "The Pipedream app slug to connect (e.g. `gmail`, `slack`, `github`, `stripe`). "
            "Required — Pipedream Connect Links must target a specific app. "
            "Find the slug via `GET /oauth-brokers/{id}/apps` or at pipedream.com/apps."
        ),
        examples=["gmail", "slack", "github"],
    )
    label: str = Field(
        ...,
        min_length=1,
        description=(
            "A human-readable name for this connection, e.g. `work email` or `personal email`. "
            "Required because Pipedream only returns the app name ('Gmail'), not the account "
            "address — without a label there is no way to distinguish multiple accounts "
            "for the same app. This label is carried through to the resulting credential "
            "in `GET /credentials` and used when provisioning the credential to a toolkit. "
            "Must be non-empty — no silent fallbacks to app slug or API ID."
        ),
        examples=["work email", "personal email", "main Slack workspace"],
    )
    api_id: str | None = Field(
        None,
        description=(
            "The Jentic catalog API ID this connection maps to (e.g. `googleapis.com/gmail`). "
            "If provided, this overrides the automatic slug-map lookup during sync — the "
            "credential will be registered under exactly this API ID. "
            "Find the right ID via `GET /catalog?q=<name>`. "
            "If omitted, the slug map is used as a fallback (may not match the catalog ID)."
        ),
        examples=["googleapis.com/gmail", "slack.com/api", "api.github.com"],
    )


@router.post(
    "/{broker_id}/connect-link",
    summary="Generate a Pipedream Connect Link for authorising apps",
    openapi_extra={
        "requestBody": {
            "description": "Connect link request: Pipedream app slug (e.g. gmail, slack), human-readable label for the connection, and optional api_id override for catalog binding"
        }
    },
)
async def create_connect_link(broker_id: BrokerIdPath, body: ConnectLinkRequest, request: Request):
    """Generate a short-lived Pipedream Connect Link URL.

    Visit the returned `connect_link_url` in a browser to authorise SaaS apps
    (e.g. Gmail, Slack, GitHub) via Pipedream's hosted OAuth consent UI.

    After completing the OAuth flow, call `POST /oauth-brokers/{id}/sync` to
    pull the new account into jentic-mini so requests start routing through it.

    The link expires after ~1 hour. Generate a new one if it expires before use.

    Intentionally open to agents (not human-session-only): only a human can
    complete the OAuth flow, so generating the link is safe for agents to initiate.
    Requires at minimum a valid toolkit key or trusted-subnet (admin) access.
    """
    is_admin = getattr(request.state, "is_admin", False)
    is_human = getattr(request.state, "is_human_session", False)
    has_toolkit = getattr(request.state, "toolkit_id", None) is not None
    if not (is_admin or is_human or has_toolkit):
        raise build_human_only_error()
    live_broker = next(
        (b for b in oauth_broker_registry.brokers if getattr(b, "broker_id", None) == broker_id),
        None,
    )
    if live_broker is None:
        brokers = await PipedreamOAuthBroker.from_db()
        live_broker = next((b for b in brokers if b.broker_id == broker_id), None)
        if live_broker:
            oauth_broker_registry.register(live_broker)

    if live_broker is None:
        raise HTTPException(404, f"OAuth broker '{broker_id}' not found or could not be loaded")

    if not hasattr(live_broker, "create_connect_token"):
        raise HTTPException(400, "Broker type does not support Connect Links")

    # Resolve app slug from api_id if the provided slug looks like a path segment
    # rather than a valid Pipedream slug (e.g. "calendar" instead of "google_calendar").
    if body.api_id:
        resolved_slug = api_host_to_pd_slug(body.api_id)
        if resolved_slug:
            body.app = resolved_slug

    # Build the success redirect URI — Pipedream will append nothing of its own,
    # so we encode all the context we need (label, app, api_id, external_user_id)
    # as query params. The callback endpoint reads these, stores the pending label,
    # triggers a sync, and redirects the user to the credentials UI.
    # Derive external_user_id from the broker's stored config — callers must not
    # override this, as an incorrect value silently routes credentials to a
    # Pipedream user that the sync will never query.
    external_user_id = getattr(live_broker, "default_external_user_id", None) or "default"

    callback_params = {
        "label": body.label,
        "app": body.app,
        "external_user_id": external_user_id,
    }
    if body.api_id:
        callback_params["api_id"] = body.api_id
    callback_path = (
        f"/oauth-brokers/{broker_id}/connect-callback?{urllib.parse.urlencode(callback_params)}"
    )
    success_redirect_uri = build_canonical_url(request, callback_path)

    try:
        result = await live_broker.create_connect_token(
            external_user_id,
            success_redirect_uri=success_redirect_uri,
        )
    except Exception as exc:
        raise HTTPException(502, f"Failed to create Pipedream Connect Token: {exc}")

    # Pipedream requires the app slug appended to the connect link URL
    connect_link_url = result["connect_link_url"]
    if "&app=" not in connect_link_url:
        connect_link_url = f"{connect_link_url}&app={body.app}"

    return {
        "broker_id": broker_id,
        "external_user_id": external_user_id,
        "app": body.app,
        "connect_link_url": connect_link_url,
        "expires_at": result["expires_at"],
        "next_step": f"Visit connect_link_url in your browser, authorise {body.app}, then the browser will redirect automatically and sync will run",
    }


@router.get(
    "/{broker_id}/connect-callback",
    summary="OAuth connect-link completion callback (browser redirect)",
    include_in_schema=False,  # not a user-facing API endpoint
)
async def connect_callback(
    broker_id: BrokerIdPath,
    request: Request,
    label: str = Query(..., description="Label set at connect-link time"),
    app: str = Query(..., description="Pipedream app slug"),
    external_user_id: str = Query("default"),
    api_id: str | None = Query(None),
    replace_account_id: str | None = Query(
        None, description="Account ID to delete after successful reconnect"
    ),
):
    """Browser callback after Pipedream OAuth completion.

    Pipedream redirects here once the user successfully authorises an app.
    We write the pending label to oauth_broker_connect_labels (keyed by
    app_slug, as before), trigger a sync immediately, then redirect the
    user to the credentials page of the UI.

    This endpoint is hit by the user's browser — no auth token required.
    Labels come from URL params encoded at connect-link time.
    """
    # Write the pending label — safe to INSERT OR REPLACE here because we are
    # processing one completion at a time (browser callback is synchronous
    # from the user's perspective). The race that motivated this redesign was
    # *two links created before either was completed*; at callback time each
    # link fires its own redirect with its own label param.
    async with get_db() as db:
        await db.execute(
            """INSERT OR REPLACE INTO oauth_broker_connect_labels
               (id, broker_id, external_user_id, app_slug, label, api_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                broker_id,
                external_user_id,
                app,
                label,
                api_id,
                time.time(),
            ),
        )
        await db.commit()

    # Trigger sync immediately so the credential lands before the UI loads
    live_broker = next(
        (b for b in oauth_broker_registry.brokers if getattr(b, "broker_id", None) == broker_id),
        None,
    )
    if live_broker is None:
        brokers = await PipedreamOAuthBroker.from_db()
        live_broker = next((b for b in brokers if b.broker_id == broker_id), None)
        if live_broker:
            oauth_broker_registry.register(live_broker)

    synced_ok = False
    if live_broker is not None and hasattr(live_broker, "discover_accounts"):
        try:
            await live_broker.discover_accounts(external_user_id)
            synced_ok = True
            log.info(
                "connect-callback: sync ok for broker %s, replace_account_id=%s",
                broker_id,
                replace_account_id,
            )
        except Exception as exc:
            log.warning("connect-callback: sync failed for broker %s: %s", broker_id, exc)
            # Don't block the redirect — user will see the credential once they
            # manually sync, or on next automatic sync.
    else:
        log.warning(
            "connect-callback: no live_broker found for %s — sync skipped, replace_account_id=%s will NOT be cleaned up",
            broker_id,
            replace_account_id,
        )

    # If this was a reconnect, clean up the old account — but only after sync
    # confirmed the new connection is present.
    if replace_account_id and synced_ok:
        # Verify the new account landed (old account_id should be gone; any
        # remaining account for this app_slug other than replace_account_id is new).
        async with get_db() as db:
            async with db.execute(
                """SELECT account_id FROM oauth_broker_accounts
                   WHERE broker_id=? AND external_user_id=? AND app_slug=?
                   AND account_id != ?""",
                (broker_id, external_user_id, app, replace_account_id),
            ) as cur:
                new_row = await cur.fetchone()
        if new_row:
            log.info(
                "Reconnect: new account %s confirmed, deleting old account %s",
                new_row[0],
                replace_account_id,
            )
            try:
                # Fetch all api_hosts for this account (may have multiple host mappings)
                async with get_db() as db:
                    async with db.execute(
                        "SELECT api_host FROM oauth_broker_accounts WHERE broker_id=? AND account_id=?",
                        (broker_id, replace_account_id),
                    ) as cur:
                        old_rows = await cur.fetchall()

                if old_rows:
                    old_cred_ids = [
                        broker_credential_id(broker_id, replace_account_id, r[0]) for r in old_rows
                    ]

                    # 1. Revoke upstream in Pipedream (best-effort, async)
                    if live_broker is not None:
                        try:
                            pd_token = await live_broker._get_access_token()
                            pd_url = (
                                f"https://api.pipedream.com/v1/connect/"
                                f"{live_broker.project_id}/accounts/{replace_account_id}"
                            )
                            async with httpx.AsyncClient(timeout=10) as client:
                                await client.delete(
                                    pd_url,
                                    headers={
                                        "Authorization": f"Bearer {pd_token}",
                                        "X-PD-Environment": live_broker.environment,
                                    },
                                )
                            log.info(
                                "Reconnect: revoked old Pipedream account %s", replace_account_id
                            )
                        except Exception as pd_exc:
                            log.warning(
                                "Reconnect: Pipedream revoke failed for %s (continuing): %s",
                                replace_account_id,
                                pd_exc,
                            )

                    # 2. Remove from toolkit provisioning + oauth_broker_accounts
                    async with get_db() as db:
                        for old_cred_id in old_cred_ids:
                            await db.execute(
                                "DELETE FROM toolkit_credentials WHERE credential_id=?",
                                (old_cred_id,),
                            )
                        await db.execute(
                            "DELETE FROM oauth_broker_accounts WHERE broker_id=? AND account_id=?",
                            (broker_id, replace_account_id),
                        )
                        await db.commit()

                    # 3. Delete from vault
                    for old_cred_id in old_cred_ids:
                        await vault.delete_credential(old_cred_id)

                    log.info(
                        "Reconnect: removed old account %s (%d credentials) after successful reconnect",
                        replace_account_id,
                        len(old_cred_ids),
                    )
                else:
                    log.warning(
                        "Reconnect: old account %s already gone from DB — nothing to clean up",
                        replace_account_id,
                    )
            except Exception as exc:
                log.warning(
                    "Reconnect: failed to remove old account %s: %s", replace_account_id, exc
                )
        else:
            log.warning(
                "Reconnect: new account NOT found in DB after sync for broker=%s app=%s external_user_id=%s (replacing %s)",
                broker_id,
                app,
                external_user_id,
                replace_account_id,
            )

    # Redirect to the appropriate UI page (return_to defaults to /oauth-brokers)
    return_to = request.query_params.get("return_to", "/oauth-brokers")
    safe_return_to = validate_relative_redirect(return_to)
    if safe_return_to is None:
        if return_to:
            # Truncate to bound log volume under probe-rate attacks.
            audit_log.warning("OAUTH_RETURN_TO_BLOCKED return_to=%r", return_to[:200])
        safe_return_to = "/oauth-brokers"
    ui_url = build_absolute_url(request, safe_return_to)
    return RedirectResponse(url=ui_url, status_code=302)


@router.post(
    "/{broker_id}/sync",
    summary="Sync connected accounts from the OAuth broker",
    openapi_extra={
        "requestBody": {
            "description": "Sync request: list of API slugs to sync accounts for (fetches connected accounts from broker and imports as Jentic credentials)"
        }
    },
)
async def sync_broker_accounts(broker_id: BrokerIdPath, body: SyncRequest, request: Request):
    """Re-fetch connected accounts from the provider and update local mappings.

    Call this after connecting a new app via Pipedream's hosted OAuth UI —
    the new account will appear in subsequent `GET /oauth-brokers/{id}/accounts`
    responses and the broker will start routing requests to it automatically.

    This does **not** affect accounts already connected — it is additive.

    Intentionally open to agents: syncing pulls in credentials the human already
    authorised. No new OAuth flows are initiated.
    """
    is_admin = getattr(request.state, "is_admin", False)
    is_human = getattr(request.state, "is_human_session", False)
    has_toolkit = getattr(request.state, "toolkit_id", None) is not None
    if not (is_admin or is_human or has_toolkit):
        raise build_human_only_error()

    async with get_db() as db:
        async with db.execute("SELECT type FROM oauth_brokers WHERE id=?", (broker_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"OAuth broker '{broker_id}' not found")

    live_broker = next(
        (b for b in oauth_broker_registry.brokers if getattr(b, "broker_id", None) == broker_id),
        None,
    )
    if live_broker is None:
        brokers = await PipedreamOAuthBroker.from_db()
        live_broker = next((b for b in brokers if b.broker_id == broker_id), None)
        if live_broker:
            oauth_broker_registry.register(live_broker)

    if live_broker is None:
        raise HTTPException(500, "Broker found in DB but could not be instantiated")

    try:
        count = await live_broker.discover_accounts(body.external_user_id, raise_on_auth_error=True)
    except httpx.HTTPStatusError as exc:
        # Pipedream rejected the broker's own credentials (wrong client id /
        # secret / project id). Surface it as 401 so the caller can tell
        # "bad credentials" apart from a valid broker with nothing connected
        # (which returns count=0 with a 200).
        if exc.response is not None and exc.response.status_code in (401, 403):
            raise HTTPException(
                401,
                "Pipedream rejected these credentials. "
                "Check the client ID, secret, and project ID.",
            )
        raise HTTPException(502, f"Sync failed: {exc}")
    except Exception as exc:
        raise HTTPException(502, f"Sync failed: {exc}")

    # Return the credential IDs created/updated so the caller knows what to provision
    async with get_db() as db:
        async with db.execute(
            "SELECT id, label, api_id FROM credentials WHERE auth_type='pipedream_oauth' "
            "AND api_id IN (SELECT api_host FROM oauth_broker_accounts "
            "WHERE broker_id=? AND external_user_id=?)",
            (broker_id, body.external_user_id),
        ) as cur:
            cred_rows = await cur.fetchall()

    credentials = [{"id": r[0], "label": r[1], "api_host": r[2]} for r in cred_rows]

    return {
        "broker_id": broker_id,
        "external_user_id": body.external_user_id,
        "accounts_synced": count,
        "credentials": credentials,
        "next_step": (
            "Provision a credential to a toolkit: "
            "POST /toolkits/{toolkit_id}/credentials with {credential_id}"
        ),
        "status": "ok",
    }


@router.get(
    "/{broker_id}/accounts",
    summary="List connected accounts for an OAuth broker",
    tags=["inspect"],
)
async def list_broker_accounts(
    broker_id: BrokerIdPath, external_user_id: ExternalUserIdQuery = None
):
    """List the OAuth-connected account mappings stored for this broker.

    Each entry represents a SaaS app the user has connected via Pipedream's OAuth
    UI, along with the API host it maps to and the Pipedream `account_id` used when
    routing requests through the proxy.

    Use `POST /oauth-brokers/{id}/sync` to refresh this list from Pipedream.
    """
    async with get_db() as db:
        async with db.execute("SELECT id FROM oauth_brokers WHERE id=?", (broker_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, f"OAuth broker '{broker_id}' not found")

        query = (
            "SELECT external_user_id, api_host, app_slug, account_id, label, healthy, synced_at "
            "FROM oauth_broker_accounts WHERE broker_id=?"
        )
        params: tuple = (broker_id,)
        if external_user_id:
            query += " AND external_user_id=? ORDER BY api_host"
            params = (broker_id, external_user_id)
        else:
            query += " ORDER BY external_user_id, api_host"

        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()

    cols = [
        "external_user_id",
        "api_host",
        "app_slug",
        "account_id",
        "label",
        "healthy",
        "synced_at",
    ]
    return [dict(zip(cols, r)) for r in rows]


@router.delete(
    "/{broker_id}/accounts/{account_id}",
    summary="Remove a connected account from an OAuth broker",
    dependencies=[Depends(require_human_session)],
)
async def delete_broker_account(
    broker_id: BrokerIdPath,
    account_id: Annotated[str, Path(description="Connected account ID to delete")],
):
    """Remove a specific connected account from this broker.

    This performs three actions in order:
    1. Revokes the account in the upstream provider (Pipedream) via their API
    2. Removes the associated credential from all toolkit provisioning
    3. Deletes the credential from the vault and the account from the local DB

    If the Pipedream revoke fails, the local cleanup still proceeds (with a warning).
    """
    async with get_db() as db:
        async with db.execute("SELECT id FROM oauth_brokers WHERE id=?", (broker_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, f"OAuth broker '{broker_id}' not found")

        async with db.execute(
            "SELECT account_id, api_host FROM oauth_broker_accounts WHERE broker_id=? AND account_id=?",
            (broker_id, account_id),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        raise HTTPException(
            404, f"No connected account '{account_id}' found for broker '{broker_id}'"
        )
    # An account can map to multiple api_hosts (e.g. Google Sheets → sheets.googleapis.com + googleapis.com/sheets)
    cred_ids = [broker_credential_id(broker_id, account_id, r[1]) for r in rows]

    # 1. Revoke upstream in Pipedream
    pipedream_revoked = await revoke_pipedream_account_upstream(broker_id, account_id)

    # 2. Remove from toolkit provisioning + vault for all host mappings
    async with get_db() as db:
        for cred_id in cred_ids:
            await db.execute("DELETE FROM toolkit_credentials WHERE credential_id=?", (cred_id,))
        await db.commit()

    for cred_id in cred_ids:
        await vault.delete_credential(cred_id)

    # 3. Delete from oauth_broker_accounts
    async with get_db() as db:
        await db.execute(
            "DELETE FROM oauth_broker_accounts WHERE broker_id=? AND account_id=?",
            (broker_id, account_id),
        )
        await db.commit()

    return {
        "status": "ok",
        "broker_id": broker_id,
        "account_id": account_id,
        "credential_ids": cred_ids,
        "pipedream_revoked": pipedream_revoked,
        "deleted": True,
    }


class AccountUpdate(NormModel):
    """Rename a connected OAuth account. Label is used in UI and credential binding displays."""

    label: str = Field(..., min_length=1, description="New display label for this account")


@router.patch(
    "/{broker_id}/accounts/{account_id}",
    summary="Update a connected account (e.g. rename label)",
    openapi_extra={
        "requestBody": {
            "description": "Account update: new display label for this connected OAuth account"
        }
    },
    dependencies=[Depends(require_human_session)],
)
async def update_broker_account(
    broker_id: BrokerIdPath,
    account_id: Annotated[str, Path(description="Connected account ID to update")],
    body: AccountUpdate,
):
    """Patch a connected account record.

    Updates the display label for a connected OAuth account. The account remains linked
    to the same external OAuth identity and credentials are not affected. Label changes
    are reflected in both the oauth_broker_accounts table and any associated credentials
    in the vault.

    Parameters:
        broker_id: OAuth broker ID (e.g. 'pipedream')
        account_id: Connected account ID from the broker
        body: Update request containing the new label

    Returns:
        Updated account_id and label.

    Auth: Requires human session (admin only).

    Currently supports updating label only. Future versions may support updating
    additional account metadata.
    """
    new_label = body.label
    async with get_db() as db:
        async with db.execute(
            "SELECT account_id FROM oauth_broker_accounts WHERE broker_id=? AND account_id=?",
            (broker_id, account_id),
        ) as cur:
            if not await cur.fetchone():
                raise HTTPException(
                    404, f"No connected account '{account_id}' found for broker '{broker_id}'"
                )
        await db.execute(
            "UPDATE oauth_broker_accounts SET label=? WHERE broker_id=? AND account_id=?",
            (new_label.strip(), broker_id, account_id),
        )
        # Also update the credential label in the vault if it exists
        await db.execute(
            "UPDATE credentials SET label=? WHERE id LIKE ?",
            (new_label.strip(), f"{broker_id}-{account_id}-%"),
        )
        await db.commit()
    return {"account_id": account_id, "label": new_label.strip()}


@router.post(
    "/{broker_id}/accounts/{account_id}/reconnect-link",
    summary="Get a reconnect link for an existing connected account",
    dependencies=[Depends(require_human_session)],
)
async def reconnect_account_link(
    broker_id: BrokerIdPath,
    account_id: Annotated[str, Path(description="OAuth broker account ID to reconnect")],
    request: Request,
):
    """Generate a new OAuth connect link for an existing connected account.

    The returned URL sends the user through the Pipedream OAuth flow for the
    same app slug.  On completion, the callback will:

    1. Sync the broker (discovering the new account).
    2. If the new account is confirmed present, delete the old account.

    This allows a user to re-authorise a broken connection without losing the
    existing credential until the replacement is confirmed.
    """
    # Load the existing account record
    async with get_db() as db:
        async with db.execute(
            """SELECT app_slug, label, api_id, external_user_id
               FROM oauth_broker_accounts WHERE broker_id=? AND account_id=?""",
            (broker_id, account_id),
        ) as cur:
            row = await cur.fetchone()

    if not row:
        raise HTTPException(
            404, f"No connected account '{account_id}' found for broker '{broker_id}'"
        )

    app_slug, label, api_id, external_user_id = row
    label = label or app_slug
    external_user_id = external_user_id or "default"

    # Get the live broker
    live_broker = next(
        (b for b in oauth_broker_registry.brokers if getattr(b, "broker_id", None) == broker_id),
        None,
    )
    if live_broker is None:
        brokers = await PipedreamOAuthBroker.from_db()
        live_broker = next((b for b in brokers if b.broker_id == broker_id), None)
        if live_broker:
            oauth_broker_registry.register(live_broker)

    if live_broker is None:
        raise HTTPException(404, f"OAuth broker '{broker_id}' not found or could not be loaded")

    if not hasattr(live_broker, "create_connect_token"):
        raise HTTPException(400, "Broker type does not support reconnect links")

    # Build callback URL — includes replace_account_id so the old account is
    # cleaned up automatically after the new one is confirmed present
    callback_params = {
        "label": label,
        "app": app_slug,
        "external_user_id": external_user_id,
        "replace_account_id": account_id,
        "return_to": "/credentials",
    }
    if api_id:
        callback_params["api_id"] = api_id
    callback_path = (
        f"/oauth-brokers/{broker_id}/connect-callback?{urllib.parse.urlencode(callback_params)}"
    )
    success_redirect_uri = build_canonical_url(request, callback_path)

    try:
        result = await live_broker.create_connect_token(
            external_user_id,
            success_redirect_uri=success_redirect_uri,
        )
    except Exception as exc:
        raise HTTPException(502, f"Failed to create Pipedream Connect Token: {exc}")

    connect_link_url = result["connect_link_url"]
    if "&app=" not in connect_link_url:
        connect_link_url = f"{connect_link_url}&app={app_slug}"

    return {
        "broker_id": broker_id,
        "account_id": account_id,
        "app": app_slug,
        "label": label,
        "connect_link_url": connect_link_url,
        "expires_at": result["expires_at"],
        "next_step": (
            f"Visit connect_link_url to re-authorise {app_slug}. "
            f"On success, the old account ({account_id}) will be replaced automatically."
        ),
    }


@router.delete(
    "/{broker_id}",
    summary="Remove an OAuth broker",
    dependencies=[Depends(require_human_session)],
)
async def delete_oauth_broker(broker_id: BrokerIdPath):
    """Remove a broker and all its connected accounts and credentials.

    Cascades through oauth_broker_accounts -> toolkit_credentials -> vault.
    Does not revoke tokens on the provider side - do that in the provider's dashboard.
    """
    async with get_db() as db:
        async with db.execute("SELECT id FROM oauth_brokers WHERE id=?", (broker_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, f"OAuth broker '{broker_id}' not found")

        # Collect all credential IDs for this broker before deleting.
        # Credentials are keyed as 'pipedream-{account_id}-{host_slug}' in the
        # credentials table; oauth_broker_accounts stores account_id, not cred_id.
        async with db.execute(
            "SELECT account_id, api_host FROM oauth_broker_accounts WHERE broker_id=?",
            (broker_id,),
        ) as cur:
            accounts = await cur.fetchall()
        cred_ids = [
            broker_credential_id(broker_id, account_id, api_host)
            for account_id, api_host in accounts
        ]

        # Remove toolkit bindings and broker account rows
        for cred_id in cred_ids:
            await db.execute("DELETE FROM toolkit_credentials WHERE credential_id=?", (cred_id,))
        await db.execute("DELETE FROM oauth_broker_accounts WHERE broker_id=?", (broker_id,))
        await db.execute("DELETE FROM oauth_broker_connect_labels WHERE broker_id=?", (broker_id,))
        await db.execute("DELETE FROM oauth_brokers WHERE id=?", (broker_id,))
        await db.commit()

    # Delete credentials from vault after DB commit
    for cred_id in cred_ids:
        try:
            await vault.delete_credential(cred_id)
        except Exception:
            log.warning(
                "Could not delete credential '%s' from vault during broker removal", cred_id
            )

    oauth_broker_registry.deregister(broker_id)
    log.info("OAuth broker '%s' removed along with %d credential(s)", broker_id, len(cred_ids))

    return {
        "status": "ok",
        "broker_id": broker_id,
        "deleted": True,
        "credentials_removed": len(cred_ids),
    }
