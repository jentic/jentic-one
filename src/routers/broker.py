"""
Broker — transparent HTTP reverse proxy with credential injection.

URL pattern: /{upstream_host}/{path}
  e.g. POST /api.stripe.com/v1/payment_intents
       GET  /api.github.com/repos/octocat/Hello-World

The broker:
  1. Detects the upstream host from the first path segment (must contain a ".")
  2. Looks up the credential for that host in the vault (toolkit-scoped if
     a toolkit API key was used, otherwise global)
  3. Injects the credential into the forwarded request headers
  4. Forwards the request verbatim (method, headers, body, query params)
  5. Returns the upstream response verbatim — no wrapping

Special request headers:
  X-Jentic-API-Key    — Jentic authentication (handled by auth middleware)
  X-Jentic-Simulate   — "true" to skip the upstream call and return would_send
  X-Jentic-Credential — credential alias; acts as a HARD OVERRIDE — the named
                        credential is used for both policy enforcement and injection,
                        bypassing host-matching auto-selection entirely. Required when
                        multiple credentials share the same upstream host (e.g. multiple
                        Google services all routing through googleapis.com).
  X-Jentic-Service    — service name (Pipedream app_slug, e.g. "google_calendar") to
                        select the right credential when multiple share a host.
                        Friendlier alternative to X-Jentic-Credential.
  X-Jentic-Callback   — webhook URL for async result delivery (TODO: phase 2)

Response headers added:
  X-Jentic-Error              — "true" when the error is from Jentic, not upstream
  X-Jentic-Execution-Id       — trace ID (exec_*) for this broker call
  X-Jentic-Credential-Used    — ID of the credential actually injected (always set when
                                a credential was used, enabling callers to detect wrong-
                                credential selection on multi-service hosts)
  X-Jentic-Credential-Ambiguous — "true" when multiple credentials matched and no
                                  alias/service was specified to disambiguate
"""

import asyncio
import base64
import json
import logging
import os
import re
import time
from typing import Annotated
from urllib.parse import unquote, urlparse

import aiohttp
from fastapi import APIRouter, HTTPException, Path, Request, Response
from jentic.apitools.openapi.common.uri import is_http_https_url

import src.vault as vault
from src.config import JENTIC_PUBLIC_HOSTNAME
from src.db import DEFAULT_TOOLKIT_ID, get_db
from src.oauth_broker import registry
from src.openapi_helpers import agent_hints
from src.routers.credentials import api_has_native_scheme
from src.routers.jobs import create_job, discard_task, register_task, update_job
from src.routers.overlays import confirm_overlay
from src.routers.toolkits import check_credential_policy
from src.routers.traces import new_trace_id, safe_write_trace
from src.routers.workflows import dispatch_workflow
from src.utils import parse_prefer_wait


log = logging.getLogger("jentic.broker")

router = APIRouter(tags=["execute"])


class ServiceNotFoundError(Exception):
    """Raised when X-Jentic-Service doesn't match any credential for the host."""


# Hop-by-hop headers that must NOT be forwarded
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    # Content-Length from upstream is wrong after any proxy buffering; let ASGI recalculate
    "content-length",
    # Jentic-specific — consumed here, not forwarded upstream
    "x-jentic-api-key",
    "x-jentic-simulate",
    "x-jentic-credential",
    "x-jentic-service",
    "x-jentic-callback",
    # Internal cross-process workflow attribution — set by the arazzo-runner
    # subprocess on loopback hops and consumed here (see the X-Jentic-Parent-Trace
    # read below). Must never reach the real upstream: it would leak Jentic's
    # internal trace IDs to every API a workflow calls.
    "x-jentic-parent-trace",
    # Browser session cookies (jentic_session JWT in particular) authenticate
    # the caller to *Jentic*, not to the upstream API. Forwarding them would
    # (a) leak the JWT to whatever upstream is being proxied (it gets logged
    # there and echoed back into broker job result bodies for any GET /jobs
    # admin to read), and (b) be the wrong identity for the upstream anyway.
    # See #56 for the response-side analogue (closed wontfix because response
    # headers are legitimate application data); the request-side reasoning is
    # different — the agent's Cookie is never intended for the upstream when
    # called through the broker.
    "cookie",
    # Host is set from the target URL
    "host",
    # Reverse-proxy headers injected by nginx/traefik/etc. — these describe
    # the inbound hop to Jentic, not the outbound hop to the upstream API.
    # Forwarding them causes failures: e.g. CloudFront returns 403
    # "Host not permitted" when it sees x-forwarded-host with the Jentic
    # hostname instead of the upstream API hostname.
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-port",
    "x-forwarded-proto",
    "x-forwarded-scheme",
    "x-real-ip",
    "x-scheme",
}

# aiohttp auto-decompresses response bodies by default (auto_decompress=True).
# We disable this below so the raw body passes through unchanged and
# content-encoding forwards correctly (client gets what upstream actually sent).
# _HOP_BY_HOP_RESPONSE is the same set — content-encoding is NOT stripped,
# because with auto_decompress=False the header accurately describes the body.
_HOP_BY_HOP_RESPONSE = _HOP_BY_HOP


async def _resolve_credential_ids(host: str, toolkit_id: str | None, path: str = "/") -> list[str]:
    """Resolve host → [credential_ids] without decrypting anything.
    Used for policy checks before the vault is touched.
    Resolution is purely route-based via credential_routes.
    """
    if not toolkit_id:
        return []
    return await vault.get_credential_ids_for_route(toolkit_id, host, path)


