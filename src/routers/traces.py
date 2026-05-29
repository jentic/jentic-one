"""
Execution trace endpoints.

Every execution (operation or workflow) writes a trace record.
Callers can look up trace details — especially useful when a workflow fails,
to inspect step-by-step results and see which step caused the error.

Routes:
  GET /traces             list recent execution traces (paginated)
  GET /traces/{id}        full trace with step-level detail
"""

import json
import logging
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request

from src.db import get_db
from src.models import TraceListPage, TraceOut, UsageResponse
from src.openapi_helpers import agent_hints


router = APIRouter()
log = logging.getLogger("jentic.traces")


# ── DB helpers ────────────────────────────────────────────────────────────────


async def write_trace(
    *,
    trace_id: str,
    toolkit_id: str | None,
    operation_id: str | None,
    workflow_id: str | None,
    spec_path: str | None,
    status: str,
    http_status: int | None,
    duration_ms: int | None,
    error: str | None,
    step_outputs: dict | None = None,
    agent_id: str | None = None,
) -> str:
    """Write an execution trace (+ optional step records) to the DB.

    Uses UPSERT to preserve created_at on updates (e.g. async pending → success).
    """
    async with get_db() as db:
        await db.execute(
            """INSERT INTO executions
               (id, toolkit_id, agent_id, operation_id, workflow_id, spec_path,
                status, http_status, duration_ms, error, created_at, completed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,unixepoch(),unixepoch())
               ON CONFLICT(id) DO UPDATE SET
                 status=excluded.status,
                 http_status=excluded.http_status,
                 duration_ms=excluded.duration_ms,
                 error=excluded.error,
                 completed_at=unixepoch()""",
            (
                trace_id,
                toolkit_id,
                agent_id,
                operation_id,
                workflow_id,
                spec_path,
                status,
                http_status,
                duration_ms,
                error,
            ),
        )

        if step_outputs:
            for step_id, step_data in step_outputs.items():
                err_ctx = (
                    step_data.get("runner_error_context") if isinstance(step_data, dict) else None
                )
                step_http = err_ctx.get("http_code") if isinstance(err_ctx, dict) else None
                step_err = step_data.get("error") if isinstance(step_data, dict) else None
                await db.execute(
                    """INSERT INTO execution_steps
                       (id, execution_id, step_id, http_status, output, error)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()),
                        trace_id,
                        step_id,
                        step_http,
                        json.dumps(step_data),
                        step_err,
                    ),
                )
        await db.commit()
    return trace_id


def new_trace_id() -> str:
    return "exec_" + uuid.uuid4().hex[:12]


async def safe_write_trace(**kwargs) -> None:
    """Safe trace writer. Prevents trace failures from affecting responses."""
    try:
        await write_trace(**kwargs)
    except Exception as exc:
        log.warning("trace write failed (non-fatal): %s", exc)


def _trace_scope_clause(request: Request) -> tuple[str, list]:
    """Return (sql_predicate, params) restricting trace reads to the caller's tenant.

    Scoping rules:
      - Human sessions (admin) see every trace.
      - Agent OAuth callers (`at_…`) see only rows stamped with their `agent_id`.
      - Toolkit-key callers (`tk_…`) see every trace tagged with their bound
        toolkit, regardless of `agent_id`. This is intentional:
          * It preserves visibility for legacy / unregistered agents that call
            via a toolkit key and would otherwise have no `agent_id` to filter
            by.
          * The toolkit key holder is treated as the operator of that toolkit
            and is expected to be able to audit everything happening under it.
        Security note: the blessed pattern is per-agent OAuth identity. Handing
        a long-lived toolkit key to a registered agent therefore upgrades that
        agent from "see only my own traces" to "see every trace from every
        agent that shares this toolkit". Treat toolkit keys as operator
        credentials, not agent credentials.
      - Anonymous callers are rejected by middleware before reaching here, so
        the unrestricted branch never fires for untrusted callers.
    """
    if getattr(request.state, "is_admin", False):
        return "1=1", []
    agent_id = getattr(request.state, "agent_client_id", None)
    if agent_id:
        return "agent_id = ?", [agent_id]
    toolkit_id = getattr(request.state, "toolkit_id", None)
    if toolkit_id:
        return "toolkit_id = ?", [toolkit_id]
    # No principal we can scope by — fail closed so a future auth path that
    # forgets to set state doesn't accidentally expose every trace.
    return "0=1", []


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get(
    "/traces",
    summary="List execution traces — audit recent broker and workflow calls",
    response_model=TraceListPage,
    openapi_extra=agent_hints(
        when_to_use="Use when you need to audit recent API calls or workflow executions, review execution history, or debug issues by inspecting recent traces. Returns paginated list of traces with status, HTTP codes, and timing. Use ?limit= and ?offset= for pagination.",
        prerequisites=["Requires authentication (toolkit key or human session)"],
        avoid_when="Do not use if you already have a specific trace ID from a broker call (X-Jentic-Execution-Id header) — use GET /traces/{id} directly instead.",
        related_operations=[
            "GET /traces/{id} — get full trace with step-by-step detail",
            "GET /{target} (broker) — returns X-Jentic-Execution-Id header pointing to trace",
            "POST /workflows/{slug} — workflow execution returns trace_id in response body",
        ],
    ),
)
async def list_traces(
    request: Request,
    limit: Annotated[
        int, Query(description="Maximum number of traces to return (1-500)", ge=1, le=500)
    ] = 20,
    offset: Annotated[int, Query(description="Number of traces to skip for pagination", ge=0)] = 0,
    toolkit_id: Annotated[
        str | None, Query(description="Filter by toolkit id (exact match)")
    ] = None,
    agent_id: Annotated[
        str | None,
        Query(description="Filter by agent client_id (exact match). Admin-only signal."),
    ] = None,
    api_id: Annotated[
        str | None,
        Query(
            description=(
                "Filter by upstream API host. Matches when the capability id (e.g. "
                "`GET/api.github.com/users/...`) contains `/{api_id}/`. Use the host "
                "as it appears in `operation_id`."
            )
        ),
    ] = None,
    status: Annotated[
        str | None,
        Query(description="Filter by trace status (`success` | `failed` | `pending`)"),
    ] = None,
    since: Annotated[
        float | None,
        Query(description="Lower bound on `created_at` (unix seconds, inclusive)", ge=0),
    ] = None,
    until: Annotated[
        float | None,
        Query(description="Upper bound on `created_at` (unix seconds, exclusive)", ge=0),
    ] = None,
    capability_id: Annotated[
        str | None,
        Query(
            description=(
                "Filter by exact capability id. Matches `operation_id` for broker calls "
                "or `workflow_id` for workflow runs."
            )
        ),
    ] = None,
):
    """Returns recent execution traces with status, capability id, toolkit, timestamp, and HTTP status. Use GET /traces/{trace_id} for step-level detail."""
    scope_sql, scope_params = _trace_scope_clause(request)

    # Optional filters layered on top of the tenant scope. Each adds an AND clause
    # only when set, so the no-filter path stays identical to the legacy plan.
    where_parts: list[str] = [scope_sql]
    params: list = list(scope_params)
    if toolkit_id is not None:
        where_parts.append("toolkit_id = ?")
        params.append(toolkit_id)
    if agent_id is not None:
        where_parts.append("agent_id = ?")
        params.append(agent_id)
    if status is not None:
        where_parts.append("status = ?")
        params.append(status)
    if since is not None:
        where_parts.append("created_at >= ?")
        params.append(since)
    if until is not None:
        where_parts.append("created_at < ?")
        params.append(until)
    if capability_id is not None:
        # A capability id can land in either column depending on whether it was a
        # broker call or a workflow run; OR them so the caller doesn't have to
        # pre-classify.
        where_parts.append("(operation_id = ? OR workflow_id = ?)")
        params.extend([capability_id, capability_id])
    if api_id is not None:
        # operation_id format is METHOD/host/path; substring match keeps us
        # index-free but cheap on the small executions table. Wrap in slashes
        # to avoid matching e.g. `api.github.com.evil.com`.
        where_parts.append("operation_id LIKE ?")
        params.append(f"%/{api_id}/%")

    where_sql = " AND ".join(f"({p})" for p in where_parts)
    count_params = list(params)
    page_params = [*params, limit, offset]

    async with get_db() as db:
        async with db.execute(
            f"""SELECT id, toolkit_id, agent_id, operation_id, workflow_id,
                      status, http_status, duration_ms, error, created_at, completed_at
               FROM executions
               WHERE {where_sql}
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            page_params,
        ) as cur:
            rows = await cur.fetchall()
        async with db.execute(
            f"SELECT COUNT(*) FROM executions WHERE {where_sql}", count_params
        ) as cur:
            total = (await cur.fetchone())[0]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "traces": [
            {
                "id": r[0],
                "toolkit_id": r[1],
                "agent_id": r[2],
                "operation_id": r[3],
                "workflow_id": r[4],
                "status": r[5],
                "http_status": r[6],
                "duration_ms": r[7],
                "error": r[8],
                "created_at": r[9],
                "completed_at": r[10],
                "_links": {"self": f"/traces/{r[0]}"},
            }
            for r in rows
        ],
    }


