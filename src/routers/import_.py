"""
POST /import — unified intake for APIs (OpenAPI) and workflows (Arazzo).

Source types:
  - type: "path"   — local file path already on disk
  - type: "url"    — fetch spec from a remote URL
  - type: "inline" — spec content posted directly in the request

Detects whether the spec is an OpenAPI or Arazzo document and routes accordingly.
Synchronous (no job queue) — suitable for Jentic's local deployment.

Authentication: requires toolkit key OR human session (agent-accessible).
"""

import json
import logging
import re
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter
from jentic.apitools.openapi.common.uri import is_http_https_url
from pydantic import Field

from src.bm25 import get_index
from src.config import SPECS_DIR, WORKFLOWS_DIR
from src.db import get_db
from src.models import ImportOut
from src.openapi_helpers import agent_hints
from src.routers.apis import (
    derive_api_id,
    is_private_server_url,
    parse_operations,
    rebuild_index,
)
from src.routers.catalog import lazy_import_catalog_workflows
from src.routers.workflows import workflow_capability_id
from src.validators import NormModel, NormStr


log = logging.getLogger("jentic")
router = APIRouter()


SPECS_DIR.mkdir(parents=True, exist_ok=True)
WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)


class ImportSource(NormModel):
    """Single import source for an OpenAPI spec or Arazzo workflow. Can be local file, URL, or inline content."""

    type: NormStr = Field(
        description="Source type: 'path' (local file), 'url' (fetch from URL), or 'inline' (spec content in request)",
        enum=["inline", "url", "path"],
    )
    path: str | None = Field(
        default=None, description="Local file system path (required if type='path')"
    )
    url: str | None = Field(
        default=None,
        examples=["https://api.example.com/openapi.json"],
        description="Remote spec URL (required if type='url')",
    )
    filename: str | None = Field(
        default=None,
        examples=["my-api.json"],
        description="Override filename for saved spec (optional)",
    )
    content: str | None = Field(
        default=None,
        description="Inline spec content as JSON or YAML string (required if type='inline')",
    )
    force_api_id: str | None = Field(
        default=None,
        examples=["github"],
        description="Override derived API ID with catalog canonical ID (optional)",
    )


class ImportRequest(NormModel):
    """Batch import request for multiple OpenAPI specs or Arazzo workflows. Sources processed in parallel."""

    sources: list[ImportSource] = Field(
        description="Array of import sources (OpenAPI specs or Arazzo workflows) to register in the catalog"
    )