async def _find_credential_for_host(
    host: str,
    path: str,
    toolkit_id: str | None,
    alias: str | None,
    service: str | None = None,
    toolkit_ids: list[str] | None = None,
) -> tuple[dict[str, str], str | None, str | None, bool]:
    """
    Return (headers_to_inject, api_id, credential_id, is_ambiguous) for the given upstream host.

    credential_id is the ID of the first credential used for injection — used by the
    caller to enforce per-credential policy rules.
    api_id is taken from the credential record — not resolved via the apis table.

    is_ambiguous is True when multiple credentials matched and no alias/service
    was provided to disambiguate.
    """
    _broker_log = logging.getLogger("jentic.broker")

    api_id = None  # populated from credential record below
    _broker_log.debug(
        "CRED LOOKUP: host=%r path=%r toolkit=%r toolkit_ids=%r alias=%r",
        host,
        path,
        toolkit_id,
        toolkit_ids,
        alias,
    )

    if toolkit_ids:
        creds = await vault.get_credentials_for_grants(toolkit_ids, host, path)
    elif toolkit_id:
        creds = await vault.get_credentials_for_route(toolkit_id, host, path)
    else:
        creds = []
    _broker_log.debug(
        "CRED LOOKUP: %d cred(s) via route for host=%r: %s",
        len(creds),
        host,
        [c.get("id") for c in creds],
    )

    if not creds and (toolkit_id or toolkit_ids):
        # Don't block no-auth APIs — only raise if the API spec defines security schemes.
        # If someone added an overlay with security schemes, they'd also have created
        # a credential — so creds wouldn't be empty and we'd never reach this branch.
        if await api_has_native_scheme(api_id):
            raise ValueError(
                f"No credentials found for host '{host}' (resolved api_id '{api_id}') "
                f"in toolkit '{toolkit_id}'. "
                f"Use POST /toolkits/{toolkit_id}/access-requests to request access."
            )

    is_ambiguous = False

    if alias and creds:
        matched = [c for c in creds if c.get("id") == alias]
        if matched:
            _broker_log.debug(
                "CRED LOOKUP: alias %r matched → using %r", alias, matched[0].get("id")
            )
            creds = matched
        else:
            _broker_log.warning(
                "CRED LOOKUP: alias %r not found in %s — falling back to first cred",
                alias,
                [c.get("id") for c in creds],
            )
        # If alias doesn't match any credential, fall through with all creds (best-effort)

    elif service and creds and len(creds) > 1:
        # X-Jentic-Service: select by Pipedream app_slug (e.g. "google_calendar")
        # Look up which credentials belong to accounts with this app_slug.
        async with get_db() as db:
            async with db.execute(
                "SELECT id FROM credentials WHERE id IN "
                "(SELECT broker_id || '-' || account_id || '-' || replace(api_host, '.', '-') "
                " FROM oauth_broker_accounts WHERE app_slug=?)",
                (service,),
            ) as cur:
                service_cred_ids = {r[0] for r in await cur.fetchall()}
        matched = [c for c in creds if c["id"] in service_cred_ids]
        if matched:
            _broker_log.debug("CRED LOOKUP: service %r matched %d cred(s)", service, len(matched))
            creds = matched
        else:
            # Service name doesn't match any credential for this host — fail with
            # a 409 listing available services so the agent can self-correct.
            async with get_db() as db:
                async with db.execute(
                    "SELECT DISTINCT app_slug FROM oauth_broker_accounts "
                    "WHERE api_host=? AND app_slug IS NOT NULL",
                    (host,),
                ) as cur:
                    available = [r[0] for r in await cur.fetchall()]
            raise ServiceNotFoundError(
                f"Service '{service}' not found for host '{host}'. Available services: {available}"
            )

    if len(creds) > 1 and not alias and not service:
        is_ambiguous = True
        _broker_log.warning(
            "CRED AMBIGUITY: %d credentials for host=%r — using first. "
            "Set X-Jentic-Service or X-Jentic-Credential header to disambiguate. "
            "Credential IDs: %s",
            len(creds),
            host,
            [c.get("id") for c in creds],
        )

    # api_id comes from the credential record — not resolved via the apis table.
    api_id = creds[0].get("api_id") if creds else None

    headers = {}
    first_credential_id: str | None = None
    for cred in creds:
        value = cred["value"]
        auth_type = cred.get("auth_type")
        identity = cred.get("identity")
        cred_scheme = cred.get("scheme")  # pre-computed blob from migration 0007 / store_credential

        if not first_credential_id:
            first_credential_id = cred["id"]

        # No-auth credential: exists only for server_variables routing — skip injection.
        if auth_type == "none":
            continue

        # Fast path: use the pre-computed scheme blob if available.
        # This is the canonical path after migration 0007 — no spec lookup needed.
        if cred_scheme:
            # Compound scheme: {"secret": {"in":...,"name":...}, "identity": {"in":...,"name":...}}
            if "secret" in cred_scheme:
                s = cred_scheme["secret"]
                s_pfx = s.get("prefix", "")
                if s.get("in") == "header":
                    headers[s["name"]] = f"{s_pfx}{value}"
                if "identity" in cred_scheme and identity:
                    si = cred_scheme["identity"]
                    if si.get("in") == "header":
                        headers[si["name"]] = identity
                continue

            s_in = cred_scheme.get("in")
            s_name = cred_scheme.get("name", "Authorization")
            s_pfx = cred_scheme.get("prefix", "")
            s_enc = cred_scheme.get("encode")
            if s_in == "header":
                if s_enc == "base64":
                    if identity:
                        _raw = f"{identity}:{value}"
                    elif ":" in value:
                        _raw = value
                    else:
                        _raw = f"token:{value}"
                    headers[s_name] = f"{s_pfx}{base64.b64encode(_raw.encode()).decode()}"
                else:
                    headers[s_name] = f"{s_pfx}{value}"
                # Compound scheme: also inject identity if a second scheme entry is present
                if cred_scheme.get("identity_name") and identity:
                    headers[cred_scheme["identity_name"]] = identity
            elif s_in == "query":
                _broker_log.warning(
                    "CRED INJECT: credential %r uses apiKey in query param (%s) — not yet supported, skipping injection",
                    cred.get("id"),
                    s_name,
                )
            continue

        # No pre-computed scheme blob — minimal no-spec fallback.
        # All credentials created since migration 0005 have a scheme blob; this path
        # is only reachable for manually-inserted or very old credentials.
        logging.getLogger("jentic.broker").warning(
            "CRED INJECT: credential %r has no scheme blob — attempting auth_type fallback",
            cred.get("id"),
        )
        if auth_type == "bearer" or auth_type == "oauth2":
            headers["Authorization"] = f"Bearer {value}"
        elif auth_type == "basic":
            if ":" in value:
                _raw = value
            elif identity:
                _raw = f"{identity}:{value}"
            else:
                _raw = f"token:{value}"
            headers["Authorization"] = f"Basic {base64.b64encode(_raw.encode()).decode()}"
        else:
            # apiKey and others require a header name — can't inject without scheme blob.
            logging.getLogger("jentic.broker").error(
                "CRED INJECT: credential %r auth_type=%r has no scheme blob and no spec fallback — skipping injection",
                cred.get("id"),
                auth_type,
            )

    _broker_log.debug(
        "CRED INJECT: api_id=%r injecting headers=%s using cred=%r ambiguous=%s",
        api_id,
        list(headers.keys()),
        first_credential_id,
        is_ambiguous,
    )
    return headers, api_id, first_credential_id, is_ambiguous


async def _find_pipedream_credential_for_host(
    host: str,
    path: str,
    toolkit_id: str | None,
    alias: str | None = None,
) -> tuple[str | None, str | None]:
    """Return (account_id, credential_id) for a Pipedream-managed credential in this toolkit.

    Pipedream credentials have auth_type='pipedream_oauth' and their encrypted value
    IS the Pipedream account_id (apn_xxx). This bypasses the apis table lookup —
    Pipedream-connected APIs may not have a spec in the local catalog.

    Uses longest-prefix matching: the credential whose api_id is the longest prefix
    of (host + path) wins. This correctly disambiguates googleapis.com/calendar from
    googleapis.com/gmail when both are provisioned.

    If alias is specified, only the credential with that ID is considered.
    Returns (None, None) if no Pipedream credential is provisioned for this host+toolkit.
    """
    if not toolkit_id:
        return None, None
    full_path = host + path  # e.g. "googleapis.com/calendar/v3/calendars/primary"
    async with get_db() as db:
        if alias:
            # Caller specified an exact credential — use it directly if it's Pipedream
            async with db.execute(
                "SELECT id, encrypted_value FROM credentials "
                "WHERE id=? AND auth_type='pipedream_oauth'",
                (alias,),
            ) as cur:
                row = await cur.fetchone()
        elif toolkit_id == DEFAULT_TOOLKIT_ID:
            # Longest-prefix match: find the credential whose api_id is a prefix of host+path
            async with db.execute(
                "SELECT id, encrypted_value FROM credentials "
                "WHERE ? LIKE (api_id || '%') AND auth_type='pipedream_oauth' "
                "ORDER BY length(api_id) DESC LIMIT 1",
                (full_path,),
            ) as cur:
                row = await cur.fetchone()
        else:
            async with db.execute(
                """SELECT c.id, c.encrypted_value FROM credentials c
                   JOIN toolkit_credentials tc ON tc.credential_id = c.id
                   WHERE tc.toolkit_id=? AND ? LIKE (c.api_id || '%')
                   AND c.auth_type='pipedream_oauth'
                   ORDER BY length(c.api_id) DESC LIMIT 1""",
                (toolkit_id, full_path),
            ) as cur:
                row = await cur.fetchone()
    if not row:
        return None, None
    return vault.decrypt(row[1]), row[0]


