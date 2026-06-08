"""
Jobs — async execution handles (RFC 7240).

A job is created only when a call cannot complete synchronously:
  - Client sends Prefer: wait=0 (don't block at all)
  - Client sends Prefer: wait=N and execution exceeds N seconds (auto-promoted)
  - Upstream API itself returns 202 (surfaced as upstream_async)

On a 202 response, Jentic Mini returns:
  Location: /jobs/{job_id}       ← RFC 7240 standard polling URL
  X-Jentic-Job-Id: job_xxx       ← raw ID for convenience (no URL parsing needed)

Job lifecycle:
  pending  → running  → complete | failed | upstream_async

  "upstream_async" means the upstream API itself returned 202 — Jentic Mini's job is
  technically done but the real work is still happening on the remote service.
  The job result includes upstream_job_url so the agent knows where to poll.

All calls (sync and async) also produce a trace. A completed job references
its trace via trace_id — jobs and traces coexist, jobs don't become traces.

GET /jobs          — list async jobs (paginated, filterable by status)
GET /jobs/{id}     — poll an async job for completion and retrieve its result
DELETE /jobs/{id}  — cancel an outstanding async job (best-effort; record retained)
"""

import asyncio
import json
import time
import uuid
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, HTTPException, Path, Query, Request

from src.db import get_db
from src.models import JobListPage, JobOut
from src.openapi_helpers import agent_hints


router = APIRouter(prefix="/jobs", tags=["observe"])

# In-memory registry of running background tasks so we can cancel them
_running_tasks: dict[str, asyncio.Task] = {}


