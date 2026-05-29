"""API registry routes — add, list, and index operations."""

import asyncio
import copy
import json
import pathlib
import re
import uuid
from typing import Annotated
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import Response

import src.bm25 as bm25
from src.db import get_db
from src.models import ApiListPage, ApiOut, OperationListPage
from src.openapi_helpers import agent_hints
from src.routers.workflows import workflow_capability_id
from src.utils import abbreviate


router = APIRouter()


# ---------------------------------------------------------------------------
# Spec helpers
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base. Overlay values win on conflict."""
    result = copy.deepcopy(base)
    for k, v in overlay.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


async def _load_spec(spec_path: str) -> dict:
    """Load a JSON or YAML spec file. Returns {} on failure."""
    try:
        p = pathlib.Path(spec_path)
        raw = p.read_text()
        return yaml.safe_load(raw) if str(p).endswith((".yaml", ".yml")) else json.loads(raw)
    except Exception:
        return {}


async def _load_merged_spec(
    api_id: str, spec_path: str | None, include_pending: bool = False
) -> dict:
    """
    Return the base spec with all confirmed overlays applied.

    Only overlay actions with target "$" are supported — they are deep-merged
    into the root of the spec. Actions targeting specific paths/operations are
    noted in x-jentic-unapplied-overlays for transparency.
    """
    spec = await _load_spec(spec_path) if spec_path else {}
    unapplied = []

    status_filter = (
        "status IN ('confirmed', 'pending')" if include_pending else "status='confirmed'"
    )
    async with get_db() as db:
        async with db.execute(
            f"SELECT overlay FROM api_overlays WHERE api_id=? AND {status_filter} ORDER BY created_at ASC",
            (api_id,),
        ) as cur:
            rows = await cur.fetchall()

    for (overlay_json,) in rows:
        try:
            overlay = json.loads(overlay_json)
            for action in overlay.get("actions", []):
                target = action.get("target", "")
                update = action.get("update", {})
                if target == "$" and isinstance(update, dict):
                    spec = _deep_merge(spec, update)
                else:
                    unapplied.append(
                        {"target": target, "note": "non-root target, not applied to merged view"}
                    )
        except Exception:
            pass

    if unapplied:
        spec.setdefault("x-jentic-unapplied-overlays", []).extend(unapplied)

    return spec


async def load_api_desc(api_id: str, include_pending: bool = False) -> dict:
    """Load the merged API description (spec + overlays) by api_id.

    Looks up spec_path from the apis table, then delegates to _load_merged_spec.
    Returns {} if the API is not found or has no spec.
    """
    async with get_db() as db:
        async with db.execute("SELECT spec_path FROM apis WHERE id=?", (api_id,)) as cur:
            row = await cur.fetchone()
    if not row or not row[0]:
        return {}
    return await _load_merged_spec(api_id, row[0], include_pending=include_pending)


def extract_vendor(api_id: str | None) -> str | None:
    """Extract registrable domain from a URL-derived API ID.

    Examples:
        travelpartner.googleapis.com  -> googleapis.com
        api.stripe.com                -> stripe.com
        api.zoom.us/v2                -> zoom.us
        api.elevenlabs.io             -> elevenlabs.io
    """
    if not api_id:
        return None
    hostname = api_id.split("/")[0]  # strip any path component
    parts = hostname.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return hostname or None


# ── helpers ───────────────────────────────────────────────────────────────────


def _extract_base_url(doc: dict) -> str | None:
    """
    Extract the canonical base URL from an OpenAPI spec's servers array.
    Returns the first server URL, stripping trailing slash.
    e.g. "https://api.elevenlabs.io" from servers[0].url = "https://api.elevenlabs.io"
    """
    servers = doc.get("servers", [])
    if servers and isinstance(servers, list):
        url = servers[0].get("url", "").rstrip("/")
        if url.startswith("http"):
            return url
        return url if url else None
    return None


def _strip_version_suffix(path: str) -> str:
    """Strip a trailing version segment from a URL path.

    Matches:
      /v1, /v2, /v10         (vN)
      /1.0, /3.0, /2023-01   (major.minor or date-like)
      /1                     (bare integer, only at end)

    Does NOT strip structural path components like /api, /rest, etc.

    Examples:
      /v1           → ''
      /api/v10      → /api
      /api/1.0      → /api
      /api          → /api   (unchanged — not a version)
    """
    return re.sub(r"(/v\d+(\.\d+)*|/\d{4}-\d{2}-\d{2}|/\d+\.\d+|/\d+)$", "", path)


def is_private_server_url(url: str) -> bool:
    """Return True if the URL's host is a private/localhost address.

    Detects: localhost, 127.x, 10.x, 192.168.x, 172.16-31.x, bare 'localhost'
    without scheme, and pure template-variable hostnames like http://{host}.
    """
    if not url:
        return False
    parsed = urlparse(url)
    host = parsed.hostname or parsed.netloc or ""
    # Strip port
    host = host.split(":")[0].lower()
    if not host:
        return False
    # Pure template variable host — e.g. http://{host} — treat as self-hosted
    if host.startswith("{") and host.endswith("}"):
        return True
    if host in ("localhost", "127.0.0.1", "0.0.0.0"):
        return True
    if host.startswith("10."):
        return True
    if host.startswith("192.168."):
        return True
    if re.match(r"172\.(1[6-9]|2[0-9]|3[0-1])\.", host):
        return True
    return False


def _title_to_local_api_id(title: str) -> str:
    """Convert an OpenAPI info.title to a .local api_id slug.

    Examples:
      'go2RTC'          → 'go2rtc.local'
      'Home Assistant'  → 'home-assistant.local'
      'Portainer CE'    → 'portainer-ce.local'
    """
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return f"{slug}.local"


def derive_api_id(base_url: str, title: str | None = None) -> str:
    """
    Derive a canonical API ID from its base URL. This is the single function
    used for all api_id generation — direct imports and catalog lazy-imports alike.

    Rules applied in order:
      1. Strip URL scheme
      2. Strip template variables from hostname and path
      3. Strip trailing version suffix from path
      4. Strip leading "www." from hostname (www carries no semantic meaning
         and diverges from the catalog directory convention)

    Special case — self-hosted APIs:
      If the server URL's host is a private/localhost address (or a pure template
      variable like {host}), the api_id is derived from info.title instead,
      with a .local suffix: e.g. 'go2rtc.local', 'home-assistant.local'.
      If no title is available, falls back to hostname-based derivation.

    The broker uses the stored base_url column for actual HTTP routing, so the
    api_id host portion does not need to be a verbatim proxy target.

    Examples:
      https://api.openai.com/v1              → api.openai.com
      https://api.zoom.us/v2                 → api.zoom.us
      https://discord.com/api/v10            → discord.com/api
      https://api.stripe.com                 → api.stripe.com
      https://www.googleapis.com/calendar/v3 → googleapis.com/calendar
      https://www.googleapis.com/gmail/v1    → googleapis.com/gmail
      https://techpreneurs.ie                → techpreneurs.ie
      http://localhost:1984  (title=go2RTC)  → go2rtc.local
      http://{host}  (title=Home Assistant)  → home-assistant.local

    Template variables stripped:
      https://{dc}.api.mailchimp.com/3.0     → api.mailchimp.com
      https://{your-domain}.atlassian.net    → atlassian.net
    """
    # Self-hosted: private/localhost/template-variable server URL → use title slug
    if is_private_server_url(base_url) and title:
        return _title_to_local_api_id(title)

    parsed = urlparse(base_url)
    host = parsed.hostname or parsed.netloc or ""
    path = parsed.path.rstrip("/")

    # Strip path segments containing template vars entirely
    if path:
        clean_segments = [s for s in path.split("/") if "{" not in s and s]
        path = "/" + "/".join(clean_segments) if clean_segments else ""

    # Strip trailing version suffix
    path = _strip_version_suffix(path)

    # Strip template vars from hostname (e.g. {dc}.api.mailchimp.com → api.mailchimp.com)
    host = re.sub(r"\{[^}]+\}\.", "", host)  # leading template labels
    host = re.sub(r"\.\{[^}]+\}", "", host)  # trailing template labels
    host = host.strip(".")

    # Strip leading www. — no semantic value, diverges from catalog dir convention
    if host.startswith("www."):
        host = host[4:]

    return (host + path).lower() if host else base_url


def _compute_jentic_id(method: str, base_url: str | None, path: str) -> str:
    """
    Compute the canonical capability id for an operation.

    Format: "METHOD/host/path"  (scheme omitted — always https; single slash separator)
    e.g.:   "GET/api.elevenlabs.io/v1/models"
            "POST/api.stripe.com/v1/payment_intents"

    The method is always a valid HTTP verb; a hostname can never start with one,
    so METHOD/host/path is unambiguous without any special separator.

    If base_url is unavailable, falls back to:
            "GET/path"  (relative — still unambiguous within the API)
    """
    if base_url:
        host = re.sub(r"^https?://", "", base_url).rstrip("/")
        if not path.startswith("/"):
            path = "/" + path
        return f"{method.upper()}/{host}{path}"
    return f"{method.upper()}/{path}"


def parse_operations(api_id: str, spec_path: str, base_url: str | None = None) -> list[dict]:
    """
    Extract operations from an OpenAPI spec file.

    Returns a list of operation dicts with:
    - id: UUID (internal DB key)
    - jentic_id: "METHOD https://host/path" (the public semantic identifier)
    - operation_id: OpenAPI operationId string
    - method, path, summary, description
    """
    p = pathlib.Path(spec_path)
    if not p.exists():
        return []
    raw = p.read_text()
    doc = yaml.safe_load(raw) if spec_path.endswith((".yaml", ".yml")) else json.loads(raw)

    # Use passed base_url or extract from spec
    resolved_base = base_url or _extract_base_url(doc)

    ops = []
    for path, methods in doc.get("paths", {}).items():
        for method, op in methods.items():
            if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                continue
            jentic_id = _compute_jentic_id(method, resolved_base, path)
            ops.append(
                {
                    "id": str(uuid.uuid4()),
                    "api_id": api_id,
                    "operation_id": op.get("operationId"),
                    "jentic_id": jentic_id,
                    "method": method.upper(),
                    "path": path,
                    "summary": op.get("summary", ""),
                    "description": op.get("description", ""),
                }
            )
    return ops


def _load_base_url_from_spec(spec_path: str) -> str | None:
    """Load and extract base URL from a spec file."""
    p = pathlib.Path(spec_path)
    if not p.exists():
        return None
    try:
        raw = p.read_text()
        doc = yaml.safe_load(raw) if spec_path.endswith((".yaml", ".yml")) else json.loads(raw)
        return _extract_base_url(doc)
    except Exception:
        return None


async def rebuild_index():
    """Rebuild BM25 index from all operations + workflows in DB.

    BM25 is CPU-bound; runs in a thread pool so it doesn't block the event loop.
    """
    async with get_db() as db:
        async with db.execute(
            """SELECT o.id, o.api_id, o.operation_id, o.jentic_id, o.method, o.path,
                      o.summary, o.description, a.id as api_url_id
               FROM operations o
               LEFT JOIN apis a ON o.api_id = a.id"""
        ) as cur:
            op_rows = await cur.fetchall()
        async with db.execute(
            "SELECT slug, name, description, involved_apis FROM workflows"
        ) as cur:
            wf_rows = await cur.fetchall()

    ops = [
        {
            "_id": r[0],
            "_operation_id": r[2],
            "_api_id": r[1],  # raw api_id for _links construction
            "id": r[3],
            "summary": r[6],
            "description": r[7],
            "_vendor": extract_vendor(r[8]),
        }
        for r in op_rows
    ]

    wfs = [
        {
            "id": workflow_capability_id(r[0]),
            "slug": r[0],
            "name": r[1],
            "summary": r[1],
            "description": r[2],
            "involved_apis": json.loads(r[3]) if r[3] else [],
        }
        for r in wf_rows
    ]

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, bm25.build, ops, wfs)


async def _fetch_oauth_brokers(db, api_ids: list[str]) -> dict[str, list[dict]]:
    """Return {api_id: [{"broker_id": ..., "broker_app_id": ...}, ...]} for the given api_ids."""
    if not api_ids:
        return {}
    placeholders = ",".join("?" * len(api_ids))
    async with db.execute(
        f"SELECT api_id, broker_id, broker_app_id FROM api_broker_apps WHERE api_id IN ({placeholders})",
        tuple(api_ids),
    ) as cur:
        rows = await cur.fetchall()
    result: dict[str, list[dict]] = {}
    for api_id, broker_id, broker_app_id in rows:
        result.setdefault(api_id, []).append(
            {"broker_id": broker_id, "broker_app_id": broker_app_id}
        )
    return result


def _row_to_op(r) -> dict:
    """Map a DB row to the public OperationOut shape.

    Row order: id, api_id, operation_id, jentic_id, method, path, summary, description
    DB jentic_id → public id. Description is abbreviated for token efficiency.
    """
    return {
        "id": r[3],
        "summary": r[6],
        "description": abbreviate(r[7]),
    }


# ── routes ────────────────────────────────────────────────────────────────────


@router.get(
    "/apis",
    summary="List APIs — browse all available API providers (local and catalog)",
    response_model=ApiListPage,
    openapi_extra=agent_hints(
        when_to_use="Use when you need to discover available API providers by vendor name, browse registered APIs, or check which APIs have credentials configured. Returns both locally registered APIs (source: local) and available catalog APIs (source: catalog). Use ?q= to filter by API ID or name, ?source= to filter by source type, and ?page=/limit= for pagination.",
        prerequisites=["Requires authentication (toolkit key or human session)"],
        avoid_when="Do not use if you already know the API ID — use GET /apis/{api_id} directly instead. Do not use for natural language capability discovery — use GET /search for that.",
        related_operations=[
            "GET /apis/{api_id} — get detailed API metadata including security schemes and credential status",
            "GET /apis/{api_id}/operations — list all operations for a specific API",
            "GET /search — search for capabilities across all APIs by natural language intent",
            "POST /credentials — add credentials for an API (imports from catalog if not yet registered)",
        ],
    ),
)
async def list_apis(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(20, ge=1, le=100, description="Results per page"),
    source: str | None = Query(
        None,
        description="Filter by source: `local` (locally registered) or `catalog` (public catalog, not yet configured). Default: all.",
    ),
    q: str | None = Query(None, description="Substring filter on API id/name"),
    include_imported: bool = Query(
        False,
        description=(
            "When `source=catalog`, controls whether catalog entries that have "
            "already been imported into the local workspace are still returned. "
            "Default `false` preserves the historical 'things you don't have yet' "
            "behaviour used by the workspace 'From the catalog' section. The "
            "`/discover` UI sets this to `true` so users keep seeing the full "
            "Jentic public catalog after importing — registered entries surface "
            "with `source: local` and a `Ready` / `Credential expired` pill "
            "instead of vanishing. No-op when `source != catalog`."
        ),
    ),
):
    """Returns paginated list of API providers — both locally registered and from the Jentic public catalog.

    Every entry has:
    - `source: "local"` — spec is indexed locally, operations are searchable and executable
    - `source: "catalog"` — available from the Jentic public catalog; add credentials to use
    - `has_credentials: bool` — whether credentials have been configured for this API
    - `has_workflows: bool` — only on catalog rows; `true` when the public catalog
      also ships Arazzo workflows for this vendor (renders as a `+ workflows` chip
      in the UI). Always `false` / omitted on local rows since those workflows are
      already imported and listed under `GET /workflows`.

    Use `?source=local` or `?source=catalog` to filter. Default returns all.
    To use a catalog API: call `POST /credentials` with `api_id` set — the spec is imported automatically.
    """
    from src.routers.catalog import (  # noqa: PLC0415  # circular: catalog imports extract_vendor from here
        GITHUB_REPO,
        load_manifest,
        load_workflow_manifest,
    )

    # ── Load local APIs ────────────────────────────────────────────────────────
    async with get_db() as db:
        async with db.execute(
            "SELECT id, name, description, spec_path, base_url, created_at FROM apis ORDER BY id"
        ) as cur:
            local_rows = await cur.fetchall()
        # Which local API ids have credentials?
        async with db.execute(
            "SELECT DISTINCT api_id FROM credentials WHERE api_id IS NOT NULL"
        ) as cur:
            cred_api_ids: set[str] = {r[0] for r in await cur.fetchall()}
        # Credential counts per API
        async with db.execute(
            "SELECT api_id, COUNT(*) FROM credentials WHERE api_id IS NOT NULL GROUP BY api_id"
        ) as cur:
            cred_count_map: dict[str, int] = {r[0]: r[1] for r in await cur.fetchall()}
        # Which local APIs have workflows?
        async with db.execute(
            "SELECT DISTINCT json_each.value FROM workflows, json_each(workflows.involved_apis)"
        ) as cur:
            wf_local_api_ids: set[str] = {r[0] for r in await cur.fetchall()}
        # Workflow counts per API
        async with db.execute(
            "SELECT json_each.value, COUNT(*) FROM workflows, json_each(workflows.involved_apis) GROUP BY json_each.value"
        ) as cur:
            wf_count_map: dict[str, int] = {r[0]: r[1] for r in await cur.fetchall()}
        # Operation counts per API
        async with db.execute(
            "SELECT api_id, COUNT(*) FROM operations WHERE api_id IS NOT NULL GROUP BY api_id"
        ) as cur:
            op_count_map: dict[str, int] = {r[0]: r[1] for r in await cur.fetchall()}
        # Fetch oauth broker mappings for all local APIs
        local_api_ids = [r[0] for r in local_rows]
        broker_map = await _fetch_oauth_brokers(db, local_api_ids)

    local_entries = [
        {
            "id": r[0],
            "name": r[1],
            "vendor": extract_vendor(r[0]),
            "source": "local",
            "has_credentials": r[0] in cred_api_ids,
            "has_workflows": r[0] in wf_local_api_ids,
            "description": r[2],
            "base_url": r[4],
            "created_at": r[5],
            "operation_count": op_count_map.get(r[0], 0),
            "credential_count": cred_count_map.get(r[0], 0),
            "workflow_count": wf_count_map.get(r[0], 0),
            **({"oauth_brokers": broker_map[r[0]]} if r[0] in broker_map else {}),
        }
        for r in local_rows
        if not q or q.lower() in r[0].lower() or (r[1] and q.lower() in r[1].lower())
    ]
    # ── Build precise coverage sets for catalog dedup ──────────────────────────
    # For LOCAL api ids, compute:
    #   covered_sub_apis: exact catalog sub-api ids that are locally covered
    #     e.g. language.googleapis.com → "googleapis.com/language"
    #   covered_leaf_vendors: vendor base domains where we have a leaf-level local API
    #     e.g. api.stripe.com → "stripe.com"
    # Rule: hide a catalog entry if:
    #   - it's a sub-api (contains "/") AND exact sub-api is in covered_sub_apis, OR
    #   - it's a leaf (no "/") AND its vendor is in covered_leaf_vendors
    # This prevents language.googleapis.com hiding googleapis.com/gmail (a different API).
    #
    # CRITICAL: a *path-style* local id like `slack.com/openai` represents
    # ONE specific sub-API the user has imported (the "Slack AI Plugin"
    # subset). It is NOT a leaf-level API for the `slack.com` vendor —
    # the catalog still has a separate `slack.com` row (the full Slack
    # Web API) which the user has not imported and should still be able
    # to discover. So path-style ids must only be deduped against their
    # own exact match (handled below by `local_by_id`); they must NOT
    # contribute to `covered_leaf_vendors` or to the `covered_sub_apis`
    # derived from their hostname's subdomain (which would also be
    # nonsensical: `slack.com/openai` says nothing about whether the
    # user covers `slack.com/anything-else`).
    _GENERIC_SUBS = {"api", "www", "app", "web", "portal", "v1", "v2", "v3"}
    covered_sub_apis: set[str] = set()
    covered_leaf_vendors: set[str] = set()
    for local_id in {r[0] for r in local_rows}:
        if "/" in local_id:
            # Path-style sub-api — exact-match dedup only (via local_by_id below).
            continue
        hostname = local_id.split("/")[0]
        parts = hostname.split(".")
        if len(parts) < 2:
            continue
        vendor = ".".join(parts[-2:])
        sub = ".".join(parts[:-2]) if len(parts) > 2 else ""
        if sub and sub not in _GENERIC_SUBS:
            covered_sub_apis.add(f"{vendor}/{sub}")
        covered_leaf_vendors.add(vendor)

    # Lookup map keyed by api_id so the catalog branch can pivot a
    # registered manifest entry to its local row. Mirrors the columns
    # `local_entries` reads above; we don't recompute description / base_url.
    local_by_id: dict[str, tuple] = {r[0]: r for r in local_rows}

    # ── Load catalog entries (deduped against local by precise coverage) ──────
    catalog_entries: list[dict] = []
    if source != "local":
        manifest = load_manifest()
        # `has_workflows` annotation: the public catalog ships Arazzo
        # workflows for some vendors (in the parallel `workflows/` tree).
        # Mirror what `/search` already does — fold workflow availability
        # into a per-row boolean so the directory grid can render the
        # `+ workflows` chip without a second round-trip per card. Set
        # is precomputed once per request so the per-row check is O(1).
        wf_manifest = load_workflow_manifest()
        workflow_api_ids: set[str] = {e["api_id"] for e in wf_manifest} if wf_manifest else set()
        for e in manifest:
            api_id = e["api_id"]
            if q and q.lower() not in api_id.lower():
                continue

            # Exact-match local pivot. We do this *before* the
            # cross-vendor dedup so an imported API always surfaces
            # under `include_imported=true`, even if there's another
            # workspace API sharing its vendor base. Without this
            # ordering, the leaf-vendor dedup below would drop the
            # exact match and leave a confusing "I imported this and
            # it's still gone" hole on /discover.
            if api_id in local_by_id:
                if not include_imported:
                    # Default behaviour preserved: hide imports from
                    # /workspace's "From the catalog" section.
                    continue
                lr = local_by_id[api_id]
                catalog_entries.append(
                    {
                        "id": lr[0],
                        "name": lr[1] or api_id,
                        "vendor": extract_vendor(lr[0]),
                        "source": "local",
                        "has_credentials": lr[0] in cred_api_ids,
                        "has_workflows": lr[0] in wf_local_api_ids or api_id in workflow_api_ids,
                        "description": lr[2],
                        "base_url": lr[4],
                        "created_at": lr[5],
                        **({"oauth_brokers": broker_map[lr[0]]} if lr[0] in broker_map else {}),
                    }
                )
                continue

            # Cross-vendor dedup: hide a manifest entry when a
            # *different* workspace API already covers its vendor /
            # sub-api. Skipping these is correct regardless of
            # `include_imported` — surfacing them would render a card
            # for a vendor the user hasn't actually registered.
            if "/" in api_id:
                if api_id in covered_sub_apis:
                    continue
            else:
                vendor = extract_vendor(api_id)
                if vendor and vendor in covered_leaf_vendors:
                    continue

            catalog_entries.append(
                {
                    "id": api_id,
                    "name": api_id,
                    "vendor": extract_vendor(api_id),
                    "source": "catalog",
                    "has_credentials": False,
                    "has_workflows": api_id in workflow_api_ids,
                    "description": None,
                    # Surface `spec_url` here so the UI can call `POST /import`
                    # directly without a follow-up `GET /catalog/{api_id}`
                    # round-trip — see the May 2026 "Import to workspace
                    # shouldn't force the credential flow" change. Mirrors the
                    # `has_workflows` precedent: anything cheap and useful
                    # from the manifest gets folded into the row.
                    "spec_url": e.get("spec_url"),
                    "_links": {
                        "catalog": f"/catalog/{api_id}",
                        "github": f"https://github.com/{GITHUB_REPO}/tree/main/{e['path']}",
                    },
                }
            )

    # ── Merge, filter, paginate ────────────────────────────────────────────────
    if source == "local":
        combined = local_entries
    elif source == "catalog":
        combined = catalog_entries
    else:
        combined = local_entries + catalog_entries

    total = len(combined)
    offset = (page - 1) * limit
    data = combined[offset : offset + limit]
    total_pages = max(1, (total + limit - 1) // limit)
    has_more = page < total_pages

    qs = f"&source={source}" if source else ""
    qs += f"&q={q}" if q else ""
    base_url_str = f"/apis?limit={limit}{qs}"
    return {
        "data": data,
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "has_more": has_more,
        "_links": {
            "self": f"{base_url_str}&page={page}",
            **({"next": f"{base_url_str}&page={page + 1}"} if has_more else {}),
            **({"prev": f"{base_url_str}&page={page - 1}"} if page > 1 else {}),
        },
    }


_API_CONTENT_TYPES = {
    "application/json": {"schema": {"type": "object"}},
    "application/yaml": {"schema": {"type": "string", "description": "API detail as YAML"}},
    "text/markdown": {"schema": {"type": "string", "description": "LLM-friendly API summary"}},
}

_OP_LIST_CONTENT_TYPES = {
    "application/json": {"schema": {"type": "object"}},
    "application/yaml": {"schema": {"type": "string", "description": "Operation list as YAML"}},
    "text/markdown": {
        "schema": {"type": "string", "description": "Operation list as Markdown table"}
    },
}

_VALID_SECTIONS = {"info", "servers", "security", "tags", "paths", "components", "webhooks"}
_DEFAULT_SECTIONS = {"info", "servers", "security"}

# Sections whose spec data can be large — not included by default, opt-in only
_LARGE_SECTIONS = {"paths", "components", "webhooks"}


@router.get(
    "/apis/{api_id:path}/openapi.json",
    summary="Download merged OpenAPI spec as JSON — base spec with all confirmed overlays applied",
    openapi_extra=agent_hints(
        when_to_use="Use when you need the full OpenAPI specification file for an API with all confirmed overlays applied (security scheme corrections, server URL fixes). Returns complete spec as JSON download with Content-Disposition attachment header. Overlay actions with target: $ are deep-merged; other actions listed in x-jentic-unapplied-overlays. Useful for SDK generation, schema analysis, or importing into external tools.",
        prerequisites=[
            "Requires authentication (toolkit key or human session)",
            "Valid API ID from GET /apis (format: hostname or hostname/path)",
        ],
        avoid_when="Do not use for lightweight API inspection — use GET /apis/{api_id}?sections=info,servers,security instead. Do not use to browse operations — use GET /apis/{api_id}/operations for paginated operation list.",
        related_operations=[
            "GET /apis/{api_id} — get API metadata with selective spec sections (no download, lighter weight)",
            "GET /apis/{api_id}/openapi.yaml — download the same spec in YAML format",
            "GET /apis/{api_id}/operations — list operations without downloading full spec",
            "GET /apis/{api_id}/overlays — view overlays that are merged into this spec",
            "POST /apis/{api_id}/overlays — submit a new overlay to correct security schemes or servers",
        ],
    ),
)
async def get_api_openapi_json(
    api_id: Annotated[str, Path(description="API ID (hostname or hostname/path format)")],
):
    """
    Returns the full merged OpenAPI spec for this API as a JSON download.

    All confirmed overlays are applied on top of the base spec using deep merge
    (overlay values win on conflict). Pending overlays are not included.

    Overlay actions with `target: "$"` are applied as root-level deep merges.
    Actions targeting specific paths or operations are listed in
    `x-jentic-unapplied-overlays` for transparency.

    For selective access to spec sections without downloading the full file,
    use `GET /apis/{api_id}?sections=info,servers,security,tags`.
    """
    async with get_db() as db:
        async with db.execute("SELECT id, spec_path FROM apis WHERE id=?", (api_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"API '{api_id}' not found")

    spec = await _load_merged_spec(api_id, row[1])
    filename = api_id.replace("/", "_") + ".openapi.json"
    return Response(
        content=json.dumps(spec, indent=2),
        media_type="application/openapi+json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/apis/{api_id:path}/openapi.yaml",
    summary="Download merged OpenAPI spec as YAML — base spec with all confirmed overlays applied",
    openapi_extra=agent_hints(
        when_to_use="Use when you need the full OpenAPI specification file for an API in YAML format with all confirmed overlays applied. Same content as GET /apis/{api_id}/openapi.json but in YAML. Useful for human readability, configuration files, or tools that prefer YAML format.",
        prerequisites=[
            "Requires authentication (toolkit key or human session)",
            "Valid API ID from GET /apis (format: hostname or hostname/path)",
        ],
        avoid_when="Do not use for lightweight API inspection — use GET /apis/{api_id}?sections=info,servers,security instead. Do not use to browse operations — use GET /apis/{api_id}/operations for paginated operation list.",
        related_operations=[
            "GET /apis/{api_id} — get API metadata with selective spec sections (no download, lighter weight)",
            "GET /apis/{api_id}/openapi.json — download the same spec in JSON format",
            "GET /apis/{api_id}/operations — list operations without downloading full spec",
            "GET /apis/{api_id}/overlays — view overlays that are merged into this spec",
            "POST /apis/{api_id}/overlays — submit a new overlay to correct security schemes or servers",
        ],
    ),
)
async def get_api_openapi_yaml(
    api_id: Annotated[str, Path(description="API ID (hostname or hostname/path format)")],
):
    """
    Returns the full merged OpenAPI spec for this API as a YAML download.

    All confirmed overlays are applied on top of the base spec using deep merge
    (overlay values win on conflict). Pending overlays are not included.

    Overlay actions with `target: "$"` are applied as root-level deep merges.
    Actions targeting specific paths or operations are listed in
    `x-jentic-unapplied-overlays` for transparency.

    For selective access to spec sections without downloading the full file,
    use `GET /apis/{api_id}?sections=info,servers,security,tags`.
    """
    async with get_db() as db:
        async with db.execute("SELECT id, spec_path FROM apis WHERE id=?", (api_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"API '{api_id}' not found")

    spec = await _load_merged_spec(api_id, row[1])
    filename = api_id.replace("/", "_") + ".openapi.yaml"
    return Response(
        content=yaml.dump(spec, default_flow_style=False, sort_keys=False, allow_unicode=True),
        media_type="application/openapi+yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/apis/{api_id:path}/operations",
    summary="List operations for an API — enumerate all available actions",
    response_model=OperationListPage,
    responses={
        200: {
            "description": "Operation list — format controlled by Accept header.",
            "content": _OP_LIST_CONTENT_TYPES,
        }
    },
    openapi_extra=agent_hints(
        when_to_use="Use after finding an API via GET /apis to enumerate all available operations (endpoints) for that API. Returns paginated list of capability IDs, summaries, and descriptions. Each operation can then be inspected via GET /inspect/{id} for full parameter schemas and auth requirements before execution. Useful for discovering what actions an API supports.",
        prerequisites=[
            "Requires authentication (toolkit key or human session)",
            "Valid API ID from GET /apis (format: hostname or hostname/path)",
        ],
        avoid_when="Do not use for natural language capability discovery across all APIs — use GET /search instead. Do not use to inspect a specific operation's parameters — use GET /inspect/{id} after finding the capability ID.",
        related_operations=[
            "GET /apis — list available APIs to find the api_id",
            "GET /inspect/{id} — inspect operation details (parameters, request/response schemas, auth)",
            "GET /search — search for specific capabilities by natural language intent instead of browsing",
            "GET /{target} (broker) — execute an operation after finding its capability ID",
        ],
    ),
)
async def list_api_operations(
    api_id: Annotated[str, Path(description="API ID to list operations for")],
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(50, ge=1, le=200, description="Results per page"),
    offset: int | None = Query(
        None,
        ge=0,
        description=(
            "Skip N operations (0-indexed). When provided, takes precedence over "
            "`page` for cursor-style pagination — pass `offset=N&limit=M` to grab "
            "an arbitrary window from the Detail Sheet's load-more affordance."
        ),
    ),
    tag: str | None = Query(
        None,
        description=(
            "Case-insensitive substring filter on the operation's OpenAPI `tags[]`. "
            "Tags are projected from the spec at request time. `total` reflects the "
            "post-filter count so the page envelope stays consistent."
        ),
    ),
):
    """Returns paginated list of operations for the given API. Each item has capability id, summary, description and OpenAPI tags. Use GET /inspect/{id} for full schema."""
    async with get_db() as db:
        async with db.execute("SELECT id, spec_path FROM apis WHERE id=?", (api_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, f"API '{api_id}' not found")
        spec_path = row[1]
        async with db.execute(
            """SELECT id, api_id, operation_id, jentic_id, method, path, summary, description
               FROM operations WHERE api_id=? ORDER BY jentic_id""",
            (api_id,),
        ) as cur:
            all_rows = await cur.fetchall()

    # Build (method, path) → tags lookup from the merged spec. Tags aren't
    # stored in the operations table — they're spec-only metadata. Loading
    # the spec per-request is fine here: this endpoint is interactive and
    # the spec parse already happens on demand for the spec-section views.
    tags_by_key: dict[tuple[str, str], list[str]] = {}
    if spec_path:
        spec = await _load_merged_spec(api_id, spec_path)
        for path, methods in (spec.get("paths") or {}).items():
            if not isinstance(methods, dict):
                continue
            for method, op in methods.items():
                if not isinstance(op, dict):
                    continue
                raw_tags = op.get("tags") or []
                op_tags = [t for t in raw_tags if isinstance(t, str)]
                tags_by_key[(method.upper(), path)] = op_tags

    enriched: list[tuple[tuple, list[str]]] = []
    tag_lower = tag.lower() if tag else None
    for r in all_rows:
        op_tags = tags_by_key.get((r[4], r[5]), [])
        if tag_lower is not None and not any(tag_lower in t.lower() for t in op_tags):
            continue
        enriched.append((r, op_tags))

    total = len(enriched)
    # Honour `offset` when supplied; otherwise fall back to page-based maths
    # so existing callers (and the generated client) keep working.
    if offset is not None:
        start = offset
        page = (offset // limit) + 1 if limit else 1
    else:
        start = (page - 1) * limit
    window = enriched[start : start + limit]
    truncated = (start + len(window)) < total

    total_pages = max(1, (total + limit - 1) // limit) if limit else 1
    has_more = (start + len(window)) < total
    base = f"/apis/{api_id}/operations"
    return {
        "data": [{**_row_to_op(r), "tags": op_tags} for r, op_tags in window],
        "page": page,
        "limit": limit,
        "offset": start,
        "total": total,
        "total_pages": total_pages,
        "has_more": has_more,
        "truncated": truncated,
        "_links": {
            "self": f"{base}?page={page}&limit={limit}",
            **({"next": f"{base}?page={page + 1}&limit={limit}"} if has_more else {}),
            **({"prev": f"{base}?page={page - 1}&limit={limit}"} if page > 1 else {}),
        },
    }


# NOTE: This catch-all route ({api_id:path} matches slashes) MUST be registered
# last among /apis/{api_id:path}/* routes. FastAPI/Starlette match in registration
# order — if this route appears first, it swallows /operations, /openapi.json, etc.
@router.get(
    "/apis/{api_id:path}",
    summary="Get API details — metadata, auth schemes, servers, and optional spec sections",
    response_model=ApiOut,
    responses={
        200: {
            "description": "API detail — format controlled by Accept header.",
            "content": _API_CONTENT_TYPES,
        }
    },
    openapi_extra=agent_hints(
        when_to_use="Use after finding an API via GET /apis or GET /search to inspect its authentication requirements, security schemes, and available credential setup options. Critical for understanding which auth types need credentials before calling operations. Returns API metadata enriched with OpenAPI spec sections (info, servers, security_schemes). Use ?sections= to request additional spec sections (tags, paths, components).",
        prerequisites=[
            "Requires authentication (toolkit key or human session)",
            "Valid API ID from GET /apis or catalog (format: hostname or hostname/path)",
        ],
        avoid_when="Do not use to download the full OpenAPI spec — use GET /apis/{api_id}/openapi.json for that. Do not use to list operations — use GET /apis/{api_id}/operations instead.",
        related_operations=[
            "GET /apis — list available APIs to find the api_id",
            "GET /apis/{api_id}/openapi.json — download full merged OpenAPI spec with overlays applied",
            "GET /apis/{api_id}/operations — list all operations for this API",
            "POST /credentials — add credentials after inspecting security_schemes",
            "GET /credentials?api_id={api_id} — check which credentials are configured",
        ],
    ),
)
async def get_api(
    api_id: Annotated[str, Path(description="API ID (hostname or hostname/path format)")],
    sections: str | None = Query(
        None,
        description=(
            "Comma-separated list of OpenAPI spec sections to include in the response. "
            f"Valid values: {', '.join(sorted(_VALID_SECTIONS))}. "
            f"Default (when omitted): {', '.join(sorted(_DEFAULT_SECTIONS))}. "
            "Large sections (paths, components, webhooks) must be requested explicitly. "
            "Use GET /apis/{api_id}/openapi.json to download the full merged spec."
        ),
    ),
):
    """
    Returns API metadata enriched with selected OpenAPI spec sections.

    **Default response** (no `?sections=`) includes:
    - Summary fields: id, name, vendor, description, base_url, operation_count, overlay_count
    - `info` — title, version, contact, license, terms of service
    - `servers` — base URLs and variables (merged from spec + confirmed overlays)
    - `security_schemes` — security scheme definitions (merged from spec + confirmed overlays),
      plus `security_required` (global security requirements)
    - `credentials_configured` — list of auth_types that already have a credential bound.
      Use this to build a credential-setup UI: iterate `security_schemes`, check each key
      against `security_schemes` (each scheme has a `type` field) to determine which auth types need credentials.
      to fill in the required fields and POST to `/credentials`.

    **Credential setup flow:**
    1. Call `GET /apis/{api_id}` — inspect `security_schemes` and `credentials_configured`
    2. For each unconfigured scheme, determine required fields from the scheme type:
       - `http bearer` → `secret` (token)
       - `http basic` → `secret` (password) + optional `identity` (username)
       - `apiKey` → `secret` (key value); if compound, check scheme names for Secret/Identity
    3. Prompt user for values, then `POST /credentials` with `api_id`, `auth_type`, `value` (and `identity` if needed).
    4. Verify with `GET /credentials?api_id={api_id}`

    **Optional sections** (add via `?sections=`):
    - `tags` — tag objects with names and descriptions
    - `paths` — full paths object (can be very large — prefer GET /apis/{api_id}/operations)
    - `components` — all reusable component definitions (schemas, parameters, responses, etc.)
    - `webhooks` — OpenAPI 3.1 webhooks (if present)

    **Full spec download:** `GET /apis/{api_id}/openapi.json`
    """
    async with get_db() as db:
        async with db.execute(
            "SELECT id, name, description, spec_path, base_url, created_at FROM apis WHERE id=?",
            (api_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, f"API '{api_id}' not found")

        async with db.execute("SELECT COUNT(*) FROM operations WHERE api_id=?", (api_id,)) as cur:
            op_count = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*), SUM(CASE WHEN status='confirmed' THEN 1 ELSE 0 END) FROM api_overlays WHERE api_id=?",
            (api_id,),
        ) as cur:
            ov_row = await cur.fetchone()
            overlay_count = ov_row[0] if ov_row else 0
            confirmed_overlay_count = ov_row[1] if ov_row else 0

        broker_map = await _fetch_oauth_brokers(db, [api_id])

        # Which security schemes already have at least one credential bound?
        # Used by client UIs to show "configured" vs "missing" per scheme.
        async with db.execute(
            "SELECT DISTINCT auth_type FROM credentials WHERE api_id=? AND auth_type IS NOT NULL",
            (api_id,),
        ) as cur:
            cred_rows = await cur.fetchall()
        credentials_configured = [r[0] for r in cred_rows]

    # Parse requested sections
    requested: set[str]
    if sections is None:
        requested = set(_DEFAULT_SECTIONS)
    else:
        requested = {s.strip().lower() for s in sections.split(",") if s.strip()}
        unknown = requested - _VALID_SECTIONS
        if unknown:
            raise HTTPException(
                400,
                f"Unknown section(s): {', '.join(sorted(unknown))}. "
                f"Valid: {', '.join(sorted(_VALID_SECTIONS))}",
            )

    spec_path = row[3]
    spec_description = row[2]

    # Load merged spec only if any spec sections are requested
    spec: dict = {}
    if requested:
        spec = await _load_merged_spec(api_id, spec_path)
        if not spec_description:
            spec_description = spec.get("info", {}).get("description")

    response: dict = {
        "id": row[0],
        "name": row[1],
        "vendor": extract_vendor(row[0]),
        "description": spec_description,
        "base_url": row[4],
        "created_at": row[5],
        "operation_count": op_count,
        "overlay_count": overlay_count,
        "confirmed_overlay_count": confirmed_overlay_count,
        "credentials_configured": credentials_configured,
    }

    if api_id in broker_map:
        response["oauth_brokers"] = broker_map[api_id]

    # --- Default sections (always included unless explicitly deselected) ---

    if "info" in requested and "info" in spec:
        response["info"] = spec["info"]

    if "servers" in requested:
        response["servers"] = spec.get("servers", [])

    if "security" in requested:
        # Expose security schemes and global security requirements together
        # under a predictable key — this is what agents need to configure credentials
        components = spec.get("components", {})
        schemes = components.get("securitySchemes", {})
        response["security_schemes"] = schemes
        response["security_required"] = spec.get("security", [])

    # --- Opt-in sections (potentially large) ---

    if "tags" in requested:
        response["tags"] = spec.get("tags", [])

    if "paths" in requested:
        response["paths"] = spec.get("paths", {})

    if "components" in requested:
        response["components"] = spec.get("components", {})

    if "webhooks" in requested:
        response["webhooks"] = spec.get("webhooks", {})

    return response


@router.delete("/apis/{api_id:path}", status_code=204, summary="Remove an API from the workspace")
async def delete_api(
    api_id: Annotated[str, Path(description="API id to delete, e.g. api.elevenlabs.io")],
    cascade: Annotated[
        bool, Query(description="If true, also delete credentials bound to this API")
    ] = False,
):
    """Remove an API and its single-API workflows from the workspace.

    By default credentials are preserved (api_id reference kept intact) so they
    automatically re-link if the API is re-imported later. Toolkit bindings also
    survive. Pass `cascade=true` to also delete all credentials and their
    toolkit bindings for a clean slate.
    """
    from src.config import WORKFLOWS_DIR  # noqa: PLC0415

    async with get_db() as db:
        async with db.execute("SELECT spec_path FROM apis WHERE id=?", (api_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "API not found")
        spec_path = row[0]

        # Delete workflows that only involve this API
        await db.execute(
            "DELETE FROM workflows WHERE involved_apis=? OR involved_apis=?",
            (json.dumps([api_id]), f'["{api_id}"]'),
        )

        if cascade:
            # Gather credential IDs first for toolkit unbinding
            async with db.execute("SELECT id FROM credentials WHERE api_id=?", (api_id,)) as cur:
                cred_ids = [r[0] for r in await cur.fetchall()]

            # Remove toolkit bindings for these credentials
            if cred_ids:
                placeholders = ",".join("?" * len(cred_ids))
                await db.execute(
                    f"DELETE FROM toolkit_credentials WHERE credential_id IN ({placeholders})",
                    cred_ids,
                )

            # Delete the credentials themselves
            await db.execute("DELETE FROM credentials WHERE api_id=?", (api_id,))
        # Non-cascade: credentials keep their api_id so they auto-link on re-import

        # CASCADE handles: operations, api_overlays, api_broker_apps
        await db.execute("DELETE FROM apis WHERE id=?", (api_id,))
        await db.commit()

    # Clean up spec file from disk
    if spec_path:
        try:
            pathlib.Path(spec_path).unlink(missing_ok=True)
        except OSError:
            pass

    # Clean up workflow arazzo files for this API
    safe_id = re.sub(r"[^a-z0-9_-]", "_", api_id.lower())
    for f in WORKFLOWS_DIR.glob(f"catalog_{safe_id}_*"):
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass

    await rebuild_index()


@router.post("/admin/rebuild-index", status_code=200, include_in_schema=False)
async def rebuild_search_index():
    """Manually rebuild the BM25 search index from all operations in the DB.

    Call this after batch API/operation changes to refresh search results.
    """
    await rebuild_index()
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) FROM operations") as cur:
            count = (await cur.fetchone())[0]
    return {"status": "ok", "operations_indexed": count}


@router.post("/admin/purge-old-api-ids", status_code=200, include_in_schema=False)
async def purge_old_api_ids():
    """Delete legacy slug-based API IDs, keeping only valid URL-derived IDs.

    Keeps IDs that:
    - contain at least one dot (URL-derived hostname)
    - do NOT start with '{'  (template variable placeholders)
    - are NOT a bare TLD ('com', 'net', 'org', 'io')
    """
    BAD_IDS = {"com", "net", "org", "io", "us", "ai"}

    async with get_db() as db:
        async with db.execute("SELECT id FROM apis") as cur:
            all_ids = [r[0] for r in await cur.fetchall()]

        to_delete = [
            aid for aid in all_ids if "." not in aid or aid.startswith("{") or aid in BAD_IDS
        ]

        for aid in to_delete:
            await db.execute("DELETE FROM operations WHERE api_id=?", (aid,))
            await db.execute("DELETE FROM apis WHERE id=?", (aid,))
        await db.commit()

    await rebuild_index()

    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) FROM apis") as cur:
            api_count = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM operations") as cur:
            op_count = (await cur.fetchone())[0]

    return {
        "status": "ok",
        "deleted": len(to_delete),
        "deleted_ids": to_delete,
        "apis_remaining": api_count,
        "operations_indexed": op_count,
    }


# Alias used by main.py lifespan startup
rebuild_index_on_startup = rebuild_index