def _load_doc(source: ImportSource) -> tuple[dict, str | None]:
    """Load and parse a spec document. Returns (doc, saved_path)."""
    if source.type == "path":
        if not source.path:
            raise ValueError("path required for type=path")
        p = Path(source.path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {source.path}")
        raw = p.read_text()
        doc = yaml.safe_load(raw) if str(p).endswith((".yaml", ".yml")) else json.loads(raw)
        return doc, str(p)

    elif source.type == "url":
        if not source.url:
            raise ValueError("url required for type=url")
        if not is_http_https_url(source.url):
            raise ValueError("Only http and https URLs are allowed")
        req = urllib.request.Request(source.url, headers={"User-Agent": "Jentic/0.2"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        doc = (
            yaml.safe_load(raw)
            if (
                source.url.endswith((".yaml", ".yml"))
                or raw.strip().startswith("openapi:")
                or raw.strip().startswith("arazzo:")
            )
            else json.loads(raw)
        )
        # Save locally
        fname = source.filename or _url_to_filename(source.url, api_id=source.force_api_id)
        dest = SPECS_DIR / fname if not _is_arazzo(doc) else WORKFLOWS_DIR / fname
        dest.write_text(
            json.dumps(doc, ensure_ascii=False)
            if isinstance(raw, str) and raw.strip().startswith("{")
            else raw
        )
        return doc, str(dest)

    elif source.type == "inline":
        if not source.content:
            raise ValueError("content required for type=inline")
        raw = source.content
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            doc = yaml.safe_load(raw)
        # Save locally
        fname = source.filename or f"inline_{uuid.uuid4().hex[:8]}.json"
        dest = SPECS_DIR / fname if not _is_arazzo(doc) else WORKFLOWS_DIR / fname
        dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2))
        return doc, str(dest)

    else:
        raise ValueError(f"Unknown source type: {source.type!r}. Valid: inline, url, path")


def _is_arazzo(doc: dict) -> bool:
    return "arazzo" in doc


def _url_to_filename(url: str, api_id: str | None = None) -> str:
    """Derive a unique local filename for a downloaded spec.

    When `api_id` is provided (catalog imports), prefix with the sanitised
    api_id so different APIs never collide even if the URL stems differ only
    beyond the 80-char window.
    """
    if api_id:
        safe_id = re.sub(r"[^a-zA-Z0-9._-]", "_", api_id)
        return f"{safe_id}_openapi.json"
    clean = re.sub(r"^https?://", "", url)
    clean = re.sub(r"[^a-zA-Z0-9._-]", "_", clean)
    clean = re.sub(r"_+", "_", clean).strip("_")
    return clean[:120] + ".json"


# ── OpenAPI registration ──────────────────────────────────────────────────────


async def register_openapi(doc: dict, saved_path: str, force_api_id: str | None = None) -> dict:
    """Register an OpenAPI spec as an API + operations in Jentic."""
    base_url = None
    servers = doc.get("servers", [])
    if servers:
        base_url = servers[0].get("url")

    title = doc.get("info", {}).get("title", "unknown")

    api_id = force_api_id or (derive_api_id(base_url, title=title) if base_url else None)
    if not api_id:
        api_id = re.sub(r"[^a-z0-9]", "-", title.lower()).strip("-")[:40]

    # Allow caller to override the derived ID (e.g. catalog import uses canonical catalog api_id)
    if force_api_id:
        api_id = force_api_id

    name = doc.get("info", {}).get("title") or api_id
    description = doc.get("info", {}).get("description")

    # Detect self-hosted API: private/localhost server URL → api_id ends in .local
    is_self_hosted = not force_api_id and base_url is not None and is_private_server_url(base_url)

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO apis (id, name, description, spec_path, base_url) VALUES (?,?,?,?,?)",
            (api_id, name, description, saved_path, base_url),
        )
        await db.commit()

    ops = parse_operations(api_id, saved_path, base_url)
    async with get_db() as db:
        for op in ops:
            await db.execute(
                """INSERT OR REPLACE INTO operations
                   (id, api_id, operation_id, jentic_id, method, path, summary, description)
                   VALUES (:id, :api_id, :operation_id, :jentic_id, :method, :path, :summary, :description)""",
                op,
            )
        await db.commit()

    await rebuild_index()

    # ── Auto-overlay for self-hosted APIs ─────────────────────────────────────
    # If the spec has a hardcoded private/localhost server URL, generate and
    # confirm a standard overlay that replaces it with a {host} template variable.
    # This enables credential server_variables to parameterise the actual host
    # without requiring users to manually upload an overlay.
    overlay_generated = False
    if is_self_hosted and base_url and "{" not in base_url:
        _parsed = urlparse(base_url)
        _path = _parsed.path.rstrip("/") or ""
        # Preserve any path component (e.g. http://localhost:8123/api → http://{host}/api)
        _template_url = "http://{host}" + _path
        _overlay = {
            "actions": [
                {
                    "target": "$",
                    "description": "Parameterise server URL for self-hosted deployment",
                    "update": {
                        "servers": [
                            {
                                "url": _template_url,
                                "variables": {
                                    "host": {
                                        "default": _parsed.netloc,
                                        "description": "Hostname (and optional port) of your local instance, e.g. 10.0.0.2:1984",
                                    }
                                },
                            }
                        ]
                    },
                }
            ]
        }
        async with get_db() as db:
            _oid = f"auto-{api_id}"
            await db.execute(
                """INSERT OR IGNORE INTO api_overlays (id, api_id, overlay, status)
                   VALUES (?, ?, ?, 'confirmed')""",
                (_oid, api_id, json.dumps(_overlay)),
            )
            await db.commit()
        overlay_generated = True
        # Update the stored base_url to the templated form so derive_api_id
        # and _resolve_server_url both see the template, not the hardcoded default.
        async with get_db() as db:
            await db.execute(
                "UPDATE apis SET base_url=? WHERE id=?",
                (_template_url, api_id),
            )
            await db.commit()

    # Auto-import catalog workflows when importing from catalog.
    workflows_imported = []
    if force_api_id:
        try:
            workflows_imported = await lazy_import_catalog_workflows(api_id)
        except Exception as e:
            logging.getLogger("jentic.import").warning(
                "Workflow auto-import failed for '%s': %s", api_id, e
            )

    result = {
        "type": "api",
        "id": api_id,
        "name": name,
        "operations_indexed": len(ops),
        "spec_path": saved_path,
        "workflows_imported": len(workflows_imported),
    }
    if is_self_hosted:
        result["self_hosted"] = True
        result["overlay_generated"] = overlay_generated
        result["server_variables_required"] = ["host"]
    return result


# ── Arazzo registration ───────────────────────────────────────────────────────


