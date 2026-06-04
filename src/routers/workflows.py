"""
Workflow routes.

Workflow URIs are functioning URLs — consistent with how operation identities work.
The identity of a workflow is: POST/{jentic_hostname}/workflows/{slug}

  GET  /workflows/{slug}   → return workflow definition (Arazzo JSON / YAML / HTML)
  POST /workflows/{slug}   → execute the workflow
  GET  /workflows          → list all workflows

Workflow capability IDs in search/inspect use the same METHOD/host/path format
as operation IDs. The backend detects them by matching the Jentic hostname.
"""

import asyncio
import copy
import html
import json
import os
import pathlib
import sys
import tempfile
import time
import traceback
from typing import Annotated

import yaml
from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from jentic.apitools.openapi.common.uri import is_http_https_url

from src.config import JENTIC_PUBLIC_HOSTNAME
from src.db import get_db
from src.openapi_helpers import agent_hints
from src.routers.catalog import GITHUB_REPO, load_workflow_manifest
from src.routers.jobs import create_job, discard_task, get_job, register_task, update_job
from src.routers.traces import new_trace_id, write_trace
from src.utils import parse_prefer_wait, workflow_has_async_steps


router = APIRouter()


def workflow_capability_id(slug: str) -> str:
    """Return the canonical capability ID for a workflow.

    Format: POST/{jentic_hostname}/workflows/{slug}
    e.g.:   POST/{JENTIC_PUBLIC_HOSTNAME}/workflows/discourse-openai-summarise
    """
    return f"POST/{JENTIC_PUBLIC_HOSTNAME}/workflows/{slug}"


def workflow_url(slug: str) -> str:
    """Return the functioning HTTPS URL for a workflow."""
    return f"https://{JENTIC_PUBLIC_HOSTNAME}/workflows/{slug}"


def parse_arazzo(arazzo_path: str) -> dict:
    p = pathlib.Path(arazzo_path)
    if not p.exists():
        return {}
    raw = p.read_text()
    if str(p).endswith((".yaml", ".yml")):
        return yaml.safe_load(raw) or {}
    return json.loads(raw)


def _preprocess_arazzo_for_broker(arazzo_path: str, broker_base_url: str) -> tuple[str, list[str]]:
    """Rewrite sourceDescription spec servers to route through the broker.

    For each sourceDescription that points to a local spec file, creates a
    temporary copy with servers[0].url rewritten from e.g.
        https://api.openai.com  →  http://localhost:8900/api.openai.com

    Returns (temp_arazzo_path, [temp_spec_paths]) — caller must delete these
    after the subprocess completes.
    """
    doc = parse_arazzo(arazzo_path)
    temp_files: list[str] = []
    new_sources = []

    for src in doc.get("sourceDescriptions", []):
        spec_url = src.get("url", "")
        # Only process local file paths we can open
        if spec_url.startswith("/"):
            try:
                with open(spec_url) as f:
                    spec = json.load(f)
                servers = spec.get("servers", [])
                if servers:
                    original_url = servers[0].get("url", "")
                    host = original_url.replace("https://", "").replace("http://", "").rstrip("/")
                    # Skip template-variable hosts like {subdomain}.example.com
                    if host and "{" not in host:
                        spec_copy = copy.deepcopy(spec)
                        spec_copy["servers"][0]["url"] = f"{broker_base_url}/{host}"
                        with tempfile.NamedTemporaryFile(
                            mode="w", suffix=".json", delete=False
                        ) as tf:
                            json.dump(spec_copy, tf)
                            temp_spec = tf.name
                        temp_files.append(temp_spec)
                        new_src = dict(src)
                        new_src["url"] = temp_spec
                        new_sources.append(new_src)
                        continue
            except Exception:
                pass  # fall through to append original src unchanged
        new_sources.append(src)

    doc["sourceDescriptions"] = new_sources
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        json.dump(doc, tf)
        temp_arazzo = tf.name

    return temp_arazzo, temp_files


def _extract_workflow_meta(doc: dict, workflow_id: str | None = None) -> dict:
    """Extract metadata from an Arazzo document for a specific workflowId."""
    workflows_list = doc.get("workflows", [])
    if not workflows_list:
        return {}
    # Pick requested workflow or first one
    wf = next((w for w in workflows_list if w.get("workflowId") == workflow_id), None)
    if wf is None:
        wf = workflows_list[0]
    steps = wf.get("steps", [])
    return {
        "workflow_id": wf.get("workflowId"),
        "name": wf.get("summary") or doc.get("info", {}).get("title"),
        "description": wf.get("description") or doc.get("info", {}).get("description"),
        "input_schema": wf.get("inputs"),
        "steps": [
            {
                "id": s.get("stepId"),
                "operation": s.get("operationId") or s.get("operationPath"),
                "description": s.get("description"),
            }
            for s in steps
        ],
        "steps_count": len(steps),
    }


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get(
    "/workflows",
    summary="List workflows — browse available multi-step Arazzo workflows",
    tags=["catalog"],
    openapi_extra=agent_hints(
        when_to_use="Use when you need to discover multi-step workflows or automated sequences. Lists both registered workflows (source: local) and available catalog workflow sources (source: catalog). Use ?q= to filter by name or API.",
        prerequisites=["Requires authentication (toolkit key or human session)"],
        avoid_when="Do not use if you already know the workflow slug — use GET /workflows/{slug} directly instead.",
        related_operations=[
            "GET /workflows/{slug} — get workflow definition and input schema",
            "POST /workflows/{slug} — execute a workflow via broker",
            "GET /search — search across operations and workflows by natural language intent",
            "GET /catalog — browse available APIs when you know workflows exist for a vendor",
        ],
    ),
)
async def list_workflows(
    page: int | None = Query(
        None,
        ge=1,
        description=(
            "Page number (1-indexed). When supplied alongside `limit`, the response "
            "switches from a bare list to a `{data, total, page, limit, total_pages}` "
            "envelope. Default (omitted) returns the unpaginated list for backward "
            "compatibility with existing callers."
        ),
    ),
    limit: int | None = Query(
        None,
        ge=1,
        le=100,
        description=(
            "Page size when paginating. Triggers the paginated envelope shape — "
            "see `page`. Omit both to keep the historical bare-list behaviour."
        ),
    ),
    q: str | None = Query(None, description='Filter by name or API, e.g. "stripe" or "oauth"'),
    source: str | None = Query(None, description='Filter by source: "local" or "catalog"'),
):
    """Returns registered workflows (source: local) plus available catalog workflow sources
    (source: catalog) — APIs in the Jentic public catalog that have associated workflows.

    Catalog entries show the API they belong to; add credentials to auto-import their workflows.
    Use ?source=local or ?source=catalog to filter. Default returns all.

    Pass `page` + `limit` for a `{data, total, page, limit, total_pages}` envelope; omit
    both to keep the original bare-list response (workspace tiles still work the old way).
    """
    # ── Local workflows ──────────────────────────────────────────────────────
    results = []
    if source != "catalog":
        async with get_db() as db:
            async with db.execute(
                """SELECT slug, name, description, steps_count, involved_apis, created_at
                   FROM workflows ORDER BY created_at DESC"""
            ) as cur:
                rows = await cur.fetchall()
        for r in rows:
            entry = {
                "id": workflow_capability_id(r[0]),
                "url": workflow_url(r[0]),
                "slug": r[0],
                "name": r[1],
                "description": r[2],
                "steps_count": r[3],
                "involved_apis": json.loads(r[4]) if r[4] else [],
                "created_at": r[5],
                "source": "local",
                "_links": {
                    "self": f"/workflows/{r[0]}",
                    "execute": f"/workflows/{r[0]}",
                },
            }
            if q:
                qlow = q.lower()
                match = (
                    (r[1] and qlow in r[1].lower())
                    or (r[2] and qlow in r[2].lower())
                    or qlow in r[0].lower()
                    or any(qlow in a.lower() for a in (json.loads(r[4]) if r[4] else []))
                )
                if not match:
                    continue
            results.append(entry)

    # ── Catalog workflow sources (unimported) ────────────────────────────────
    if source != "local":
        wf_manifest = load_workflow_manifest()
        if wf_manifest:
            async with get_db() as db:
                async with db.execute("SELECT id FROM apis") as cur:
                    local_api_ids: set[str] = {r[0] for r in await cur.fetchall()}

            # Build vendor coverage sets (same logic as GET /apis)
            _GENERIC_SUBS = {"api", "www", "app", "web", "portal", "v1", "v2", "v3"}
            covered_sub_apis: set[str] = set()
            covered_leaf_vendors: set[str] = set()
            for local_id in local_api_ids:
                hostname = local_id.split("/")[0]
                parts = hostname.split(".")
                if len(parts) < 2:
                    continue
                vendor = ".".join(parts[-2:])
                sub = ".".join(parts[:-2]) if len(parts) > 2 else ""
                if sub and sub not in _GENERIC_SUBS:
                    covered_sub_apis.add(f"{vendor}/{sub}")
                covered_leaf_vendors.add(vendor)

            for entry in wf_manifest:
                src_id = entry["source_id"]
                api_id = entry["api_id"]

                # Skip if already covered locally (same dedup logic as GET /apis)
                if api_id in local_api_ids:
                    continue
                if "/" in api_id:
                    if api_id in covered_sub_apis:
                        continue
                else:
                    hostname = api_id.split("/")[0]
                    parts = hostname.split(".")
                    vendor = ".".join(parts[-2:]) if len(parts) >= 2 else hostname
                    if vendor in covered_leaf_vendors:
                        continue

                if q:
                    if q.lower() not in src_id.lower() and q.lower() not in api_id.lower():
                        continue

                results.append(
                    {
                        "id": f"catalog:workflows:{src_id}",
                        "url": None,
                        "slug": src_id,
                        "name": f"{api_id} (catalog)",
                        "description": (
                            f"Workflows available from the Jentic public catalog for {api_id}. "
                            f"Add credentials for this API to import them automatically."
                        ),
                        "steps_count": 0,
                        "involved_apis": [api_id],
                        "created_at": None,
                        "source": "catalog",
                        "source_id": src_id,
                        "_links": {
                            "catalog_api": f"/catalog/{api_id}",
                            "add_credentials": "/credentials",
                            "github": f"https://github.com/{GITHUB_REPO}/tree/main/{entry['path']}",
                        },
                    }
                )

    # Backward-compat: bare list when neither pagination param is supplied.
    # Existing callers (stats strip, sheet body, api-detail-view, etc.)
    # don't ask for pages and still get the historical shape.
    if page is None and limit is None:
        return results

    effective_page = page or 1
    effective_limit = limit or 20
    total = len(results)
    total_pages = max(1, (total + effective_limit - 1) // effective_limit) if total else 1
    start = (effective_page - 1) * effective_limit
    end = start + effective_limit
    return {
        "data": results[start:end],
        "total": total,
        "page": effective_page,
        "limit": effective_limit,
        "total_pages": total_pages,
    }


_WORKFLOW_CONTENT_TYPES = {
    "application/json": {
        "schema": {"type": "object", "description": "Workflow metadata (default)"}
    },
    "application/vnd.oai.workflows+json": {
        "schema": {"type": "object", "description": "Raw Arazzo document as JSON"}
    },
    "application/vnd.oai.workflows+yaml": {
        "schema": {"type": "string", "description": "Raw Arazzo document as YAML"}
    },
    "text/markdown": {"schema": {"type": "string", "description": "LLM-friendly prose summary"}},
    "text/html": {"schema": {"type": "string", "description": "Human-readable HTML visualiser"}},
}


@router.get(
    "/workflows/{slug}",
    summary="Get workflow definition — Arazzo spec and input schema",
    tags=["catalog"],
    responses={
        200: {
            "description": "Workflow definition — format controlled by Accept header.",
            "content": _WORKFLOW_CONTENT_TYPES,
        }
    },
    openapi_extra=agent_hints(
        when_to_use="Use after finding a workflow via GET /workflows or GET /search to retrieve its full definition, input schema, and step sequence. Returns Arazzo spec with content negotiation (JSON, YAML, Markdown, HTML).",
        prerequisites=[
            "Requires authentication (toolkit key or human session)",
            "Valid workflow slug (from GET /workflows or GET /search results)",
        ],
        avoid_when="Do not use to execute the workflow — use POST /workflows/{slug} via broker for execution.",
        related_operations=[
            "POST /workflows/{slug} — execute this workflow with inputs",
            "GET /inspect/{id} — get full capability details (use workflow capability ID format: POST/{host}/workflows/{slug})",
            "GET /workflows — list all workflows when you don't know the slug yet",
        ],
    ),
)
async def get_workflow(
    slug: Annotated[str, Path(description="Workflow slug (URL-safe identifier)")], request: Request
):
    """Returns the workflow definition with content negotiation:
    - application/json (default): workflow metadata with simplified step info
    - application/vnd.oai.workflows+json: raw Arazzo document as JSON
    - application/vnd.oai.workflows+yaml: raw Arazzo document as YAML
    - text/markdown: compact LLM-friendly summary with input schema and steps
    - text/html: human-readable HTML summary
    Execute via broker: POST /{jentic_host}/workflows/{slug}
    """
    accept = request.headers.get("accept", "application/json")

    async with get_db() as db:
        async with db.execute(
            "SELECT slug, name, description, arazzo_path, input_schema, steps_count, involved_apis, created_at FROM workflows WHERE slug=?",
            (slug,),
        ) as cur:
            row = await cur.fetchone()

    if not row:
        raise HTTPException(404, f"Workflow '{slug}' not found")

    (
        db_slug,
        name,
        description,
        arazzo_path,
        input_schema_str,
        steps_count,
        involved_apis_str,
        created_at,
    ) = row
    doc = parse_arazzo(arazzo_path)
    meta = _extract_workflow_meta(doc)
    involved_apis = json.loads(involved_apis_str) if involved_apis_str else []
    capability_id = workflow_capability_id(slug)

    # Formal Arazzo media types - return raw Arazzo document
    if "application/vnd.oai.workflows+json" in accept:
        return Response(
            content=json.dumps(doc, ensure_ascii=False),
            media_type="application/vnd.oai.workflows+json",
        )

    if "application/vnd.oai.workflows+yaml" in accept:
        return Response(
            content=yaml.dump(doc, default_flow_style=False, allow_unicode=True),
            media_type="application/vnd.oai.workflows+yaml",
        )

    if "text/html" in accept:
        esc = html.escape
        steps_html = ""
        for s in meta.get("steps", []):
            steps_html += f"<li><code>{esc(str(s.get('id', '')))}</code> — {esc(str(s.get('operation', '?')))}"
            if s.get("description"):
                steps_html += f"<br><small>{esc(str(s['description']))}</small>"
            steps_html += "</li>"
        apis_html = ", ".join(f"<code>{esc(a)}</code>" for a in involved_apis) or "—"
        body = f"""<!DOCTYPE html>
<html>
<head><title>{esc(name)} — Jentic Workflow</title>
<style>body{{font-family:sans-serif;max-width:800px;margin:40px auto;padding:0 20px}}
code{{background:#f4f4f4;padding:2px 6px;border-radius:3px}}
pre{{background:#f4f4f4;padding:16px;border-radius:6px;overflow:auto}}
.meta{{color:#666;font-size:.9em}}
h1 span{{color:#888;font-weight:normal;font-size:.6em;margin-left:12px}}</style>
</head>
<body>
<h1>{esc(name)} <span>workflow</span></h1>
<p class="meta">Capability ID: <code>{esc(capability_id)}</code></p>
<p>{esc(description or "")}</p>
<h2>Steps ({steps_count})</h2>
<ol>{steps_html}</ol>
<h2>APIs used</h2>
<p>{apis_html}</p>
<h2>Execute</h2>
<p>POST to <code>https://{JENTIC_PUBLIC_HOSTNAME}/workflows/{esc(slug)}</code> with your inputs and <code>X-Jentic-API-Key</code> header.</p>
<h2>Arazzo source</h2>
<pre>{esc(json.dumps(doc, indent=2)[:4000]) if doc else ""}</pre>
</body></html>"""
        return HTMLResponse(body)

    if "text/markdown" in accept:
        steps_md = "\n".join(
            f"{i + 1}. **{s['id']}** — `{s.get('operation', '?')}`{': ' + s['description'] if s.get('description') else ''}"
            for i, s in enumerate(meta.get("steps", []))
        )
        md = f"## {name}\n\n{description or ''}\n\n**Capability ID:** `{capability_id}`\n\n**Steps:**\n{steps_md}\n\n**APIs:** {', '.join(f'`{a}`' for a in involved_apis) or '—'}\n\n**Execute:** POST `{workflow_url(slug)}`"
        return Response(content=md, media_type="text/markdown")

    # Default: JSON — return structured detail
    return {
        "id": capability_id,
        "url": workflow_url(slug),
        "slug": slug,
        "name": name,
        "description": description,
        "steps": meta.get("steps", []),
        "steps_count": steps_count,
        "input_schema": json.loads(input_schema_str)
        if input_schema_str
        else meta.get("input_schema"),
        "involved_apis": involved_apis,
        "arazzo_path": arazzo_path,
        "created_at": created_at,
        "_links": {
            "self": f"/workflows/{slug}",
            "execute": f"/workflows/{slug}",
            "capability": f"/inspect/{capability_id}",
        },
    }


async def dispatch_workflow(
    slug: str,
    body_bytes: bytes,
    caller_api_key: str,
    toolkit_id: str | None,
    simulate: bool = False,
    prefer_wait: float | None = None,
    callback_url: str | None = None,
    agent_id: str | None = None,
    caller_bearer_token: str | None = None,
):
    """
    Core workflow execution logic — called by both the /workflows/{slug} route
    and the broker when it detects a request targeting the Jentic hostname.

    Parameters
    ----------
    slug          : workflow slug (from URL)
    body_bytes    : raw request body (parsed as JSON inputs dict)
    caller_api_key: the API key the caller used (forwarded to broker sub-requests)
    toolkit_id : resolved toolkit (or None for admin)
    simulate      : simulate mode — return would_send without executing
    prefer_wait   : RFC 7240 Prefer: wait=N — seconds to block before returning 202
                    0.0 = return 202 immediately; None = block indefinitely
    callback_url  : X-Jentic-Callback URL to POST result when async job completes
    """
    async with get_db() as db:
        async with db.execute(
            "SELECT arazzo_path, name FROM workflows WHERE slug=?", (slug,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"Workflow '{slug}' not found")

    arazzo_path, name = row
    doc = parse_arazzo(arazzo_path)
    workflows_list = doc.get("workflows", [])
    if not workflows_list:
        raise HTTPException(500, f"Arazzo file for '{slug}' contains no workflows")
    workflow_id = workflows_list[0].get("workflowId", slug)

    # Parse request body as inputs
    try:
        inputs = json.loads(body_bytes) if body_bytes else {}
        if not isinstance(inputs, dict):
            inputs = {}
    except Exception:
        inputs = {}

    # Apply JSON Schema defaults from the workflow's input schema.
    # arazzo-runner does not resolve $schema defaults — we must do it here.
    wf_schema = workflows_list[0].get("inputs", {}) if workflows_list else {}
    for prop, defn in wf_schema.get("properties", {}).items():
        if prop not in inputs and "default" in defn:
            inputs[prop] = defn["default"]

    is_simulate = simulate

    # ── Async dispatch decision ───────────────────────────────────────────────
    # Trigger async (return 202 + job handle) if:
    #   1. prefer_wait == 0 (client said "don't block")
    #   2. Any step in this workflow is tagged x-async: true
    # For prefer_wait > 0: we attempt sync execution with a timeout; if it
    #   doesn't complete in time we promote to async and return 202.
    should_async = (prefer_wait is not None and prefer_wait == 0.0) or workflow_has_async_steps(doc)

    if should_async or (prefer_wait is not None and prefer_wait > 0):
        # Create the job record
        job_id = await create_job(
            kind="workflow",
            slug_or_id=slug,
            toolkit_id=toolkit_id,
            inputs=inputs,
            agent_id=agent_id,
        )
        # Store callback URL if provided (http/https only — prevent SSRF)
        if callback_url:
            if not is_http_https_url(callback_url):
                raise HTTPException(400, "X-Jentic-Callback must be an http or https URL")
            async with get_db() as db:
                await db.execute(
                    "UPDATE jobs SET callback_url=? WHERE id=?",
                    (callback_url, job_id),
                )
                await db.commit()

        # The actual execution coroutine — runs in background
        async def _run_in_background():
            try:
                await update_job(job_id, status="running")
                result_response = await execute_workflow_core(
                    slug=slug,
                    name=name,
                    doc=doc,
                    workflow_id=workflow_id,
                    inputs=inputs,
                    arazzo_path=arazzo_path,
                    caller_api_key=caller_api_key,
                    toolkit_id=toolkit_id,
                    is_simulate=is_simulate,
                    trace_id=None,
                    agent_id=agent_id,
                    caller_bearer_token=caller_bearer_token,
                    job_id=job_id,
                )
                # Parse the JSONResponse to extract result
                body = json.loads(result_response.body)
                http_status = result_response.status_code
                trace_id = body.get("trace_id")

                if http_status == 200:
                    await update_job(
                        job_id,
                        status="complete",
                        result=body,
                        http_status=200,
                        trace_id=trace_id,
                    )
                elif http_status == 202:
                    # Upstream itself returned 202 — double async case
                    upstream_loc = None
                    if isinstance(body.get("outputs"), dict):
                        upstream_loc = body["outputs"].get("location") or body["outputs"].get(
                            "Location"
                        )
                    await update_job(
                        job_id,
                        status="upstream_async",
                        result=body,
                        http_status=202,
                        upstream_async=True,
                        upstream_job_url=upstream_loc,
                        trace_id=trace_id,
                    )
                else:
                    await update_job(
                        job_id,
                        status="failed",
                        error=body.get("message") or body.get("error") or "Workflow failed",
                        http_status=http_status,
                        trace_id=trace_id,
                    )
            except Exception:
                await update_job(job_id, status="failed", error=traceback.format_exc()[-800:])
            finally:
                discard_task(job_id)

        if should_async:
            # Fire and forget immediately — return 202 now
            task = asyncio.create_task(_run_in_background())
            register_task(job_id, task)
            return JSONResponse(
                status_code=202,
                headers={"Location": f"/jobs/{job_id}", "X-Jentic-Job-Id": job_id},
                content={
                    "status": "running",
                    "job_id": job_id,
                    "_links": {"poll": f"/jobs/{job_id}"},
                    "message": "Workflow dispatched asynchronously. Poll _links.poll for completion.",
                },
            )
        else:
            # prefer_wait > 0: attempt sync with timeout, promote to async if it expires
            try:
                coro = _run_in_background()
                task = asyncio.create_task(coro)
                register_task(job_id, task)
                # Block for up to prefer_wait seconds
                await asyncio.wait_for(asyncio.shield(task), timeout=prefer_wait)
                # Completed within timeout — fetch result and return synchronously
                job = await get_job(job_id)
                if job and job["status"] == "complete":
                    return JSONResponse(
                        status_code=200,
                        content=job.get("result") or {},
                    )
                elif job and job["status"] == "failed":
                    return JSONResponse(
                        status_code=job.get("http_status") or 502,
                        content={"error": job.get("error")},
                    )
                # If still somehow running, fall through to 202
            except asyncio.TimeoutError:
                pass  # Promote to async below
            return JSONResponse(
                status_code=202,
                headers={"Location": f"/jobs/{job_id}", "X-Jentic-Job-Id": job_id},
                content={
                    "status": "running",
                    "job_id": job_id,
                    "_links": {"poll": f"/jobs/{job_id}"},
                    "message": f"Workflow did not complete within {prefer_wait}s. Poll _links.poll for completion.",
                },
            )

    # ── Synchronous execution path (no Prefer: wait header) ──────────────────
    return await execute_workflow_core(
        slug=slug,
        name=name,
        doc=doc,
        workflow_id=workflow_id,
        inputs=inputs,
        arazzo_path=arazzo_path,
        caller_api_key=caller_api_key,
        toolkit_id=toolkit_id,
        is_simulate=is_simulate,
        trace_id=None,
        agent_id=agent_id,
        caller_bearer_token=caller_bearer_token,
    )


async def execute_workflow_core(
    *,
    slug: str,
    name: str,
    doc: dict,
    workflow_id: str,
    inputs: dict,
    arazzo_path: str,
    caller_api_key: str,
    toolkit_id: str | None,
    is_simulate: bool,
    trace_id: str | None,
    agent_id: str | None = None,
    caller_bearer_token: str | None = None,
    job_id: str | None = None,
):
    if not trace_id:
        trace_id = new_trace_id()

    # Rewrite all sourceDescription spec servers to go through the local broker
    # (http://localhost:{port}/{host}). The arazzo-runner calls the broker
    # instead of upstream directly; the broker injects credentials from the
    # toolkit. No credential env vars needed in the subprocess — broker handles
    # it all.
    _internal_port = int(os.environ.get("JENTIC_INTERNAL_PORT", "8900"))
    _BROKER_BASE = f"http://localhost:{_internal_port}"
    temp_arazzo, temp_specs = _preprocess_arazzo_for_broker(arazzo_path, _BROKER_BASE)

    # Extract the API key the caller used — pass it to every broker request so
    # the broker can look up the right toolkit's credentials.
    # caller_api_key passed in as parameter

    # Mint the workflow's own trace_id before building the runner script so
    # we can stamp every child broker call with X-Jentic-Parent-Trace. The
    # broker reads this header (loopback-only) and writes parent_trace_id on
    # the child trace, enabling "part of workflow X" attribution in the UI.
    workflow_trace_id = new_trace_id()

    script = f"""
from arazzo_runner import ArazzoRunner
import os
import requests
import json

session = requests.Session()
_bearer = os.environ.get("_JENTIC_BEARER", "").strip()
if _bearer:
    session.headers["Authorization"] = "Bearer " + _bearer
else:
    session.headers["X-Jentic-API-Key"] = os.environ["_JENTIC_CALLER_KEY"]
session.headers["X-Jentic-Parent-Trace"] = {repr(workflow_trace_id)}
runner = ArazzoRunner.from_arazzo_path({repr(temp_arazzo)}, http_client=session)
result = runner.execute_workflow({repr(workflow_id)}, {repr(inputs)})
if hasattr(result, '__dataclass_fields__') or hasattr(result, '__dict__'):
    out = {{
        'status': str(result.status),
        'workflow_id': result.workflow_id,
        'outputs': result.outputs,
        'step_outputs': result.step_outputs,
        'inputs': result.inputs,
        'error': result.error,
    }}
else:
    out = result
print(json.dumps(out, default=str))
"""
    trace_id = workflow_trace_id
    env = dict(os.environ)
    env["_JENTIC_CALLER_KEY"] = caller_api_key or ""
    if caller_bearer_token:
        env["_JENTIC_BEARER"] = caller_bearer_token
    elif "_JENTIC_BEARER" in env:
        del env["_JENTIC_BEARER"]
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()

    # Clean up temp files
    for p in [temp_arazzo, *temp_specs]:
        try:
            os.unlink(p)
        except Exception:
            pass
    duration_ms = int((time.monotonic() - t0) * 1000)

    try:
        result_data = json.loads(stdout.decode())
    except Exception:
        result_data = {"status": "error", "error": stdout.decode() or "No output from runner"}

    wf_status = result_data.get("status", "error") if isinstance(result_data, dict) else "error"
    is_success = wf_status in ("workflow_complete", "completed", "success")

    # ── Extract failure detail from step_outputs ──────────────────────────────
    failed_step: dict | None = None
    inferred_http_status: int | None = None
    step_outputs = result_data.get("step_outputs") if isinstance(result_data, dict) else None

    # Build a step_id → Arazzo step definition map for enrichment
    wf_doc = doc.get("workflows", [{}])[0]
    arazzo_steps: dict[str, dict] = {s.get("stepId", ""): s for s in wf_doc.get("steps", [])}

    # Build operationId → API host map from sourceDescriptions + spec files
    # Arazzo operationIds may be bare (resolved via single source) or prefixed (source.operationId)
    _op_to_api: dict[str, str] = {}
    for src in doc.get("sourceDescriptions", []):
        src_url = src.get("url", "")
        # Only resolve local file paths we can read
        if src_url.startswith("/"):
            try:
                with open(src_url) as _f:
                    _spec = json.load(_f)
                _servers = _spec.get("servers", [])
                _host = _servers[0]["url"] if _servers else ""
                # Strip scheme for display
                _host_display = _host.replace("https://", "").replace("http://", "").rstrip("/")
                # Index all operationIds from this spec
                for _path, _path_item in _spec.get("paths", {}).items():
                    for _method, _op in _path_item.items():
                        if _method in (
                            "get",
                            "post",
                            "put",
                            "patch",
                            "delete",
                            "head",
                            "options",
                        ) and isinstance(_op, dict):
                            _op_id = _op.get("operationId")
                            if _op_id:
                                _op_to_api[_op_id] = _host_display
                                # Also index with source prefix (source.operationId)
                                _op_to_api[f"{src['name']}.{_op_id}"] = _host_display
            except Exception:
                pass

    if not is_success and step_outputs:
        for step_id, step_data in step_outputs.items():
            if isinstance(step_data, dict):
                err_ctx = step_data.get("runner_error_context")
                if isinstance(err_ctx, dict) and err_ctx.get("http_code"):
                    inferred_http_status = err_ctx["http_code"]

                    # Enrich with what the step was calling
                    arazzo_step = arazzo_steps.get(step_id, {})
                    operation = (
                        arazzo_step.get("operationId")
                        or arazzo_step.get("operationPath")
                        or arazzo_step.get("workflowId")
                        or "unknown operation"
                    )
                    api_host = _op_to_api.get(operation, "")
                    step_description = arazzo_step.get("description", "")
                    upstream_error = err_ctx.get("http_response", {})
                    upstream_msg = (
                        upstream_error.get("message") or (upstream_error.get("errors") or [""])[0]
                        if isinstance(upstream_error, dict)
                        else str(upstream_error)
                    )

                    # Synthesise the top-level message (not repeated inside failed_step)
                    _step_summary = (
                        f"Step '{step_id}' failed calling {operation}"
                        + (f" on {api_host}" if api_host else "")
                        + (f" — {step_description}" if step_description else "")
                        + f" (HTTP {inferred_http_status}"
                        + (f": {upstream_msg}" if upstream_msg else "")
                        + ")"
                    )
                    failed_step = {
                        "step_id": step_id,
                        "operation": operation,
                        "api": api_host or None,
                        "http_status": inferred_http_status,
                        # summary intentionally omitted — duplicated in top-level message
                        "detail": err_ctx.get("http_response"),
                    }
                    # Stash the summary for top-level message assembly
                    failed_step["_summary"] = _step_summary
                    break

    # ── Write trace ───────────────────────────────────────────────────────────
    wf_outputs = result_data.get("outputs") if isinstance(result_data, dict) else None
    await write_trace(
        trace_id=trace_id,
        toolkit_id=toolkit_id,
        agent_id=agent_id,
        operation_id=None,
        workflow_id=workflow_id,
        spec_path=arazzo_path,
        status=wf_status,
        http_status=inferred_http_status or (200 if is_success else 502),
        duration_ms=duration_ms,
        error=result_data.get("error") if isinstance(result_data, dict) else str(result_data),
        step_outputs=step_outputs,
        arazzo_steps=arazzo_steps,
        inputs=inputs if isinstance(inputs, dict) else None,
        outputs=wf_outputs if isinstance(wf_outputs, dict) else None,
        job_id=job_id,
    )

    response_headers = {"X-Jentic-Trace-Id": trace_id}

    # ── Success ───────────────────────────────────────────────────────────────
    if is_success:
        return JSONResponse(
            status_code=200,
            headers=response_headers,
            content={
                "workflow": name,
                "slug": slug,
                "status": wf_status,
                "outputs": result_data.get("outputs")
                if isinstance(result_data, dict)
                else result_data,
                "simulate": is_simulate,
                "trace_id": trace_id,
                "_links": {"trace": f"/traces/{trace_id}"},
            },
        )

    # ── Failure — propagate HTTP status, return parseable detail ─────────────
    # Use the upstream HTTP status if we got one; otherwise 502 Bad Gateway.
    http_code = inferred_http_status or 502
    wf_error = result_data.get("error") if isinstance(result_data, dict) else str(result_data)

    # Pull the step summary out of the private stash, strip it from the public failed_step
    step_summary: str | None = None
    if failed_step:
        step_summary = failed_step.pop("_summary", None)

    detail: dict = {
        "error": "workflow_execution_failed",
        "workflow": name,
        "slug": slug,
        "workflow_status": wf_status,
        "message": step_summary or wf_error or "Workflow execution failed",
        "trace_id": trace_id,
        "_links": {"trace": f"/traces/{trace_id}"},
    }
    if failed_step:
        detail["failed_step"] = failed_step

    # ── Remediation hint — only for logic/drift failures, not simple auth errors ──
    # 401/403 = missing or wrong credentials (client already has the api + error_type)
    # 429 = rate limit (client knows to back off)
    # Anything else (400, 422, 5xx, unknown) may be workflow logic errors or API drift —
    # advise the client to read the workflow, execute manually, and submit a repair.
    _auth_codes = {401, 403, 429}
    if http_code not in _auth_codes:
        detail["remediation"] = {
            "message": (
                "This failure may be caused by a workflow logic error or API drift. "
                "Read the workflow definition, execute it step-by-step manually, "
                "then POST a repaired workflow as a note to help improve the catalog."
            ),
            "_links": {
                "workflow_definition": f"/workflows/{slug}",
                "post_note": f"/workflows/{slug}/notes",
            },
        }

    return JSONResponse(
        status_code=http_code,
        headers=response_headers,
        content=detail,
    )


@router.delete(
    "/workflows/{slug}",
    status_code=204,
    summary="Delete a workflow from the workspace",
    tags=["catalog"],
)
async def delete_workflow(
    slug: Annotated[str, Path(description="Workflow slug to delete")],
):
    """Permanently delete a workflow and its Arazzo file from the workspace."""
    async with get_db() as db:
        async with db.execute("SELECT arazzo_path FROM workflows WHERE slug=?", (slug,)) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "Workflow not found")
        arazzo_path = row[0]

        await db.execute("DELETE FROM workflows WHERE slug=?", (slug,))
        await db.commit()

    if arazzo_path:
        try:
            pathlib.Path(arazzo_path).unlink(missing_ok=True)
        except OSError:
            pass

    # Rebuild BM25 index (no per-entry removal supported)
    from src.routers.apis import rebuild_index  # noqa: PLC0415

    await rebuild_index()


@router.post(
    "/workflows/{slug}", summary="Execute workflow", tags=["execute"], include_in_schema=False
)
async def execute_workflow_by_slug(slug: str, request: Request):
    """Execute a workflow by its slug.

    Thin wrapper around dispatch_workflow() — the same core logic is called
    by the broker when a POST targets /{jentic_hostname}/workflows/{slug}.
    Body is the workflow inputs dict (JSON).
    Supports Prefer: wait=N (RFC 7240) and X-Jentic-Callback.
    """
    body_bytes = await request.body()
    caller_api_key = request.headers.get("x-jentic-api-key") or ""
    _auth = request.headers.get("Authorization", "")
    caller_bearer = None
    if _auth.lower().startswith("bearer "):
        _tok = _auth[7:].strip()
        if _tok.startswith("at_"):
            caller_bearer = _tok
    toolkit_id = getattr(request.state, "toolkit_id", None)
    simulate = getattr(request.state, "simulate", False)
    prefer_wait = parse_prefer_wait(request.headers.get("prefer"))
    callback_url = request.headers.get("x-jentic-callback")
    return await dispatch_workflow(
        slug,
        body_bytes,
        caller_api_key,
        toolkit_id,
        simulate,
        prefer_wait=prefer_wait,
        callback_url=callback_url,
        agent_id=getattr(request.state, "agent_client_id", None),
        caller_bearer_token=caller_bearer,
    )


# ── Startup backfill ──────────────────────────────────────────────────────────


async def backfill_workflow_involved_apis() -> None:
    """Fix workflows imported from the catalog that have empty involved_apis.

    Catalog workflows saved via lazy_import_catalog_workflows before the
    parent_api_id fix have involved_apis=[] because the Arazzo-native step
    references couldn't be parsed by the capability-ID regex. This backfill
    looks at the arazzo_path naming convention (catalog_{safe_id}_{slug}.json)
    and cross-references with the apis table to fill in the correct api_id.
    """
    import logging  # noqa: PLC0415
    import re  # noqa: PLC0415

    log = logging.getLogger("jentic.backfill")

    async with get_db() as db:
        # Find workflows with empty involved_apis that look like catalog imports
        async with db.execute(
            """SELECT slug, arazzo_path, involved_apis FROM workflows
               WHERE arazzo_path LIKE '%catalog_%'"""
        ) as cur:
            rows = await cur.fetchall()

        if not rows:
            return

        # Get all known API ids for matching
        async with db.execute("SELECT id FROM apis") as cur:
            api_ids = {r[0] for r in await cur.fetchall()}

        updated = 0
        for slug, arazzo_path, involved_apis_json in rows:
            existing = json.loads(involved_apis_json) if involved_apis_json else []
            if existing:
                continue

            # Extract the safe_id from the filename: catalog_{safe_id}_{slug}.json
            # safe_id was created with: re.sub(r"[^a-z0-9_-]", "_", source_id.lower())
            # source_id was: api_id.replace("/", "~", 1)
            filename = pathlib.Path(arazzo_path).stem
            # Remove "catalog_" prefix
            if not filename.startswith("catalog_"):
                continue
            remainder = filename[len("catalog_") :]

            # Try to match against known api_ids
            matched_api = None
            for api_id in api_ids:
                # Convert api_id to the safe format used in the filename
                source_id = api_id.replace("/", "~", 1)
                safe = re.sub(r"[^a-z0-9_-]", "_", source_id.lower())
                if remainder.startswith(safe + "_") or remainder == safe:
                    matched_api = api_id
                    break

            if matched_api:
                await db.execute(
                    "UPDATE workflows SET involved_apis=? WHERE slug=?",
                    (json.dumps([matched_api]), slug),
                )
                updated += 1

        if updated:
            await db.commit()
            log.info("Backfilled involved_apis for %d workflow(s)", updated)