def _like_escape(term: str) -> str:
    r"""Escape SQL ``LIKE`` metacharacters in a user-supplied search term.

    ``%`` and ``_`` are wildcards in ``LIKE``; without escaping, a search for
    ``100%`` or ``a_b`` silently turns into a wildcard match (and ``q=%`` would
    match every row). We backslash-escape ``\``, ``%`` and ``_`` so the term is
    matched literally — callers MUST pair this with an ``ESCAPE '\'`` clause.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _jobs_scope_clause(request: Request, prefix: str = "") -> tuple[str, list]:
    """Return (sql_predicate, params) restricting job reads to the caller's tenant.

    Mirrors `_trace_scope_clause` in `routers/traces.py`. Same rules:
      - Human admin sessions see every job.
      - Agent OAuth callers see only jobs stamped with their agent_id.
      - Toolkit-key callers see every job tagged with their toolkit, regardless
        of agent_id (operator-credential semantics, see traces helper docstring).
      - Anonymous callers fail closed (the auth middleware rejects them earlier,
        but if a future code path forgets to set state we don't want every job
        listed).

    `prefix` lets callers qualify column refs when joining other tables.
    Migration 0007 added a partial index on `jobs(agent_id, created_at)`
    keyed for the agent-scoped read. The toolkit-key fallback falls back
    to a scan, but the jobs table is bounded by the active workload —
    pre-emptive optimisation isn't worth a second index until profiling
    says otherwise.
    """
    if getattr(request.state, "is_admin", False):
        return "1=1", []
    agent_id = getattr(request.state, "agent_client_id", None)
    if agent_id:
        return f"{prefix}agent_id = ?", [agent_id]
    toolkit_id = getattr(request.state, "toolkit_id", None)
    if toolkit_id:
        return f"{prefix}toolkit_id = ?", [toolkit_id]
    return "0=1", []


def register_task(job_id: str, task: asyncio.Task) -> None:
    _running_tasks[job_id] = task


def discard_task(job_id: str) -> None:
    _running_tasks.pop(job_id, None)


def cancel_task(job_id: str) -> None:
    task = _running_tasks.pop(job_id, None)
    if task and not task.done():
        task.cancel()


# ── DB helpers ─────────────────────────────────────────────────────────────────


async def create_job(
    *,
    kind: str,  # "workflow" | "broker"
    slug_or_id: str,  # workflow slug or capability ID
    toolkit_id: str | None,
    inputs: dict,
    agent_id: str | None = None,
) -> str:
    """Create a job record and return its ID.

    `agent_id` is the calling agent's `client_id` for OAuth callers (`at_…`),
    or None for toolkit-key callers / admin-initiated jobs. Stamped at INSERT
    so the column is set even if the job never reaches `update_job`.
    """
    job_id = "job_" + uuid.uuid4().hex[:12]
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO jobs (id, kind, slug_or_id, toolkit_id, agent_id, inputs, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (job_id, kind, slug_or_id, toolkit_id, agent_id, json.dumps(inputs), time.time()),
        )
        await db.commit()
    return job_id


async def update_job(
    job_id: str,
    *,
    status: str,
    result: Any = None,
    error: str | None = None,
    http_status: int | None = None,
    upstream_async: bool = False,
    upstream_job_url: str | None = None,
    trace_id: str | None = None,
) -> None:
    """Update job status and result."""
    async with get_db() as db:
        await db.execute(
            """
            UPDATE jobs
            SET status=?, result=?, error=?, http_status=?,
                upstream_async=?, upstream_job_url=?, trace_id=?,
                completed_at=?
            WHERE id=?
            """,
            (
                status,
                json.dumps(result) if result is not None else None,
                error,
                http_status,
                1 if upstream_async else 0,
                upstream_job_url,
                trace_id,
                time.time() if status in ("complete", "failed", "upstream_async") else None,
                job_id,
            ),
        )
        await db.commit()
    # Fire callback if registered
    await _fire_callback(job_id)


async def _fire_callback(job_id: str) -> None:
    """POST the job result to X-Jentic-Callback URL if one was registered."""
    try:
        async with get_db() as db:
            async with db.execute(
                "SELECT callback_url, status, result, error FROM jobs WHERE id=?", (job_id,)
            ) as cur:
                row = await cur.fetchone()
        if not row or not row[0] or row[1] not in ("complete", "failed", "upstream_async"):
            return
        callback_url, status, result_json, error = row
        payload = {"job_id": job_id, "status": status}
        if result_json:
            payload["result"] = json.loads(result_json)
        if error:
            payload["error"] = error
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(callback_url, json=payload)
    except Exception:
        pass  # Callback failure is non-fatal


async def get_job(job_id: str) -> dict | None:
    """Return job row as dict, or None if not found."""
    async with get_db() as db:
        async with db.execute(
            "SELECT id, kind, slug_or_id, toolkit_id, agent_id, status, result, error, "
            "http_status, upstream_async, upstream_job_url, trace_id, inputs, "
            "created_at, completed_at, callback_url, "
            # Correlated lookup so a child broker job (one whose owning trace
            # has parent_trace_id set) can render "part of workflow X" in the
            # Job drawer without a second round-trip. NULL when the job has
            # no trace yet (pending) or the trace is top-level.
            "(SELECT parent_trace_id FROM executions WHERE id = jobs.trace_id) AS parent_trace_id "
            "FROM jobs WHERE id=?",
            (job_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    keys = [
        "id",
        "kind",
        "slug_or_id",
        "toolkit_id",
        "agent_id",
        "status",
        "result",
        "error",
        "http_status",
        "upstream_async",
        "upstream_job_url",
        "trace_id",
        "inputs",
        "created_at",
        "completed_at",
        "callback_url",
        "parent_trace_id",
    ]
    d = dict(zip(keys, row))
    if d.get("result"):
        try:
            d["result"] = json.loads(d["result"])
        except Exception:
            pass
    if d.get("inputs"):
        try:
            d["inputs"] = json.loads(d["inputs"])
        except Exception:
            pass
    d["upstream_async"] = bool(d.get("upstream_async"))
    return d


def _job_response(d: dict) -> dict:
    """Serialise a job row into the public API shape."""
    out = {
        "job_id": d["id"],
        "status": d["status"],
        "kind": d["kind"],
        "capability": d["slug_or_id"],
        "created_at": d["created_at"],
    }
    if d.get("toolkit_id") is not None:
        out["toolkit_id"] = d["toolkit_id"]
    if d.get("agent_id") is not None:
        out["agent_id"] = d["agent_id"]
    if d.get("completed_at"):
        out["completed_at"] = d["completed_at"]
    if d["status"] in ("complete", "upstream_async"):
        out["result"] = d.get("result")
    if d["status"] == "failed":
        out["error"] = d.get("error")
        if d.get("http_status"):
            out["http_status"] = d["http_status"]
    if d.get("upstream_async"):
        out["upstream_async"] = True
        if d.get("upstream_job_url"):
            out["upstream_job_url"] = d["upstream_job_url"]
    if d.get("trace_id"):
        out["trace_id"] = d["trace_id"]
        out["_links"] = {
            "self": f"/jobs/{d['id']}",
            "trace": f"/traces/{d['trace_id']}",
        }
    else:
        out["_links"] = {"self": f"/jobs/{d['id']}"}
    if d.get("parent_trace_id"):
        out["parent_trace_id"] = d["parent_trace_id"]
    return out


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get(
    "",
    summary="List async jobs — paginated handles for outstanding and completed async calls",
    response_model=JobListPage,
    description=(
        "Returns async jobs only — calls that could not complete synchronously. "
        "Sync calls produce traces but no jobs. "
        "Filter by `status` (pending|running|complete|failed|upstream_async). "
        "Poll `GET /jobs/{id}` for individual job status."
    ),
)
async def list_jobs(
    request: Request,
    status: Annotated[
        str | None,
        Query(
            description=(
                "Filter by status. Accepts a single value or a comma-separated set "
                "(e.g. `pending,running` for in-flight only). Whitespace tolerated."
            )
        ),
    ] = None,
    kind: Annotated[
        str | None,
        Query(
            description=(
                "Filter by job kind: `workflow` (multi-step Arazzo runs) or `broker` "
                "(individual API calls dispatched async). Used by the Monitor Jobs tab "
                "to split workflow runs from broker calls in separate views."
            ),
            pattern="^(workflow|broker)$",
        ),
    ] = None,
    page: Annotated[int, Query(description="Page number (1-indexed)", ge=1)] = 1,
    limit: Annotated[int, Query(description="Results per page (1-100)", ge=1, le=100)] = 20,
    toolkit_id: Annotated[
        str | None, Query(description="Filter by toolkit id (exact match)")
    ] = None,
    agent_id: Annotated[
        str | None,
        Query(description="Filter by agent client_id (exact match)."),
    ] = None,
    since: Annotated[
        float | None,
        Query(description="Lower bound on `created_at` (unix seconds, inclusive)", ge=0),
    ] = None,
    until: Annotated[
        float | None,
        Query(description="Upper bound on `created_at` (unix seconds, exclusive)", ge=0),
    ] = None,
    q: Annotated[
        str | None,
        Query(
            description=(
                "Free-text substring match (case-insensitive) across the columns the "
                "Jobs tab row renders: `slug_or_id` (workflow slug or broker capability "
                "id), `agent_id`, `toolkit_id`, and `upstream_job_url` (so a search for "
                "the upstream provider's job-id snippet still finds the right row). "
                "Empty/whitespace strings are treated as not set so the no-filter plan "
                "stays cheap. None of those columns are indexed for prefix lookups, so "
                "`q` always implies a scan over the rows that the tenant + time-window "
                "clauses already select — fine for the Monitor page (24h default) but "
                "don't use it as a general-purpose search."
            ),
            min_length=1,
            max_length=200,
        ),
    ] = None,
):
    offset = (page - 1) * limit

    # Tenant scoping first — the rest of the WHERE layers user-supplied
    # filters on top. Anonymous principals fail closed (`0=1`).
    scope_sql, scope_params = _jobs_scope_clause(request)
    where_parts: list[str] = [scope_sql]
    params: list = list(scope_params)
    if status:
        # Comma-list → IN(?,?,...). The Monitor Jobs tab sends e.g.
        # `status=pending,running` to display only in-flight jobs; previously
        # only single-value status was supported.
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if len(statuses) == 1:
            where_parts.append("status = ?")
            params.append(statuses[0])
        elif statuses:
            placeholders = ",".join("?" * len(statuses))
            where_parts.append(f"status IN ({placeholders})")
            params.extend(statuses)
    if kind is not None:
        where_parts.append("kind = ?")
        params.append(kind)
    if toolkit_id is not None:
        where_parts.append("toolkit_id = ?")
        params.append(toolkit_id)
    if agent_id is not None:
        where_parts.append("agent_id = ?")
        params.append(agent_id)
    if since is not None:
        where_parts.append("created_at >= ?")
        params.append(since)
    if until is not None:
        where_parts.append("created_at < ?")
        params.append(until)
    if q is not None and q.strip():
        # Substring match across the four columns the Jobs row renders. The
        # OR list mirrors the column order of the table so a typed token
        # like "stripe" matches whether it landed in slug_or_id, the agent
        # client_id, toolkit, or the upstream provider URL. See the docstring
        # on the parameter for the cost story.
        like = f"%{_like_escape(q.strip())}%"
        where_parts.append(
            "(slug_or_id LIKE ? ESCAPE '\\' OR agent_id LIKE ? ESCAPE '\\' "
            "OR toolkit_id LIKE ? ESCAPE '\\' OR upstream_job_url LIKE ? ESCAPE '\\')"
        )
        params.extend([like, like, like, like])

    # `where_parts` always contains at least the scope predicate, so the
    # WHERE clause is unconditional now.
    where_sql = " WHERE " + " AND ".join(where_parts)
    page_params = [*params, limit, offset]

    async with get_db() as db:
        async with db.execute(f"SELECT COUNT(*) FROM jobs{where_sql}", params) as cur:
            total = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT id, kind, slug_or_id, toolkit_id, agent_id, status, result, error, "
            "http_status, upstream_async, upstream_job_url, trace_id, inputs, "
            "created_at, completed_at, callback_url, "
            # See `get_job` — correlated lookup avoids a JOIN that would
            # collide with the unqualified column names in `where_sql`.
            "(SELECT parent_trace_id FROM executions WHERE id = jobs.trace_id) AS parent_trace_id "
            f"FROM jobs{where_sql} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            page_params,
        ) as cur:
            rows = await cur.fetchall()

    keys = [
        "id",
        "kind",
        "slug_or_id",
        "toolkit_id",
        "agent_id",
        "status",
        "result",
        "error",
        "http_status",
        "upstream_async",
        "upstream_job_url",
        "trace_id",
        "inputs",
        "created_at",
        "completed_at",
        "callback_url",
        "parent_trace_id",
    ]
    items = []
    for row in rows:
        d = dict(zip(keys, row))
        d["upstream_async"] = bool(d.get("upstream_async"))
        if d.get("result"):
            try:
                d["result"] = json.loads(d["result"])
            except Exception:
                pass
        items.append(_job_response(d))

    total_pages = max(1, (total + limit - 1) // limit)
    return {
        "data": items,
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "has_more": page < total_pages,
        "_links": {
            "self": f"/jobs?page={page}&limit={limit}",
            **({"next": f"/jobs?page={page + 1}&limit={limit}"} if page < total_pages else {}),
            **({"prev": f"/jobs?page={page - 1}&limit={limit}"} if page > 1 else {}),
        },
    }


@router.get(
    "/{job_id}",
    summary="Poll async job — check status and retrieve result when complete",
    response_model=JobOut,
    description=(
        "Poll this endpoint after receiving a 202. The job_id comes from the "
        "`Location` response header (RFC 7240) or the `X-Jentic-Job-Id` header. "
        "Returns `status: pending|running` while in progress. "
        "Returns `status: complete` with `result` when done. "
        "Returns `status: upstream_async` when the upstream API itself returned 202 — "
        "check `upstream_job_url` to follow the upstream job. "
        "Returns `status: failed` with `error` and `http_status` on failure."
    ),
    openapi_extra=agent_hints(
        when_to_use="Use after receiving HTTP 202 from a broker call or workflow execution to poll for completion. Job ID comes from Location header (RFC 7240) or X-Jentic-Job-Id header. Poll until status is complete, failed, or upstream_async. Jobs are created when: (1) client sends Prefer: wait=0, (2) execution exceeds Prefer: wait=N timeout, or (3) upstream API returns 202.",
        prerequisites=[
            "Requires authentication (toolkit key or human session)",
            "Valid job ID from a 202 response (format: job_{12chars})",
        ],
        avoid_when="Do not use for synchronous calls (200 responses) — those produce traces, not jobs. Do not poll excessively — implement exponential backoff (start at 1s, max 30s).",
        related_operations=[
            "GET /{target} (broker) — broker call with Prefer: wait=0 returns 202 + job ID",
            "POST /workflows/{slug} — workflow with Prefer: wait=0 returns 202 + job ID",
            "GET /traces/{id} — completed jobs reference a trace via trace_id field",
            "DELETE /jobs/{id} — cancel an outstanding async job",
        ],
    ),
)
async def get_job_route(
    request: Request,
    job_id: Annotated[str, Path(description="Job ID (format: job_{12chars})")],
):
    d = await get_job(job_id)
    if not d:
        raise HTTPException(404, f"Job '{job_id}' not found")
    # Tenant scoping: pretend non-owners don't see the row at all (404, not 403),
    # mirroring the pattern in /traces/{id} — leaks no existence information.
    if not getattr(request.state, "is_admin", False):
        agent_id = getattr(request.state, "agent_client_id", None)
        toolkit_id = getattr(request.state, "toolkit_id", None)
        if agent_id:
            if d.get("agent_id") != agent_id:
                raise HTTPException(404, f"Job '{job_id}' not found")
        elif toolkit_id:
            if d.get("toolkit_id") != toolkit_id:
                raise HTTPException(404, f"Job '{job_id}' not found")
        else:
            # Anonymous-with-state-set is unexpected; fail closed.
            raise HTTPException(404, f"Job '{job_id}' not found")
    return _job_response(d)


@router.delete(
    "/{job_id}",
    status_code=204,
    summary="Cancel async job — best-effort cancellation of an outstanding job",
    description=(
        "Requests cancellation of a pending or running async job. "
        "Best-effort: cancellation fires at the next async checkpoint; "
        "an in-flight upstream HTTP request will complete before the job stops. "
        "The job record is retained (marked failed, error='Cancelled by client'). "
        "Has no effect on already-completed jobs."
    ),
)
async def cancel_job(
    request: Request,
    job_id: Annotated[str, Path(description="Job ID to cancel")],
):
    d = await get_job(job_id)
    if not d:
        raise HTTPException(404, f"Job '{job_id}' not found")
    # Same tenant-scoping pattern as GET /jobs/{id}: non-owners see 404.
    # Without this, any authenticated caller could cancel any other tenant's
    # in-flight jobs by guessing job_ids.
    if not getattr(request.state, "is_admin", False):
        agent_id = getattr(request.state, "agent_client_id", None)
        toolkit_id = getattr(request.state, "toolkit_id", None)
        if agent_id:
            if d.get("agent_id") != agent_id:
                raise HTTPException(404, f"Job '{job_id}' not found")
        elif toolkit_id:
            if d.get("toolkit_id") != toolkit_id:
                raise HTTPException(404, f"Job '{job_id}' not found")
        else:
            raise HTTPException(404, f"Job '{job_id}' not found")
    if d["status"] in ("complete", "failed", "upstream_async"):
        return  # already done, 204 is fine
    cancel_task(job_id)
    await update_job(job_id, status="failed", error="Cancelled by client")