def _is_broker_path(path: str) -> bool:
    """True if the path looks like an upstream host prefix (contains a dot)."""
    if not path or path == "/":
        return False
    first_segment = path.lstrip("/").split("/")[0]
    return "." in first_segment and not first_segment.startswith(".")


# Custom Starlette path convertor so the broker catch-all only matches paths
# whose first segment looks like a hostname (contains a dot, e.g. api.stripe.com).
# This means UI routes like /search or /catalog never reach the broker at all —
# they are handled by earlier registered routes or the SPA catch-all in main.py.
from starlette.convertors import CONVERTOR_TYPES, Convertor  # noqa: E402


class _BrokerHostConvertor(Convertor):
    # First segment must contain a dot, not start with one, and have at least one char before the dot.
    # Rejects: .well-known/..., /@vite/..., empty first segment
    # Matches: api.stripe.com/v1/customers, httpbin.org/get
    regex = r"[^/.][^/]*\.[^/.][^/]*(?:/.*)?$"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


CONVERTOR_TYPES["brokerhost"] = _BrokerHostConvertor()


_BROKER_DESCRIPTION = (
    "Routes any HTTP request to the upstream API, injecting credentials automatically.\n\n"
    "URL shape: `/{upstream_host}/{path}` — e.g. `/api.stripe.com/v1/customers`\n\n"
    "All HTTP methods supported; Swagger UI shows GET as representative.\n\n"
    "**Headers:**\n"
    "- `X-Jentic-Simulate: true` — validate and preview the call without sending it\n"
    "- `X-Jentic-Credential: {alias}` — select a specific credential when multiple exist for an API\n"
    "- `X-Jentic-Service: {app_slug}` — select by service name (e.g. `google_calendar`, `gmail`) when multiple credentials share a host\n"
    "- `X-Jentic-Dry-Run: true` — alias for Simulate (deprecated)\n\n"
    "Returns upstream response verbatim plus `X-Jentic-Execution-Id` for trace correlation."
)

_BROKER_RESPONSES = {
    200: {
        "description": "Upstream response proxied verbatim. Content-Type matches upstream.",
        "content": {
            "application/json": {"schema": {}},
            "text/html": {},
            "text/plain": {},
        },
    },
    202: {
        "description": "Async job created (RFC 7240). Poll via Location header or GET /jobs/{job_id}"
    },
    400: {"description": "Bad request (upstream or Jentic validation)"},
    401: {"description": "Missing or rejected credential"},
    403: {"description": "Policy denied or upstream forbidden"},
    404: {"description": "Upstream resource not found"},
    502: {"description": "Upstream unreachable"},
}


# ── Documentation stub ────────────────────────────────────────────────────────
# Swagger UI breaks when multiple HTTP methods share the same catch-all path
# (it collapses them and uses the wrong verb in "Try it out"). We hide the real
# multi-method handler from the schema and expose a single GET stub for docs.
# The real handler below is include_in_schema=False.


@router.get(
    "/{target:brokerhost}",
    include_in_schema=True,
    tags=["execute"],
    summary="Broker — proxy a call to any registered API with automatic credential injection",
    description=_BROKER_DESCRIPTION,
    responses=_BROKER_RESPONSES,
    operation_id="broker_get",
    openapi_extra=agent_hints(
        when_to_use="Use this after you've inspected an operation via GET /inspect/{id} and are ready to execute it. The broker automatically injects credentials from the toolkit's vault, enforces access policies, and traces all calls. URL format: /{upstream_host}/{path} (e.g., /api.stripe.com/v1/payment_intents). Supports all HTTP methods (GET, POST, PUT, PATCH, DELETE).",
        prerequisites=[
            "Requires authentication (toolkit key or human session)",
            "Requires registered API with credentials (use POST /credentials to add)",
            "Requires credential bound to toolkit (use POST /toolkits/{id}/credentials with {credential_id} in body)",
            "Toolkit credential must allow the operation (governed by access policy)",
        ],
        avoid_when="Do not use for workflows — use POST /workflows/{slug} instead. Do not use for Jentic internal endpoints — use direct paths like /apis, /search.",
        related_operations=[
            "GET /inspect/{id} — inspect operation before calling to see parameters and auth requirements",
            "GET /traces/{id} — view execution trace after broker call (use X-Jentic-Execution-Id header)",
            "GET /jobs/{id} — poll async job status when Prefer: wait=0 header is used",
            "POST /credentials — add credentials before calling",
            "POST /toolkits/{id}/credentials — bind credentials to toolkit (body: {credential_id})",
        ],
    ),
)
async def broker_doc_stub(
    request: Request,
    target: Annotated[
        str,
        Path(description="Upstream API path (format: host.domain/path, e.g. api.github.com/repos)"),
    ],
):
    """Documentation stub — delegates to the real broker handler."""
    return await broker(request, target)


@router.api_route(
    "/{target:brokerhost}",
    methods=["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def broker(request: Request, target: str):
    """
    Catch-all broker route. Fires only for paths that look like upstream hosts
    (first segment contains a dot). All Jentic-internal routes are registered first
    and take priority.
    """
    if not _is_broker_path("/" + target):
        raise HTTPException(404, "Not found")

    # URL-decode the target — Swagger UI encodes slashes as %2F, so
    # /%2Ftechpreneurs.ie%2Flatest.json arrives as %2Ftechpreneurs.ie%2Flatest.json.
    # Decode and strip any leading slash so both forms work identically.
    target = unquote(target).lstrip("/")

    # Parse upstream host and path from target
    parts = target.split("/", 1)
    upstream_host = parts[0]
    upstream_path = "/" + parts[1] if len(parts) > 1 else "/"

    # ── Workflow dispatch ─────────────────────────────────────────────────────
    # If the target host is the Jentic host itself and path is /workflows/{slug},
    # route to the arazzo orchestrator internally instead of making an HTTP call.
    # Also detect self-referential calls via the request's Host header
    # (handles cases where the container doesn't have env vars set)
    _request_host = request.headers.get("host", "").split(":")[0]
    _is_self = (
        upstream_host == JENTIC_PUBLIC_HOSTNAME
        or upstream_host == _request_host
        or upstream_host in ("localhost", "127.0.0.1", "0.0.0.0")
    )
    if _is_self and upstream_path.startswith("/workflows/"):
        slug = upstream_path.split("/workflows/", 1)[1].split("/")[0]
        if slug:
            body_bytes_wf = await request.body()
            caller_key = request.headers.get("x-jentic-api-key") or ""
            _auth_wf = request.headers.get("Authorization", "")
            caller_bearer_wf = None
            if _auth_wf.lower().startswith("bearer "):
                _t = _auth_wf[7:].strip()
                if _t.startswith("at_"):
                    caller_bearer_wf = _t
            toolkit_id_wf = getattr(request.state, "toolkit_id", None)
            simulate_wf = (
                getattr(request.state, "simulate", False)
                or request.headers.get("x-jentic-simulate", "").lower() == "true"
            )
            prefer_wait_wf = parse_prefer_wait(request.headers.get("prefer"))
            callback_url_wf = request.headers.get("x-jentic-callback")
            return await dispatch_workflow(
                slug=slug,
                body_bytes=body_bytes_wf,
                caller_api_key=caller_key,
                toolkit_id=toolkit_id_wf,
                simulate=simulate_wf,
                prefer_wait=prefer_wait_wf,
                callback_url=callback_url_wf,
                agent_id=getattr(request.state, "agent_client_id", None),
                caller_bearer_token=caller_bearer_wf,
            )

    execution_id = new_trace_id()
    started_at = time.time()

    # toolkit_id is None for unauthenticated (anonymous) requests —
    # credential injection and policy checks are skipped in that case.
    toolkit_id: str | None = getattr(request.state, "toolkit_id", None)
    grant_ids: list[str] | None = getattr(request.state, "granted_toolkit_ids", None)
    agent_cid: str | None = getattr(request.state, "agent_client_id", None)
    suspended_ids: list[str] | None = getattr(request.state, "suspended_toolkit_ids", None)

    is_simulate = (
        getattr(request.state, "simulate", False)
        or request.headers.get("x-jentic-simulate", "").lower() == "true"
    )

    # Cross-process workflow attribution. The arazzo-runner subprocess (spawned
    # by workflows.execute_workflow_core) sets X-Jentic-Parent-Trace to the
    # workflow's own trace id on every broker hop, so we can stamp child broker
    # traces with parent_trace_id and render "part of workflow X" in the UI.
    #
    # Loopback-only on purpose: the runner connects via http://localhost:{port}
    # so we trust the header from there. External callers can't spoof workflow
    # parentage.
    parent_trace_id: str | None = None
    raw_parent = request.headers.get("X-Jentic-Parent-Trace")
    if raw_parent:
        client_host = request.client.host if request.client else None
        if client_host in ("127.0.0.1", "::1", "localhost"):
            parent_trace_id = raw_parent

    # Filled in by the async dispatch branches below when this broker call
    # gets wrapped in a job. _write_trace closes over this name, so updates
    # made before each exit point flow through.
    current_job_id: str | None = None

    # Catalog-form `apis.id` for the upstream API. Filled in after the
    # credential lookup runs (see `_find_credential_for_host` below); stays
    # None for the early-error exit points that fail before credentials are
    # resolved (e.g. policy denials, malformed targets) and for anonymous
    # broker calls where no credential matched. The read-side LEFT JOIN
    # treats NULL as "unattributed", same as toolkit_id/agent_id.
    current_api_id: str | None = None

    # Helper to write broker traces (reduces duplication across 10+ call sites)
    async def _write_trace(
        status: str,
        http_status: int,
        error: str | None = None,
        *,
        toolkit_id_override: str | None = None,
    ) -> None:
        """Write trace for this broker call. All broker exit points should call this.

        ``toolkit_id_override`` lets denial paths attribute the trace to a
        specific (e.g. suspended) toolkit even when the request-level
        ``toolkit_id`` is None — see the agent all-grants-suspended path where
        the killed toolkit id is known from ``suspended_ids`` (P2-8).
        """
        if not is_simulate:
            await safe_write_trace(
                trace_id=execution_id,
                toolkit_id=toolkit_id_override or toolkit_id,
                agent_id=agent_cid,
                operation_id=f"{request.method}/{upstream_host}{upstream_path}",
                workflow_id=None,
                spec_path=None,
                status=status,
                http_status=http_status,
                duration_ms=int((time.time() - started_at) * 1000),
                error=error,
                step_outputs=None,
                job_id=current_job_id,
                parent_trace_id=parent_trace_id,
                api_id=current_api_id,
            )

    credential_alias = request.headers.get("x-jentic-credential")
    credential_service = request.headers.get("x-jentic-service")
    callback_url = request.headers.get("x-jentic-callback")
    if callback_url and not is_http_https_url(callback_url):
        raise HTTPException(400, "X-Jentic-Callback must be an http or https URL")

    if agent_cid is not None and grant_ids is not None and len(grant_ids) == 0:
        # Distinguish two empty-grant cases so the error message is truthful:
        #   1. The agent's grants ALL point at suspended (killed) toolkits — the
        #      grants exist, the toolkits were disabled. Report toolkit_suspended.
        #   2. The agent genuinely has no grants — an admin must add one.
        if suspended_ids:
            _tid = suspended_ids[0]
            await _write_trace(
                "toolkit_suspended",
                403,
                f"Toolkit '{_tid}' suspended",
                toolkit_id_override=_tid,
            )
            return Response(
                content=json.dumps(
                    {
                        "error": "toolkit_suspended",
                        "message": f"Toolkit '{_tid}' has been suspended. All API access is blocked. Contact the toolkit owner to restore access.",
                        "toolkit_id": _tid,
                    }
                ),
                status_code=403,
                media_type="application/json",
                headers={"X-Jentic-Error": "true", "X-Jentic-Execution-Id": execution_id},
            )
        await _write_trace("policy_denied", 403, "Agent has no toolkit grants")
        return Response(
            content=json.dumps(
                {
                    "error": "policy_denied",
                    "message": "This agent is approved but has no toolkit grants. "
                    "An admin must POST /agents/{id}/grants with a toolkit_id.",
                    "agent_id": agent_cid,
                }
            ),
            status_code=403,
            media_type="application/json",
            headers={"X-Jentic-Error": "true", "X-Jentic-Execution-Id": execution_id},
        )

    # ── Killswitch: reject if the bound toolkit is suspended ───────────────────
    # Agent path: `auth.py` already filtered disabled toolkits out of `grant_ids`
    # (and surfaced them via `suspended_ids`, handled above), so re-checking each
    # grant here is a redundant per-iteration DB round-trip. Only the toolkit-key
    # (`tk_`) path — a single `toolkit_id` with no grants — still needs a check.
    _kits_to_check = [] if grant_ids else ([toolkit_id] if toolkit_id else [])
    for _tid in _kits_to_check:
        async with get_db() as _ks_db:
            async with _ks_db.execute(
                "SELECT disabled FROM toolkits WHERE id=?", (_tid,)
            ) as _ks_cur:
                _ks_row = await _ks_cur.fetchone()
        if _ks_row and _ks_row[0]:
            await _write_trace("toolkit_suspended", 403, f"Toolkit '{_tid}' suspended")
            return Response(
                content=json.dumps(
                    {
                        "error": "toolkit_suspended",
                        "message": f"Toolkit '{_tid}' has been suspended. All API access is blocked. Contact the toolkit owner to restore access.",
                        "toolkit_id": _tid,
                    }
                ),
                status_code=403,
                media_type="application/json",
                headers={"X-Jentic-Error": "true", "X-Jentic-Execution-Id": execution_id},
            )

    # ── Prefer: wait=N for single broker calls ────────────────────────────────
    # Parsed here and threaded through to the async path below if the upstream
    # call takes too long.
    prefer_wait = parse_prefer_wait(request.headers.get("prefer"))

    # ── Resolve credential IDs (no decryption) → policy check ────────────────
    # We resolve the api_id and credential IDs first — without decrypting —
    # so policy can be enforced before the vault is ever touched.
    # Denied requests never decrypt a credential.
    #
    # X-Jentic-Credential is a HARD OVERRIDE: when the caller names a specific
    # credential, policy is checked against THAT credential — not whatever the
    # host-matching heuristic would pick. This is critical for multi-service hosts
    # (e.g. googleapis.com) where auto-selection would otherwise target the wrong
    # credential (e.g. Calendar when the caller wants Gmail), causing spurious 403s
    # with a misleading credential_id in the error body.
    _resolved_cred_ids: list[str] = []
    # Per-credential origin toolkit. For OAuth agents with multiple grants this
    # records which grant matched each credential, so policy errors can blame the
    # right toolkit instead of an arbitrary first-grant choice.
    _cred_source_toolkit: dict[str, str] = {}

    if credential_alias and (toolkit_id or grant_ids):
        # X-Jentic-Credential is a disambiguator, not a bypass — resolve by route
        # first, then verify the named credential is among the matches.
        if grant_ids:
            pairs = await vault.get_credential_ids_with_toolkit_for_grants(
                grant_ids, upstream_host, upstream_path
            )
            _cred_source_toolkit = {cid: tid for cid, tid in pairs}
            _resolved_cred_ids = [cid for cid, _ in pairs]
        else:
            _resolved_cred_ids = await _resolve_credential_ids(
                host=upstream_host, toolkit_id=toolkit_id, path=upstream_path
            )
        if credential_alias in _resolved_cred_ids:
            _resolved_cred_ids = [credential_alias]
        elif _resolved_cred_ids:
            # Alias doesn't match any route-resolved credential for this host —
            # log warning but proceed with route-matched creds (best-effort).
            log.warning(
                "X-Jentic-Credential=%r not found among route-matched credentials %s for host=%r — ignoring alias",
                credential_alias,
                _resolved_cred_ids,
                upstream_host,
            )
        # If _resolved_cred_ids is empty, fall through to the fail-closed check below.
    elif toolkit_id or grant_ids:
        try:
            if grant_ids:
                pairs = await vault.get_credential_ids_with_toolkit_for_grants(
                    grant_ids, upstream_host, upstream_path
                )
                _cred_source_toolkit = {cid: tid for cid, tid in pairs}
                _resolved_cred_ids = [cid for cid, _ in pairs]
            else:
                _resolved_cred_ids = await _resolve_credential_ids(
                    host=upstream_host, toolkit_id=toolkit_id, path=upstream_path
                )
        except Exception:
            # Fail closed: if we can't resolve credentials for policy checking,
            # don't proceed to credential injection — deny the request.
            # Only applies to authenticated requests; anonymous passthrough
            # skips credential resolution entirely.
            log.exception(
                "Credential resolution failed for %r (toolkit=%s grant_ids=%s)",
                upstream_host,
                toolkit_id,
                grant_ids,
            )
            await _write_trace("error", 500, f"Credential resolution failed for {upstream_host}")
            return Response(
                content=json.dumps(
                    {
                        "error": "CREDENTIAL_RESOLUTION_FAILED",
                        "message": f"Could not resolve credentials for '{upstream_host}'. Request denied (fail-closed).",
                    }
                ),
                status_code=500,
                media_type="application/json",
                headers={"X-Jentic-Error": "true", "X-Jentic-Execution-Id": execution_id},
            )

    if (toolkit_id or grant_ids) and not _resolved_cred_ids:
        # Fail closed: an authenticated request with a toolkit_id must resolve
        # to at least one credential for the target host. If none can be found,
        # deny immediately — never fall through to unenforced injection.
        await _write_trace("policy_denied", 403, f"No credential found for '{upstream_host}'")
        # Multi-grant calls can't attribute the miss to a specific toolkit,
        # so toolkit_id is omitted in that case to avoid a misleading remediation
        # link. Single-toolkit (tk_) callers still get their toolkit echoed back.
        error_body: dict = {
            "error": "policy_denied",
            "message": f"No credential configured for '{upstream_host}'. Request denied.",
            "remediation": "Add a credential for this host via the Jentic Mini UI.",
        }
        if toolkit_id and not grant_ids:
            error_body["toolkit_id"] = toolkit_id
        return Response(
            content=json.dumps(error_body),
            status_code=403,
            media_type="application/json",
            headers={"X-Jentic-Error": "true", "X-Jentic-Execution-Id": execution_id},
        )

    if (toolkit_id or grant_ids) and _resolved_cred_ids:
        # Check against the first matched credential (primary).
        # When credential_alias is set this is always the aliased credential.
        primary_cred_id = _resolved_cred_ids[0]
        # Attribute policy errors to the toolkit that actually surfaced this
        # credential. For single-toolkit (tk_) callers this is just toolkit_id;
        # for multi-grant OAuth agents it's whichever grant matched.
        primary_source_toolkit = _cred_source_toolkit[primary_cred_id] if grant_ids else toolkit_id
        try:
            allowed, reason = await check_credential_policy(
                credential_id=primary_cred_id,
                operation_id=f"{request.method}/{upstream_host}{upstream_path}",
                method=request.method,
                path=upstream_path,
            )
            if not allowed:
                await _write_trace("policy_denied", 403, f"Policy denied: {reason}")
                error_body = {
                    "error": "policy_denied",
                    "message": f"{request.method} {upstream_host}{upstream_path} denied by credential policy. {reason}",
                    "credential_id": primary_cred_id,
                    "toolkit_id": primary_source_toolkit,
                    "remediation": f"POST /toolkits/{primary_source_toolkit}/access-requests to request expanded permissions.",
                }
                return Response(
                    content=json.dumps(error_body),
                    status_code=403,
                    media_type="application/json",
                    headers={"X-Jentic-Error": "true", "X-Jentic-Execution-Id": execution_id},
                )
        except Exception:
            # Fail closed: if the policy check itself errors, deny the request
            # rather than allowing it through unchecked.
            log.exception(
                "Policy check failed for %s %r %r (cred=%s)",
                request.method,
                upstream_host,
                upstream_path,
                primary_cred_id,
            )
            await _write_trace(
                "error",
                403,
                f"Policy check failed for {request.method} {upstream_host}{upstream_path} (credential {primary_cred_id})",
            )
            return Response(
                content=json.dumps(
                    {
                        "error": "POLICY_CHECK_FAILED",
                        "message": f"Policy evaluation failed for credential '{primary_cred_id}'. Request denied (fail-closed).",
                        "credential_id": primary_cred_id,
                        "toolkit_id": toolkit_id,
                    }
                ),
                status_code=403,
                media_type="application/json",
                headers={"X-Jentic-Error": "true", "X-Jentic-Execution-Id": execution_id},
            )

    # body_bytes initialised here so the OAuthBroker fallback can read it
    # without a double-read; the main forward path reads it again below if empty.
    body_bytes: bytes = b""

    # ── Full credential lookup (with decryption) ──────────────────────────────
    try:
        (
            inject_headers,
            api_id,
            credential_id,
            credential_ambiguous,
        ) = await _find_credential_for_host(
            host=upstream_host,
            path=upstream_path,
            toolkit_id=toolkit_id,
            alias=credential_alias,
            service=credential_service,
            toolkit_ids=grant_ids,
        )
        # Stamp the catalog-form api id onto the trace so the Monitor surfaces
        # can JOIN apis at read time. _find_credential_for_host returns the
        # `api_id` taken straight from the matched credential record, which
        # for catalog imports is the canonical SLD form (e.g. `stripe.com`).
        # Stays None when no credential matched (anonymous calls / no-auth APIs).
        current_api_id = api_id
    except ServiceNotFoundError as e:
        await _write_trace("error", 409, str(e))
        return Response(
            content=json.dumps({"error": "SERVICE_NOT_FOUND", "message": str(e)}),
            status_code=409,
            media_type="application/json",
            headers={"X-Jentic-Error": "true", "X-Jentic-Execution-Id": execution_id},
        )
    except Exception:
        log.exception("Credential lookup failed")
        await _write_trace("error", 500, "Credential lookup failed")
        error_body = {
            "error": "CREDENTIAL_LOOKUP_FAILED",
            "message": "Internal error during credential lookup.",
        }
        return Response(
            content=json.dumps(error_body),
            status_code=500,
            media_type="application/json",
            headers={"X-Jentic-Error": "true", "X-Jentic-Execution-Id": execution_id},
        )

    # Credential-related headers — included on all responses (success and error)
    _cred_headers: dict[str, str] = {}
    if credential_id:
        _cred_headers["X-Jentic-Credential-Used"] = credential_id
    if credential_ambiguous:
        _cred_headers["X-Jentic-Credential-Ambiguous"] = "true"
        await _write_trace("error", 409, "Ambiguous credential")
        return Response(
            content=json.dumps(
                {
                    "error": "CREDENTIAL_AMBIGUOUS",
                    "message": (
                        f"Multiple credentials match host '{upstream_host}'. "
                        "Use X-Jentic-Credential to specify which one, "
                        "or X-Jentic-Service to select by service name."
                    ),
                }
            ),
            status_code=409,
            media_type="application/json",
            headers={
                "X-Jentic-Error": "true",
                "X-Jentic-Execution-Id": execution_id,
                **_cred_headers,
            },
        )

    # ── Routing host ──────────────────────────────────────────────────────────
    # upstream_host is the host the caller addressed in the broker URL.
    # For .local self-hosted APIs, the upstream_host is a semantic routing key
    # (e.g. 'bedroom.go2rtc.local') — not a real DNS name. Resolve the actual
    # upstream from the matched credential's server_variables.
    routing_host = upstream_host
    _resolved_scheme = None  # populated by .local server_variables resolution
    if upstream_host.endswith(".local"):
        # Find the credential's server_variables and resolve via the API's
        # confirmed overlay server URL template.
        _local_cred = None
        if inject_headers is not None:
            # Try to get the credential record that produced inject_headers
            if credential_id:
                _local_cred = await vault.get_credential(credential_id)
        if _local_cred is None and credential_id:
            _local_cred = await vault.get_credential(credential_id)
        if _local_cred:
            _sv = _local_cred.get("server_variables") or {}
            _cred_api_id = _local_cred.get("api_id")
            if _sv:
                _resolved = await vault._resolve_server_url(_cred_api_id, _sv)
                if _resolved:
                    _p = urlparse(_resolved)
                    routing_host = _p.netloc or routing_host
                    _resolved_scheme = _p.scheme
                    log.debug(
                        "broker: .local route %r resolved to upstream %r (scheme=%s) via server_variables",
                        upstream_host,
                        routing_host,
                        _resolved_scheme,
                    )
                else:
                    log.warning(
                        "broker: .local route %r — could not resolve upstream from server_variables %r",
                        upstream_host,
                        _sv,
                    )
            elif _sv is not None:
                log.warning(
                    "broker: .local route %r — credential %r has no server_variables; cannot resolve upstream",
                    upstream_host,
                    credential_id,
                )

    # ── Pipedream credential path ─────────────────────────────────────────────
    # If the vault lookup yielded no headers, check for an explicitly-provisioned
    # Pipedream credential (auth_type='pipedream_oauth'). This path requires:
    #   1. POST /oauth-brokers/{id}/sync  — creates the credential in the vault
    #   2. POST /toolkits/{id}/credentials — explicitly provisions it to this toolkit
    # No implicit fallback. If no credential is provisioned, we fall through to
    # unauthenticated forwarding (or the request will fail upstream with 401).
    if not inject_headers:
        pd_account_id, pd_cred_id = None, None
        # Track which granted toolkit surfaced the credential — used in policy
        # error remediation so multi-grant OAuth agents get pointed at the right
        # /toolkits/{id}/access-requests endpoint.
        pd_source_toolkit: str | None = None
        if grant_ids:
            for tid in grant_ids:
                a, c = await _find_pipedream_credential_for_host(
                    upstream_host, upstream_path, tid, alias=credential_alias
                )
                if a and c:
                    pd_account_id, pd_cred_id, pd_source_toolkit = a, c, tid
                    break
        else:
            pd_account_id, pd_cred_id = await _find_pipedream_credential_for_host(
                upstream_host, upstream_path, toolkit_id, alias=credential_alias
            )
            pd_source_toolkit = toolkit_id
        if pd_account_id and pd_cred_id:
            # Policy check for this Pipedream credential (same gate as vault path)
            if toolkit_id or grant_ids:
                try:
                    allowed, reason = await check_credential_policy(
                        credential_id=pd_cred_id,
                        operation_id=f"{request.method}/{upstream_host}{upstream_path}",
                        method=request.method,
                        path=upstream_path,
                    )
                    if not allowed:
                        await _write_trace("policy_denied", 403, f"Policy denied: {reason}")
                        error_body = {
                            "error": "policy_denied",
                            "message": f"{request.method} {upstream_host}{upstream_path} denied. {reason}",
                            "credential_id": pd_cred_id,
                            "toolkit_id": pd_source_toolkit,
                            "remediation": f"POST /toolkits/{pd_source_toolkit}/access-requests",
                        }
                        return Response(
                            content=json.dumps(error_body),
                            status_code=403,
                            media_type="application/json",
                            headers={
                                "X-Jentic-Error": "true",
                                "X-Jentic-Execution-Id": execution_id,
                            },
                        )
                except Exception:
                    log.exception(
                        "Pipedream policy check failed for %s %r %r (cred=%s)",
                        request.method,
                        upstream_host,
                        upstream_path,
                        pd_cred_id,
                    )
                    await _write_trace(
                        "error",
                        403,
                        f"Policy check failed for {request.method} {upstream_host}{upstream_path} (credential {pd_cred_id})",
                    )
                    return Response(
                        content=json.dumps(
                            {
                                "error": "POLICY_CHECK_FAILED",
                                "message": f"Policy evaluation failed for credential '{pd_cred_id}'. Request denied (fail-closed).",
                                "credential_id": pd_cred_id,
                                "toolkit_id": toolkit_id,
                            }
                        ),
                        status_code=403,
                        media_type="application/json",
                        headers={"X-Jentic-Error": "true", "X-Jentic-Execution-Id": execution_id},
                    )

            # Find the Pipedream broker instance and proxy using the credential's account_id
            # Always use the external_user_id stored against this account in the DB —
            # never trust the caller-supplied header, which may differ (e.g. sdk sends
            # michael@jentic.com but the account was registered under "default")
            _ext_user = "default"
            async with get_db() as _eudb:
                async with _eudb.execute(
                    "SELECT external_user_id FROM oauth_broker_accounts WHERE account_id=? LIMIT 1",
                    (pd_account_id,),
                ) as _eucur:
                    _eurow = await _eucur.fetchone()
                    if _eurow:
                        _ext_user = _eurow[0]
            _pd_broker = None
            for _b in registry.brokers:
                if hasattr(_b, "proxy_request_with_account"):
                    _pd_broker = _b
                    break

            if _pd_broker is not None:
                if not body_bytes:
                    body_bytes = await request.body()
                _fwd_hdrs = {
                    k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP
                }
                _pd_resp = await _pd_broker.proxy_request_with_account(
                    account_id=pd_account_id,
                    api_host=routing_host,
                    upstream_path=upstream_path,
                    method=request.method,
                    headers=_fwd_hdrs,
                    body=body_bytes,
                    query_string=request.url.query,
                    external_user_id=_ext_user,
                )
                if _pd_resp is not None:
                    # Write trace for Pipedream proxy call
                    trace_status = "success" if _pd_resp.status_code < 400 else "error"
                    await _write_trace(trace_status, _pd_resp.status_code)
                    if _pd_resp.status_code < 400:
                        await vault.mark_credential_used(pd_cred_id)
                    # Track OAuth account health: 401/403 = broken grant; <400 = healthy.
                    # Powers the red/green StatusDot in the credentials list and the
                    # inline Reconnect affordance on the workspace API detail page.
                    if _pd_resp.status_code in (401, 403):
                        try:
                            async with get_db() as _hdb:
                                await _hdb.execute(
                                    "UPDATE oauth_broker_accounts SET healthy=0 "
                                    "WHERE account_id=? AND api_host=?",
                                    (pd_account_id, routing_host),
                                )
                                await _hdb.commit()
                        except Exception:
                            log.debug(
                                "Pipedream health write (unhealthy) failed for account %s host %s",
                                pd_account_id,
                                routing_host,
                                exc_info=True,
                            )
                    elif _pd_resp.status_code < 400:
                        try:
                            async with get_db() as _hdb:
                                await _hdb.execute(
                                    "UPDATE oauth_broker_accounts SET healthy=1 "
                                    "WHERE account_id=? AND api_host=? AND healthy IS NOT 1",
                                    (pd_account_id, routing_host),
                                )
                                await _hdb.commit()
                        except Exception:
                            log.debug(
                                "Pipedream health write (healthy) failed for account %s host %s",
                                pd_account_id,
                                routing_host,
                                exc_info=True,
                            )
                    _pd_resp_headers = {
                        k: v
                        for k, v in _pd_resp.headers.items()
                        if k.lower() not in _HOP_BY_HOP_RESPONSE
                    }
                    _pd_resp_headers["X-Jentic-Execution-Id"] = execution_id
                    _pd_resp_headers["X-Jentic-OAuth-Broker"] = "pipedream"
                    _pd_resp_headers["X-Jentic-Credential-Id"] = pd_cred_id
                    return Response(
                        content=_pd_resp.content,
                        status_code=_pd_resp.status_code,
                        headers=_pd_resp_headers,
                        media_type=_pd_resp.headers.get("content-type"),
                    )

    # ── Build upstream URL ────────────────────────────────────────────────────
    _internal_port = int(os.environ.get("JENTIC_INTERNAL_PORT", "8900"))
    # Disable TLS verification for private/local addresses (self-signed certs)
    _routing_host_bare = routing_host.split(":")[0]
    _routing_host_port = int(routing_host.split(":")[1]) if ":" in routing_host else None
    _is_private_host = (
        _routing_host_bare in ("localhost", "127.0.0.1")
        or _routing_host_bare.startswith("10.")
        or _routing_host_bare.startswith("192.168.")
        or bool(re.match(r"172\.(1[6-9]|2[0-9]|3[0-1])\.", _routing_host_bare))
        or _routing_host_bare.endswith(
            ".local"
        )  # self-hosted semantic routes never resolve via DNS
    )
    _ssl_verify = not _is_private_host
    # Scheme selection priority:
    # 1. If server_variables resolution provided an explicit scheme, use it
    # 2. Public hosts → always HTTPS
    # 3. Private hosts → HTTPS only on standard TLS ports (443, 8443, 9443)
    _SSL_PORTS = {443, 8443, 9443}
    if _resolved_scheme:
        _use_https = _resolved_scheme == "https"
    else:
        _use_https = not _is_private_host or (_routing_host_port in _SSL_PORTS)
    if _is_self:
        upstream_url = f"http://localhost:{_internal_port}{upstream_path}"
    else:
        _scheme = "https" if _use_https else "http"
        upstream_url = f"{_scheme}://{routing_host}{upstream_path}"
    if request.url.query:
        upstream_url += f"?{request.url.query}"

    # ── Simulate: return what would be sent ──────────────────────────────────
    if is_simulate:
        body_bytes = await request.body()
        forward_headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
        forward_headers.update(inject_headers)
        would_send = {
            "method": request.method,
            "url": upstream_url,
            "headers": {
                k: ("***" if k.lower() == "authorization" else v)
                for k, v in forward_headers.items()
            },
        }
        if body_bytes:
            try:
                would_send["body"] = json.loads(body_bytes)
            except Exception:
                would_send["body"] = body_bytes.decode("utf-8", errors="replace")

        return Response(
            content=json.dumps(
                {
                    "simulate": True,
                    "synthesised": False,
                    "valid": True,
                    "would_send": would_send,
                }
            ),
            status_code=200,
            media_type="application/json",
            headers={"X-Jentic-Execution-Id": execution_id},
        )

    # ── Build forwarded headers ───────────────────────────────────────────────
    forward_headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    # Strip inbound auth headers before injecting vault credentials —
    # prevents duplicate Authorization when toolkit key arrives as Basic auth
    # (e.g. git embedding the key in the remote URL).
    if inject_headers:
        forward_headers.pop("authorization", None)
    # Inject credentials (replaces any auth header)
    forward_headers.update(inject_headers)

    # ── Forward request ───────────────────────────────────────────────────────
    body_bytes = await request.body()

    # ── Prefer: wait=0 → async broker call ───────────────────────────────────
    if prefer_wait is not None and prefer_wait == 0.0:
        capability_id = f"{request.method}/{upstream_host}{upstream_path}"
        job_id = await create_job(
            kind="broker",
            slug_or_id=capability_id,
            toolkit_id=toolkit_id,
            inputs={},
            agent_id=agent_cid,
        )
        # Bind the job into the trace-writer closure so subsequent _write_trace
        # calls in this scope (and the background task below) stamp the trace
        # with this job_id, enabling the [job ↗] cross-link in the Execution
        # Log.
        current_job_id = job_id
        if callback_url:
            async with get_db() as _db:
                await _db.execute(
                    "UPDATE jobs SET callback_url=? WHERE id=?", (callback_url, job_id)
                )
                await _db.commit()

        async def _broker_bg():
            try:
                await update_job(job_id, status="running", trace_id=execution_id)
                fwd_hdrs = {
                    k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP
                }
                fwd_hdrs.update(inject_headers)
                _connector = aiohttp.TCPConnector(ssl=False if not _ssl_verify else None)
                async with aiohttp.ClientSession(connector=_connector, auto_decompress=False) as cl:
                    async with cl.request(
                        request.method,
                        upstream_url,
                        headers=fwd_hdrs,
                        data=body_bytes or None,
                        allow_redirects=True,
                        timeout=aiohttp.ClientTimeout(total=120.0),
                    ) as resp:
                        resp_body = await resp.read()
                        resp_text = resp_body.decode(errors="replace")
                upstream_async_flag = resp.status == 202
                upstream_loc = resp.headers.get("location") if upstream_async_flag else None
                result = {"status_code": resp.status, "body": resp_text[:4096]}

                # Update trace with final status
                trace_status = "success" if resp.status < 400 else "error"
                await _write_trace(trace_status, resp.status)
                if credential_id:
                    if resp.status < 400:
                        await vault.mark_credential_used(credential_id)
                        await vault.mark_credential_health(credential_id, healthy=True)
                    elif resp.status in (401, 403):
                        await vault.mark_credential_health(credential_id, healthy=False)

                if upstream_async_flag:
                    await update_job(
                        job_id,
                        status="upstream_async",
                        result=result,
                        http_status=202,
                        upstream_async=True,
                        upstream_job_url=upstream_loc,
                        trace_id=execution_id,
                    )
                elif resp.status < 400:
                    await update_job(
                        job_id,
                        status="complete",
                        result=result,
                        http_status=resp.status,
                        trace_id=execution_id,
                    )
                else:
                    await update_job(
                        job_id,
                        status="failed",
                        error=resp_text[:512],
                        http_status=resp.status,
                        trace_id=execution_id,
                    )
            except Exception as exc:
                # Update trace on exception
                await _write_trace("error", 500, f"Background task error: {str(exc)}")
                await update_job(job_id, status="failed", error=str(exc), trace_id=execution_id)
            finally:
                discard_task(job_id)

        task = asyncio.create_task(_broker_bg())
        register_task(job_id, task)

        # Write pending trace (will be updated by background task)
        await _write_trace("pending", 202)

        return Response(
            content=json.dumps(
                {
                    "status": "running",
                    "job_id": job_id,
                    "_links": {"poll": f"/jobs/{job_id}"},
                    "message": "Request dispatched asynchronously. Poll _links.poll for completion.",
                }
            ),
            status_code=202,
            media_type="application/json",
            headers={
                "Location": f"/jobs/{job_id}",
                "X-Jentic-Job-Id": job_id,
                "X-Jentic-Execution-Id": execution_id,
            },
        )

    try:
        _connector = aiohttp.TCPConnector(ssl=False if not _ssl_verify else None)
        async with aiohttp.ClientSession(connector=_connector, auto_decompress=False) as client:
            async with client.request(
                method=request.method,
                url=upstream_url,
                headers=forward_headers,
                data=body_bytes if body_bytes else None,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=60.0),
            ) as upstream_response:
                _upstream_body = await upstream_response.read()
                _upstream_status = upstream_response.status
                _upstream_headers = dict(upstream_response.headers)
    except asyncio.TimeoutError:
        await _write_trace("timeout", 504, f"Upstream {upstream_host} timeout after 60s")
        error_body = {
            "error": "UPSTREAM_TIMEOUT",
            "message": f"Upstream {upstream_host} did not respond within 60s",
        }
        return Response(
            content=json.dumps(error_body),
            status_code=504,
            media_type="application/json",
            headers={
                "X-Jentic-Error": "true",
                "X-Jentic-Execution-Id": execution_id,
                **_cred_headers,
            },
        )
    except aiohttp.ClientError:
        log.exception("Upstream request failed for %s", upstream_host)
        await _write_trace("error", 502, f"Network error reaching {upstream_host}")
        error_body = {
            "error": "UPSTREAM_UNREACHABLE",
            "message": f"Could not reach {upstream_host}.",
        }
        return Response(
            content=json.dumps(error_body),
            status_code=502,
            media_type="application/json",
            headers={
                "X-Jentic-Error": "true",
                "X-Jentic-Execution-Id": execution_id,
                **_cred_headers,
            },
        )

    # ── Build response — strip hop-by-hop, add Jentic trace headers ──────────
    response_headers = {
        k: v for k, v in _upstream_headers.items() if k.lower() not in _HOP_BY_HOP_RESPONSE
    }
    response_headers["X-Jentic-Execution-Id"] = execution_id
    response_headers.update(_cred_headers)

    # ── Confirm pending overlay on first successful call ──────────────────────
    if api_id and _upstream_status < 400:
        try:
            await confirm_overlay(api_id, execution_id)
        except Exception:
            pass  # non-fatal

    # ── Mark credential health + last_used_at from the upstream's verdict ─────
    # Best-effort; failures are swallowed inside vault.* (never blocks the path).
    # This is the manual-credential counterpart to the Pipedream OAuth-account
    # health write above: <400 means the upstream accepted the credential
    # (healthy + bump last_used_at); 401/403 means it was rejected (unhealthy →
    # red StatusDot). Other statuses (404/429/5xx) are not credential verdicts,
    # so we leave `healthy` untouched rather than flapping it on a flaky upstream.
    if credential_id:
        if _upstream_status < 400:
            await vault.mark_credential_used(credential_id)
            await vault.mark_credential_health(credential_id, healthy=True)
        elif _upstream_status in (401, 403):
            await vault.mark_credential_health(credential_id, healthy=False)

    # ── Auth failure hint for BasicAuth ───────────────────────────────────────
    # When a BasicAuth call gets 401/403, the likely cause is the wrong
    # username format. Surface a machine-readable hint so agents can
    # self-correct by researching and uploading an overlay.
    if _upstream_status in (401, 403):
        auth_header = inject_headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            hint = {
                "x-jentic-hint": "basic_auth_failure",
                "message": (
                    f"BasicAuth to {upstream_host} failed ({_upstream_status}). "
                    "The credential value may be correct but the identity (username) is wrong. "
                    "PATCH /credentials/{id} with the correct 'identity' field. "
                    "For most token-based APIs any username works; for traditional user/password APIs "
                    "the identity must match the actual account username."
                ),
                "action": "PATCH /credentials/{id}",
                "example": {"identity": "your_username_here"},
                "upstream_status": _upstream_status,
                "upstream_body": _upstream_body.decode(errors="replace")[:512],
            }
            response_headers["X-Jentic-Hint"] = "basic_auth_failure"
            # Write trace for BasicAuth failure hint path
            await _write_trace("error", _upstream_status, "BasicAuth failure - identity mismatch")
            return Response(
                content=json.dumps(hint),
                status_code=_upstream_status,
                headers=response_headers,
                media_type="application/json",
            )

    # ── Detect upstream 202: surface as upstream_async ───────────────────────
    # If the upstream itself returned 202, and a callback was registered,
    # create a job record so the agent has a consistent handle.
    if _upstream_status == 202 and callback_url:
        upstream_loc = _upstream_headers.get("location")
        capability_id = f"{request.method}/{upstream_host}{upstream_path}"
        job_id = await create_job(
            kind="broker",
            slug_or_id=capability_id,
            toolkit_id=toolkit_id,
            inputs={},
            agent_id=agent_cid,
        )
        # Bind the job into the trace-writer closure so the trace written
        # below carries job_id (Execution Log [job ↗] cross-link).
        current_job_id = job_id
        async with get_db() as _db:
            await _db.execute("UPDATE jobs SET callback_url=? WHERE id=?", (callback_url, job_id))
            await _db.commit()
        await update_job(
            job_id,
            status="upstream_async",
            result={"body": _upstream_body.decode(errors="replace")[:4096]},
            http_status=202,
            upstream_async=True,
            upstream_job_url=upstream_loc,
            trace_id=execution_id,
        )
        response_headers["X-Jentic-Job-Id"] = job_id
        response_headers["Location"] = f"/jobs/{job_id}"

    # Write trace for standard path (includes 202 upstream async case)
    trace_status = "success" if _upstream_status < 400 else "error"
    await _write_trace(trace_status, _upstream_status)

    return Response(
        content=_upstream_body,
        status_code=_upstream_status,
        headers=response_headers,
        media_type=_upstream_headers.get("content-type"),
    )