async def register_arazzo(
    doc: dict, saved_path: str, slug_hint: str | None = None, parent_api_id: str | None = None
) -> dict:
    """Register an Arazzo workflow file in Jentic."""
    info = doc.get("info", {})
    workflows_list = doc.get("workflows", [])
    if not workflows_list:
        raise ValueError("Arazzo document contains no workflows")

    wf = workflows_list[0]
    workflow_id = wf.get("workflowId", "")
    name = wf.get("summary") or info.get("title") or workflow_id
    description = wf.get("description") or info.get("description")
    steps = wf.get("steps", [])
    steps_count = len(steps)

    # Derive involved API IDs from operationIds in steps
    involved_apis: list[str] = []
    for step in steps:
        op = step.get("operationId") or step.get("operationPath", "")
        # capability id format: METHOD/host/path → extract host
        m = re.match(r"^[A-Z]+/([^/]+)", op)
        if m:
            host = m.group(1)
            if host not in involved_apis:
                involved_apis.append(host)

    # Fallback: extract API IDs from sourceDescriptions when steps use
    # Arazzo-native references ($sourceDescriptions.name.operationId)
    # rather than jentic capability IDs (METHOD/host/path).
    if not involved_apis:
        source_descs = doc.get("sourceDescriptions", [])
        for sd in source_descs:
            url = sd.get("url", "")
            # If rewritten to local spec path, look up the api_id from DB later;
            # for now try to extract from the URL
            if url.startswith("http"):
                from urllib.parse import urlparse as _urlparse  # noqa: PLC0415

                parsed = _urlparse(url)
                if parsed.hostname and parsed.hostname not in involved_apis:
                    involved_apis.append(parsed.hostname)
            elif "/" in url or url.endswith(".json") or url.endswith(".yaml"):
                # Local spec path — resolve api_id from DB
                pass

    # If we still have nothing but the caller told us which API this
    # workflow belongs to, use that.
    if not involved_apis and parent_api_id:
        involved_apis.append(parent_api_id)

    # Slug: prefer explicit hint, then workflowId, then filename
    if slug_hint:
        slug = slug_hint
    elif workflow_id:
        slug = re.sub(r"[^a-z0-9-]", "-", workflow_id.lower()).strip("-")[:60]
    else:
        slug = Path(saved_path).stem[:60]
    slug = re.sub(r"-+", "-", slug)

    input_schema = wf.get("inputs")

    async with get_db() as db:
        await db.execute(
            """INSERT OR REPLACE INTO workflows
               (slug, name, description, arazzo_path, input_schema, steps_count, involved_apis)
               VALUES (?,?,?,?,?,?,?)""",
            (
                slug,
                name,
                description,
                saved_path,
                json.dumps(input_schema) if input_schema else None,
                steps_count,
                json.dumps(involved_apis),
            ),
        )
        await db.commit()

    # Index in BM25
    index = get_index()
    index.add_workflow(slug, name, description, involved_apis)

    return {
        "type": "workflow",
        "id": workflow_capability_id(slug),
        "slug": slug,
        "name": name,
        "steps_count": steps_count,
        "arazzo_path": saved_path,
    }


# ── Route ─────────────────────────────────────────────────────────────────────


@router.post(
    "/import",
    summary="Import an API spec or workflow — add to the searchable catalog",
    response_model=ImportOut,
    openapi_extra={
        **agent_hints(
            when_to_use="Use to register a new API (OpenAPI 3.x spec) or workflow (Arazzo document) into the local catalog for searchability and execution. Supports three source types: url (fetch from remote URL), path (local file path), inline (spec content in request body). Automatically detects OpenAPI vs Arazzo, parses operations, computes capability IDs, and indexes for BM25 search. Use when adding a new API not yet in the catalog.",
            prerequisites=[
                "Requires authentication (toolkit key or human session)",
                "Valid OpenAPI 3.x or Arazzo 1.0 document",
                "For url type: publicly accessible spec URL",
                "For path type: local file system path (server must have read access)",
                "For inline type: spec content as JSON or YAML string",
            ],
            avoid_when="Do not use for APIs already in the catalog — check GET /apis or GET /catalog first. Do not use to add credentials (use POST /credentials). Do not use to update existing specs — delete and re-import instead.",
            related_operations=[
                "GET /apis — check if API is already registered before importing",
                "GET /catalog — browse available APIs in public catalog before importing",
                "POST /credentials — add credentials after importing an API",
                "GET /search — verify imported operations are searchable",
            ],
        ),
        "requestBody": {
            "description": "Array of import sources (local file paths, URLs, or inline spec content) to register in the catalog — supports OpenAPI 3.x and Arazzo 1.0"
        },
    },
)
async def import_sources(body: ImportRequest):
    """Registers an OpenAPI spec or Arazzo workflow into the catalog and BM25 index.
    Source types: path (local file), url (fetch from URL), inline (spec content in request body).
    For OpenAPI specs: parses operations, computes capability IDs, indexes descriptions.
    For Arazzo workflows: stores definition, extracts input schema and involved APIs.
    Returns the registered API or workflow with its canonical id.
    """
    results = []
    for i, source in enumerate(body.sources):
        try:
            doc, saved_path = _load_doc(source)
            if _is_arazzo(doc):
                result = await register_arazzo(doc, saved_path)
            else:
                result = await register_openapi(doc, saved_path, force_api_id=source.force_api_id)
            results.append({"index": i, "status": "success", **result})
        except Exception:
            log.exception("Import failed for source %d", i)
            results.append(
                {
                    "index": i,
                    "status": "failed",
                    "error": "Import failed. Check server logs for details.",
                    "source": source.model_dump(exclude_none=True),
                }
            )

    succeeded = sum(1 for r in results if r["status"] == "success")
    failed = len(results) - succeeded
    return {
        "status": "ok" if failed == 0 else ("partial" if succeeded > 0 else "failed"),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }
