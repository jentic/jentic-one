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
from src.routers.jobs import _jobs_scope_clause, _like_escape


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
    arazzo_steps: dict[str, dict] | None = None,
    agent_id: str | None = None,
    job_id: str | None = None,
    parent_trace_id: str | None = None,
    api_id: str | None = None,
    inputs: dict | None = None,
    outputs: dict | None = None,
) -> str:
    """Write an execution trace (+ optional step records) to the DB.

    Uses UPSERT to preserve created_at on updates (e.g. async pending → success).

    `api_id` is the FK-shaped pointer into `apis(id)`. The broker passes the
    credential's `api_id` when one matched (catalog-form, e.g. `stripe.com`);
    workflow rows pass None because they're multi-API by definition. NULL is
    fine — the read-side LEFT JOIN renders unattributed rows the same way it
    handles unknown toolkits/agents.

    `arazzo_steps` is an optional `{step_id: arazzo_step_def}` map used to
    enrich per-step rows in `execution_steps`. Workflow runs pass it through
    so the drawer can show what each step actually called (operationId,
    workflowId, etc.) instead of just an opaque step_id and an error blob.

    `inputs` / `outputs` are workflow-level JSON payloads shown in the
    drawer's Inputs / Outputs panels. Workflow runs pass these from the
    runner result; broker calls leave them None — request/response bodies
    aren't persisted (PII / size). Stored as JSON-encoded TEXT; UPSERT
    preserves prior non-null values via COALESCE so an async-job status
    update doesn't blow them away.
    """
    async with get_db() as db:
        await db.execute(
            """INSERT INTO executions
               (id, toolkit_id, agent_id, operation_id, workflow_id, spec_path,
                status, http_status, duration_ms, error, job_id, parent_trace_id,
                api_id, inputs, outputs, created_at, completed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,unixepoch(),
                       CASE WHEN ?='pending' THEN NULL ELSE unixepoch() END)
               ON CONFLICT(id) DO UPDATE SET
                 status=excluded.status,
                 http_status=excluded.http_status,
                 duration_ms=excluded.duration_ms,
                 error=excluded.error,
                 job_id=COALESCE(executions.job_id, excluded.job_id),
                 parent_trace_id=COALESCE(executions.parent_trace_id, excluded.parent_trace_id),
                 api_id=COALESCE(executions.api_id, excluded.api_id),
                 inputs=COALESCE(executions.inputs, excluded.inputs),
                 outputs=COALESCE(executions.outputs, excluded.outputs),
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
                job_id,
                parent_trace_id,
                api_id,
                json.dumps(inputs) if inputs is not None else None,
                json.dumps(outputs) if outputs is not None else None,
                # Bound again for the completed_at CASE: a 'pending' insert has
                # not completed, so completed_at stays NULL until a terminal
                # status update fills it via the ON CONFLICT branch below.
                status,
            ),
        )

        if step_outputs:
            for step_id, step_data in step_outputs.items():
                err_ctx = (
                    step_data.get("runner_error_context") if isinstance(step_data, dict) else None
                )
                step_http = err_ctx.get("http_code") if isinstance(err_ctx, dict) else None
                step_err = step_data.get("error") if isinstance(step_data, dict) else None
                # Per-step status: explicit `error` field wins; otherwise infer
                # from runner_error_context presence; otherwise treat as success.
                # `step_outputs` is only populated by the runner once the step
                # finished, so "no error signal" == "ran cleanly".
                if step_err or err_ctx:
                    step_status = "error"
                else:
                    step_status = "success"
                # Resolve `operation` from the Arazzo step definition when the
                # caller passed the lookup map. The Arazzo runner doesn't echo
                # this back inside step_data so the writer has no other source.
                step_operation: str | None = None
                if arazzo_steps and step_id in arazzo_steps:
                    arazzo_step = arazzo_steps[step_id]
                    step_operation = (
                        arazzo_step.get("operationId")
                        or arazzo_step.get("operationPath")
                        or arazzo_step.get("workflowId")
                    )
                await db.execute(
                    """INSERT INTO execution_steps
                       (id, execution_id, step_id, operation, status,
                        http_status, output, error)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()),
                        trace_id,
                        step_id,
                        step_operation,
                        step_status,
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


def _trace_scope_clause(request: Request, prefix: str = "") -> tuple[str, list]:
    """Return (sql_predicate, params) restricting trace reads to the caller's tenant.

    `prefix` lets callers qualify column refs when the trace SELECT joins
    other tables — pass `prefix="e."` and the predicate becomes
    `e.agent_id = ?`. Default empty for the simpler single-table reads
    (e.g. `get_usage`).

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
        return f"{prefix}agent_id = ?", [agent_id]
    toolkit_id = getattr(request.state, "toolkit_id", None)
    if toolkit_id:
        return f"{prefix}toolkit_id = ?", [toolkit_id]
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
                "Filter by upstream API. Exact match against the `api_id` column "
                "on executions, which is the catalog-form `apis.id` (e.g. "
                "`stripe.com`, `github.com`). Indexed; use this in preference to "
                "scanning `operation_id` substrings."
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
    q: Annotated[
        str | None,
        Query(
            description=(
                "Free-text substring match (case-insensitive) over the columns the "
                "Monitor row renders: `operation_id`, `workflow_id`, `api_id`, "
                "`agent_id`. Empty/whitespace strings are treated as not set so the "
                "no-filter plan stays cheap. Note: none of these columns are indexed "
                "for prefix lookups, so `q` always implies a scan over the rows the "
                "tenant + time-window clauses already select — fine for the Monitor "
                "page (range capped to 24h by default) but don't use it as a "
                "general-purpose search."
            ),
            min_length=1,
            max_length=200,
        ),
    ] = None,
):
    """Returns recent execution traces with status, capability id, toolkit, timestamp, and HTTP status. Use GET /traces/{trace_id} for step-level detail."""
    scope_sql, scope_params = _trace_scope_clause(request, prefix="e.")

    # Optional filters layered on top of the tenant scope. Each adds an AND clause
    # only when set, so the no-filter path stays identical to the legacy plan.
    where_parts: list[str] = [scope_sql]
    params: list = list(scope_params)
    if toolkit_id is not None:
        where_parts.append("e.toolkit_id = ?")
        params.append(toolkit_id)
    if agent_id is not None:
        where_parts.append("e.agent_id = ?")
        params.append(agent_id)
    if status is not None:
        where_parts.append("e.status = ?")
        params.append(status)
    if since is not None:
        where_parts.append("e.created_at >= ?")
        params.append(since)
    if until is not None:
        where_parts.append("e.created_at < ?")
        params.append(until)
    if capability_id is not None:
        # A capability id can land in either column depending on whether it was a
        # broker call or a workflow run; OR them so the caller doesn't have to
        # pre-classify.
        where_parts.append("(e.operation_id = ? OR e.workflow_id = ?)")
        params.extend([capability_id, capability_id])
    if api_id is not None:
        # api_id is a column on executions (catalog form, e.g. `stripe.com`).
        # Indexed; equality match. Legacy rows without api_id were backfilled
        # in migration 0007 wherever the upstream had been imported.
        where_parts.append("e.api_id = ?")
        params.append(api_id)
    if q is not None and q.strip():
        # Free-text fallthrough: substring match across the four columns the
        # Monitor row actually renders. Each LIKE forces a scan but the scan
        # is bounded by the tenant + (since,until) clauses already in
        # where_parts, so on the Monitor page (24h default range) it stays
        # cheap. We OR the four columns so a single token like "stripe"
        # matches whether it landed in api_id, operation_id, workflow_id, or
        # agent_id.
        like = f"%{_like_escape(q.strip())}%"
        where_parts.append(
            "(e.operation_id LIKE ? ESCAPE '\\' OR e.workflow_id LIKE ? ESCAPE '\\' "
            "OR e.api_id LIKE ? ESCAPE '\\' OR e.agent_id LIKE ? ESCAPE '\\')"
        )
        params.extend([like, like, like, like])

    where_sql = " AND ".join(f"({p})" for p in where_parts)
    count_params = list(params)
    page_params = [*params, limit, offset]

    async with get_db() as db:
        async with db.execute(
            f"""SELECT e.id, e.toolkit_id, e.agent_id, e.operation_id, e.workflow_id,
                      e.status, e.http_status, e.duration_ms, e.error,
                      e.created_at, e.completed_at,
                      e.job_id, e.parent_trace_id,
                      e.api_id, a.name AS api_name
               FROM executions e
               LEFT JOIN apis a ON a.id = e.api_id
               WHERE {where_sql}
               ORDER BY e.created_at DESC, e.id DESC LIMIT ? OFFSET ?""",
            page_params,
        ) as cur:
            rows = await cur.fetchall()
        async with db.execute(
            f"SELECT COUNT(*) FROM executions e WHERE {where_sql}", count_params
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
                "job_id": r[11],
                "parent_trace_id": r[12],
                "api_id": r[13],
                "api_name": r[14],
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
        Query(
            description=(
                "Filter by upstream API. Exact match against the indexed "
                "`api_id` column on executions (catalog-form `apis.id`, "
                "e.g. `stripe.com`). Same semantics as `/traces?api_id=`."
            )
        ),
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
        # Exact match on the indexed `api_id` column. Was a substring on
        # `operation_id` until /traces switched to the column in 0007 — this
        # endpoint had been left behind and disagreed with /traces on what
        # `api_id` meant. Catalog-form ids only (e.g. "github.com").
        where_parts.append("api_id = ?")
        params.append(api_id)
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

        # Active-now: in-flight jobs (pending/running) — snapshot at query
        # time, not bound to the window. Scoped to the same tenant as the
        # rest of `get_usage` so an agent doesn't see "5 active" because
        # somebody else's workflow is running.
        jobs_scope_sql, jobs_scope_params = _jobs_scope_clause(request)
        async with db.execute(
            f"SELECT COUNT(*) FROM jobs "
            f"WHERE {jobs_scope_sql} AND status IN ('pending', 'running')",
            jobs_scope_params,
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

        # Top groups: pick the column based on group_by. Each is a real column
        # on `executions` now — `api_id` was added in migration 0007 and is
        # populated at write time by the broker (or backfilled). No more
        # operation_id substring gymnastics.
        if group_by == "toolkit":
            group_col = "COALESCE(toolkit_id, '')"
        elif group_by == "agent":
            group_col = "COALESCE(agent_id, '')"
        else:  # api
            group_col = "COALESCE(api_id, '')"
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

        # Per-row sparkline trend: a fixed-length time-series for each top row.
        # Independent of the top-level `bucket_seconds` (which can be up to 144
        # columns wide) — sparklines render best with a small, constant number
        # of points so we always pick 12 equal buckets across the window.
        SPARKLINE_BUCKETS = 12
        sparkline_seconds = max(1.0, span / SPARKLINE_BUCKETS)
        trend_map: dict[str, list[int]] = {}
        if top_rows:
            top_keys = [tr[0] or "" for tr in top_rows]
            placeholders = ",".join("?" * len(top_keys))
            async with db.execute(
                f"""SELECT
                      {group_col} AS k,
                      CAST((created_at - ?) / ? AS INTEGER) AS bidx,
                      COUNT(*)
                    FROM executions WHERE {where_sql}
                      AND {group_col} IN ({placeholders})
                    GROUP BY k, bidx""",
                [since, sparkline_seconds, *params, *top_keys],
            ) as cur:
                trend_rows = await cur.fetchall()
            for tr in top_rows:
                trend_map[tr[0] or ""] = [0] * SPARKLINE_BUCKETS
            for k, bidx, count in trend_rows:
                key = k or ""
                idx = max(0, min(SPARKLINE_BUCKETS - 1, int(bidx)))
                if key in trend_map:
                    trend_map[key][idx] += int(count or 0)

        # For agent / api group_by, look up friendly labels in one pass. For
        # toolkit we don't have a 1:1 name table (id IS the slug, no name
        # column). For api the row key is `api_id` (catalog form, e.g.
        # `stripe.com`) — joining `apis` recovers the human-readable name
        # ("Stripe API"); rows whose api_id isn't registered fall through to
        # null and the frontend renders the key.
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
        elif group_by == "api" and top_rows:
            api_ids = [r[0] for r in top_rows if r[0]]
            if api_ids:
                placeholders = ",".join("?" * len(api_ids))
                async with db.execute(
                    f"SELECT id, name FROM apis WHERE id IN ({placeholders})",
                    api_ids,
                ) as cur:
                    for aid, name in await cur.fetchall():
                        labels[aid] = name

    top = [
        {
            "key": tr[0] or "",
            # Display label resolution by group:
            #   - agent: agents.client_name (looked up above)
            #   - api:   apis.name when registered (looked up above), null
            #            otherwise — frontend falls back to rendering the key
            #            (the catalog-form host)
            #   - toolkit: id IS the label (no separate name table)
            "label": (labels.get(tr[0]) if group_by in ("agent", "api") else (tr[0] or None)),
            "total": int(tr[1] or 0),
            "success": int(tr[2] or 0),
            "failed": int(tr[3] or 0),
            "avg_ms": float(tr[4]) if tr[4] is not None else None,
            "trend": trend_map.get(tr[0] or "", []),
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
    scope_sql, scope_params = _trace_scope_clause(request, prefix="e.")
    async with get_db() as db:
        async with db.execute(
            f"""SELECT e.id, e.toolkit_id, e.agent_id, e.operation_id, e.workflow_id,
                      e.spec_path, e.status, e.http_status, e.duration_ms, e.error,
                      e.created_at, e.completed_at,
                      e.job_id, e.parent_trace_id,
                      e.api_id, a.name AS api_name,
                      e.inputs, e.outputs
               FROM executions e
               LEFT JOIN apis a ON a.id = e.api_id
               WHERE e.id=? AND {scope_sql}""",
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

        # Children panel: broker traces spawned by this trace. Only workflow
        # traces ever have children, so we skip the query for non-workflow
        # rows. Tenant scoping reuses the same scope clause as the parent
        # fetch — a child written under a different tenant should not leak
        # back into a workflow drawer (defense in depth, the writer already
        # carries tenant forward).
        child_rows: list = []
        if row[4]:  # row[4] is workflow_id
            async with db.execute(
                f"""SELECT e.id, e.operation_id, e.status, e.http_status,
                          e.duration_ms, e.created_at,
                          e.api_id, a.name AS api_name
                   FROM executions e
                   LEFT JOIN apis a ON a.id = e.api_id
                   WHERE e.parent_trace_id = ? AND {scope_sql}
                   ORDER BY e.created_at ASC, e.id ASC""",
                (trace_id, *scope_params),
            ) as cur:
                child_rows = await cur.fetchall()

    steps = [
        {
            "id": s[0],
            "step_id": s[1],
            "operation": s[2],
            "status": s[3],
            "http_status": s[4],
            # Column 5 is `inputs`; current writer leaves it NULL because the
            # Arazzo runner doesn't expose per-step inputs. Surface it anyway
            # so a future writer change reads through without UI work.
            "inputs": json.loads(s[5]) if s[5] else None,
            # Column 6 is `output` — the JSON-encoded step_data blob from the
            # runner. Includes upstream response, error context, and any
            # outputs the workflow declared. The drawer renders this as a
            # collapsible blob; TraceDetailPage just shows whether it exists.
            "output": json.loads(s[6]) if s[6] else None,
            "error": s[7],
            "started_at": s[8],
            "completed_at": s[9],
        }
        for s in step_rows
    ]

    children = [
        {
            "id": c[0],
            "operation_id": c[1],
            "status": c[2],
            "http_status": c[3],
            "duration_ms": c[4],
            "created_at": c[5],
            "api_id": c[6],
            "api_name": c[7],
        }
        for c in child_rows
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
        "job_id": row[12],
        "parent_trace_id": row[13],
        "api_id": row[14],
        "api_name": row[15],
        "inputs": json.loads(row[16]) if row[16] else None,
        "outputs": json.loads(row[17]) if row[17] else None,
        "steps": steps,
        "children": children,
        "_links": {"self": f"/traces/{row[0]}"},
    }