# Defined before `/traces/{trace_id}` so FastAPI's path matcher resolves the
# concrete `/traces/usage` route rather than treating "usage" as a trace_id.
@router.get(
    "/traces/usage",
    summary="Trace usage aggregations — bucketed counts and top groups",
    response_model=UsageResponse,
    openapi_extra=agent_hints(
        when_to_use=(
            "Use when an operator UI needs aggregate signals (totals, success rate, "
            "latency, recent activity over time) instead of paginating raw traces. "
            "Mirrors the data shown on the Monitor page's HealthStrip and bar chart. "
            "Returns a single window summary, equally-sized time buckets, and the "
            "top groups by toolkit, API host, or agent."
        ),
        prerequisites=["Requires authentication (toolkit key or human session)"],
        avoid_when=(
            "Do not use for individual trace lookup — use GET /traces or "
            "GET /traces/{id}. Do not use for raw timeline data — bucket widths are "
            "chosen by the server (1 minute to 1 day depending on window length)."
        ),
        related_operations=[
            "GET /traces — list raw traces with the same filter set",
            "GET /jobs?status=running — count of in-flight async jobs",
        ],
    ),
)
async def get_usage(
    request: Request,
    since: Annotated[
        float | None,
        Query(description="Window start (unix seconds, inclusive). Defaults to 24h ago.", ge=0),
    ] = None,
    until: Annotated[
        float | None,
        Query(description="Window end (unix seconds, exclusive). Defaults to now.", ge=0),
    ] = None,
    group_by: Annotated[
        str,
        Query(description="What to group the `top` list by: 'toolkit' | 'api' | 'agent'."),
    ] = "toolkit",
    top_limit: Annotated[
        int,
        Query(description="Maximum rows in `top` list (1–50)", ge=1, le=50),
    ] = 10,
    toolkit_id: Annotated[
        str | None, Query(description="Filter to one toolkit before aggregating")
    ] = None,
    agent_id: Annotated[
        str | None, Query(description="Filter to one agent before aggregating")
    ] = None,
    api_id: Annotated[
        str | None,
        Query(description="Filter by upstream API host (substring match on operation_id)"),
    ] = None,
    status: Annotated[
        str | None, Query(description="Filter to a single status before aggregating")
    ] = None,
):
    """Aggregate execution traces in a time window for monitoring dashboards.

    The endpoint serves three pieces of information in one round-trip:

    1. `stats` — totals, success/failed split, mean and p50/p95 latency, and a
       point-in-time count of in-flight async jobs. Powers the HealthStrip.
    2. `buckets` — equally-sized time slices for stacking success/failed bar
       charts. Bucket width is chosen by the server based on the window:
       windows ≤ 1h use 60s buckets, ≤ 24h use 1h buckets, anything bigger
       uses 1d buckets. We never return more than ~144 buckets.
    3. `top` — the top N groups (toolkits, agents or API hosts) by trace count.

    All filters compose with AND semantics on top of the tenant scope.
    """
    if group_by not in ("toolkit", "api", "agent"):
        raise HTTPException(400, "group_by must be one of: toolkit, api, agent")

    now = time.time()
    if until is None:
        until = now
    if since is None:
        since = until - 86400.0
    if since >= until:
        raise HTTPException(400, "since must be < until")

    span = until - since
    # Bucket-size schedule: keep the bar chart ≤ ~144 columns so SVG stays cheap
    # and the response stays bounded regardless of how wide the caller asks.
    if span <= 3600.0:
        bucket_seconds = 60
    elif span <= 86400.0:
        bucket_seconds = 3600
    elif span <= 7 * 86400.0:
        bucket_seconds = 6 * 3600
    else:
        bucket_seconds = 86400

    scope_sql, scope_params = _trace_scope_clause(request)
    where_parts: list[str] = [scope_sql, "created_at >= ?", "created_at < ?"]
    params: list = [*scope_params, since, until]
    if toolkit_id is not None:
        where_parts.append("toolkit_id = ?")
        params.append(toolkit_id)
    if agent_id is not None:
        where_parts.append("agent_id = ?")
        params.append(agent_id)
    if api_id is not None:
        where_parts.append("operation_id LIKE ?")
        params.append(f"%/{api_id}/%")
    if status is not None:
        where_parts.append("status = ?")
        params.append(status)
    where_sql = " AND ".join(f"({p})" for p in where_parts)

    async with get_db() as db:
        # Top-level stats: counts split by status + duration aggregates. SUM/AVG
        # ignore NULL so unfinished traces don't skew the latency.
        async with db.execute(
            f"""SELECT
                  COUNT(*),
                  SUM(CASE WHEN status='success' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN status='failed'  THEN 1 ELSE 0 END),
                  SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),
                  AVG(duration_ms)
                FROM executions WHERE {where_sql}""",
            params,
        ) as cur:
            row = await cur.fetchone()
        total = int(row[0] or 0)
        success = int(row[1] or 0)
        failed = int(row[2] or 0)
        pending = int(row[3] or 0)
        avg_ms = float(row[4]) if row[4] is not None else None

        # Percentile via offset-based selection. SQLite has no native percentile
        # function, but for the trace volumes we expect (≪ 1M rows in the
        # window) ordering and slicing is acceptable. Skip if no rows.
        p50_ms: float | None = None
        p95_ms: float | None = None
        if total > 0:
            for percentile, target in ((0.5, "p50"), (0.95, "p95")):
                idx = max(0, min(total - 1, int(percentile * total)))
                async with db.execute(
                    f"""SELECT duration_ms FROM executions
                        WHERE {where_sql} AND duration_ms IS NOT NULL
                        ORDER BY duration_ms ASC LIMIT 1 OFFSET ?""",
                    [*params, idx],
                ) as cur:
                    p_row = await cur.fetchone()
                value = float(p_row[0]) if p_row and p_row[0] is not None else None
                if target == "p50":
                    p50_ms = value
                else:
                    p95_ms = value

        # Active-now: in-flight jobs (pending/running) — snapshot at query time,
        # not bound to the window. Cheap COUNT, no scope-clause complications.
        async with db.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN ('pending', 'running')"
        ) as cur:
            active_now = int((await cur.fetchone())[0] or 0)

        # Time buckets: integer-divide created_at into bucket_seconds slots.
        # Aligning to `since` keeps bucket starts predictable for the UI.
        async with db.execute(
            f"""SELECT
                  CAST((created_at - ?) / ? AS INTEGER) AS bucket_idx,
                  COUNT(*),
                  SUM(CASE WHEN status='success' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN status='failed'  THEN 1 ELSE 0 END),
                  AVG(duration_ms)
                FROM executions WHERE {where_sql}
                GROUP BY bucket_idx ORDER BY bucket_idx""",
            [since, bucket_seconds, *params],
        ) as cur:
            bucket_rows = await cur.fetchall()
        buckets = [
            {
                "ts": since + int(br[0]) * bucket_seconds,
                "total": int(br[1] or 0),
                "success": int(br[2] or 0),
                "failed": int(br[3] or 0),
                "avg_ms": float(br[4]) if br[4] is not None else None,
            }
            for br in bucket_rows
        ]

        # Top groups: pick the column based on group_by. For 'api' we extract
        # the host segment from operation_id (METHOD/host/path); SQLite doesn't
        # have a regex split, so we do a small Python loop after the GROUP BY.
        if group_by == "toolkit":
            group_col = "COALESCE(toolkit_id, '')"
        elif group_by == "agent":
            group_col = "COALESCE(agent_id, '')"
        else:  # api
            # Extract the second slash-segment of operation_id. Two passes of
            # substring trimming is enough — the format is METHOD/host/path.
            group_col = (
                "COALESCE("
                "SUBSTR(operation_id, INSTR(operation_id, '/') + 1, "
                "INSTR(SUBSTR(operation_id, INSTR(operation_id, '/') + 1), '/') - 1), "
                "'')"
            )
        async with db.execute(
            f"""SELECT
                  {group_col} AS k,
                  COUNT(*),
                  SUM(CASE WHEN status='success' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN status='failed'  THEN 1 ELSE 0 END),
                  AVG(duration_ms)
                FROM executions WHERE {where_sql}
                GROUP BY k
                ORDER BY COUNT(*) DESC LIMIT ?""",
            [*params, top_limit],
        ) as cur:
            top_rows = await cur.fetchall()

        # For agent group_by, look up friendly labels in one pass. For toolkit
        # we don't have a 1:1 name table (id IS the slug); for api the host is
        # already its own label.
        labels: dict[str, str | None] = {}
        if group_by == "agent" and top_rows:
            agent_ids = [r[0] for r in top_rows if r[0]]
            if agent_ids:
                placeholders = ",".join("?" * len(agent_ids))
                async with db.execute(
                    f"SELECT client_id, client_name FROM agents WHERE client_id IN ({placeholders})",
                    agent_ids,
                ) as cur:
                    for cid, name in await cur.fetchall():
                        labels[cid] = name

    top = [
        {
            "key": tr[0] or "",
            "label": labels.get(tr[0]) if group_by == "agent" else (tr[0] or None),
            "total": int(tr[1] or 0),
            "success": int(tr[2] or 0),
            "failed": int(tr[3] or 0),
            "avg_ms": float(tr[4]) if tr[4] is not None else None,
        }
        for tr in top_rows
    ]

    return {
        "since": since,
        "until": until,
        "bucket_seconds": bucket_seconds,
        "group_by": group_by,
        "stats": {
            "total": total,
            "success": success,
            "failed": failed,
            "pending": pending,
            "avg_ms": avg_ms,
            "p50_ms": p50_ms,
            "p95_ms": p95_ms,
            "active_now": active_now,
        },
        "buckets": buckets,
        "top": top,
    }


@router.get(
    "/traces/{trace_id}",
    summary="Get trace detail — step-by-step execution log",
    response_model=TraceOut,
    openapi_extra=agent_hints(
        when_to_use="Use after executing a broker call or workflow to retrieve the full execution trace with step-by-step details. Essential for debugging workflow failures — shows which step failed, what inputs were used, and what error was returned. Trace ID comes from X-Jentic-Execution-Id response header (broker calls) or trace_id in response body (workflows).",
        prerequisites=[
            "Requires authentication (toolkit key or human session)",
            "Valid trace ID from a previous execution (format: exec_{12chars})",
        ],
        avoid_when="Do not use for browsing recent traces — use GET /traces with pagination instead.",
        related_operations=[
            "GET /traces — list recent traces when you don't have a specific trace ID yet",
            "GET /{target} (broker) — execution returns X-Jentic-Execution-Id header",
            "POST /workflows/{slug} — workflow execution returns trace_id field",
        ],
    ),
)
async def get_trace(
    request: Request,
    trace_id: Annotated[str, Path(description="Trace ID (format: exec_{12chars})")],
):
    """Returns the full execution trace with all steps: capability called, inputs, outputs, HTTP status, and timing. Useful for debugging failed workflow steps."""
    scope_sql, scope_params = _trace_scope_clause(request)
    async with get_db() as db:
        async with db.execute(
            f"""SELECT id, toolkit_id, agent_id, operation_id, workflow_id, spec_path,
                      status, http_status, duration_ms, error, created_at, completed_at
               FROM executions WHERE id=? AND {scope_sql}""",
            (trace_id, *scope_params),
        ) as cur:
            row = await cur.fetchone()

        if not row:
            # 404 (not 403) for cross-tenant reads so we don't leak existence.
            raise HTTPException(404, f"Trace '{trace_id}' not found")

        async with db.execute(
            """SELECT id, step_id, operation, status, http_status,
                      inputs, output, error, started_at, completed_at
               FROM execution_steps WHERE execution_id=? ORDER BY started_at""",
            (trace_id,),
        ) as cur:
            step_rows = await cur.fetchall()

    steps = [
        {
            "id": s[0],
            "step_id": s[1],
            "operation": s[2],
            "status": s[3],
            "http_status": s[4],
            "output": json.loads(s[5]) if s[5] else None,  # inputs stored in output col
            "detail": json.loads(s[6]) if s[6] else None,
            "error": s[7],
            "started_at": s[8],
            "completed_at": s[9],
        }
        for s in step_rows
    ]

    return {
        "id": row[0],
        "toolkit_id": row[1],
        "agent_id": row[2],
        "operation_id": row[3],
        "workflow_id": row[4],
        "spec_path": row[5],
        "status": row[6],
        "http_status": row[7],
        "duration_ms": row[8],
        "error": row[9],
        "created_at": row[10],
        "completed_at": row[11],
        "steps": steps,
        "_links": {"self": f"/traces/{row[0]}"},
    }
