# Monitor

The **Monitor** page (`/monitor`) is the operational lens over every capability
call your agents make — synchronous broker calls, async jobs, and Arazzo
workflow executions. It is backed by two tables: `executions` (one row per
trace, written when a call completes) and `jobs` (one row per async submission,
written when a call is accepted).

The legacy `/traces` and `/jobs` routes redirect to `/monitor`; the per-record
pages `/traces/{id}` and `/jobs/{id}` still resolve for deep links.

---

## The three tabs

### Overview

A health summary for the selected time range:

- **Health strip** — total calls, success rate, average latency, and a
  clickable **Active now** pill counting in-flight async jobs (`pending` /
  `running`). The pill links to the Jobs tab filtered to in-flight work.
- **Breakdown** — call volume grouped by toolkit, API, or agent, each with a
  per-row sparkline trend.
- **Charts** — a daily bar chart and a top-N bubble chart.

### Execution Log

The historical record — one row per trace, written **after** a call completes.
Includes every synchronous broker call, every workflow run, and every async
call once it has reached a terminal state.

- Filters: status, toolkit, API, agent, time range, plus free-text search.
- A detail sheet showing the trace's inputs, outputs, and steps.
- For workflow traces, a **Child broker calls** panel listing each child trace
  the workflow spawned.
- A `JobBadge` on rows that originated from a job, linking to the Jobs tab.

### Jobs

The control plane over async work — one row per job, written **when the call is
submitted**. Only async-flavoured calls appear here:

- requests sent with `Prefer: wait=0`,
- requests that exceeded a `Prefer: wait=N` timeout,
- broker calls where the upstream API itself returned `202`,
- async workflow runs.

- Filters: status, kind (`workflow` / `broker`), toolkit, agent, free-text
  search.
- Polling, so in-flight rows update without a manual refresh.
- A detail sheet with a **Cancel** action and a back-link to the trace the job
  produced.

---

## Execution Log vs Jobs

The two tabs overlap on async calls that have already completed, but they answer
different questions:

| | Execution Log | Jobs |
|---|---|---|
| Question | "What happened?" | "What was asked for, including work that hasn't finished?" |
| Table | `executions` | `jobs` |
| Written | After the call completes | When the call is submitted |
| Covers | Every sync call, workflow run, and completed async call | Async submissions only |
| Holds | Outcome: status, duration, outputs, steps | Intent: agent-supplied `inputs`, current state, cancel |

Use the Execution Log to debug or audit past calls, the Jobs tab to watch what's
running right now or cancel a runaway job, and the Overview for high-level
health. The same explanation is available in-app from the help control in the
page header.

---

## Cross-linking

Traces and jobs reference each other so you can move between them without a
second lookup:

- **Trace → job** — a trace produced by a job renders a `JobBadge`; its detail
  sheet has a **Linked Context** section.
- **Job → trace** — a job that produced a trace exposes a clickable `trace_id`.
- **Workflow → children** — each broker hop made by a workflow is recorded as a
  child trace carrying `parent_trace_id`, surfaced in the **Child broker calls**
  panel.

Child traces are attributed automatically: the workflow runner forwards an
`X-Jentic-Parent-Trace` header on each step, which the broker honours **only**
for loopback callers, so the link cannot be spoofed from outside.

State lives in the URL — the active tab, filters, search (`?q=`), and the open
drawer (`?id=` / `?job=`) — so refresh, back/forward, and shared links all
restore the view.

---

## Data model

`executions` carries the trace record and its catalog links:

| Column | Meaning |
|---|---|
| `api_id` | Catalog-form API id the call resolved to (e.g. `stripe.com`), joined to `apis` for a friendly name |
| `job_id` | The job that produced this trace, if any |
| `parent_trace_id` | The workflow trace this row is a child of, if any |
| `inputs` / `outputs` | Workflow-level input and output bundles (JSON) |

`inputs` and `outputs` are populated for **workflow** runs. Synchronous broker
rows leave them `null` — the broker does not store request or response bodies,
so the drawer's Inputs / Outputs panels stay hidden for those rows. (Broker body
capture is intentionally out of scope; bodies routinely carry credentials and
PII and would need a redaction story first.)

Per-step detail for workflows is stored in `execution_steps` (`step_id`,
`operation`, `status`, `http_status`, `inputs`, `output`, `error`) and returned
on the trace as `steps[]`.

---

## API

All endpoints require authentication and are tenant-scoped: an OAuth agent sees
only its own traces and jobs; admins see everything. Cross-tenant reads of a
specific record return `404`.

### `GET /traces`

Paginated trace list for the Execution Log.

- Filters: `status`, `toolkit_id`, `api_id` (exact match on the `api_id`
  column), `agent_id`, time range.
- `q` — free-text search across `operation_id`, `workflow_id`, `api_id`, and
  `agent_id`. `%` and `_` are escaped, so the term matches literally.
- Ordered by `created_at DESC` with a stable `id` tiebreaker for consistent
  pagination.

### `GET /traces/{id}`

A single trace with `steps[]` (per-step inputs/outputs for workflows) and
`children[]` (child broker traces, for workflow traces only).

### `GET /traces/usage`

Aggregates that power the Overview tab.

```http
GET /traces/usage?group_by=api&since={unix_ts}&top_limit=12
X-Jentic-API-Key: {key}
```

- `group_by` — one of `toolkit`, `api`, `agent`.
- `api_id` — exact match on the `api_id` column (same semantics as `/traces`).
- Returns per-bucket counts, per-row `trend` series for the sparklines, and a
  tenant-scoped `stats.active_now` in-flight count.

### `GET /jobs`

Paginated job list for the Jobs tab.

- Filters: `status` (single value or comma-separated, e.g. `pending,running`),
  `kind` (`workflow` | `broker`), `toolkit_id`, `agent_id`.
- `q` — free-text search across the rendered columns (LIKE-escaped).
- Each row exposes `parent_trace_id` via a correlated lookup, so a child job can
  render "part of workflow X" without a second fetch.
- The capability identifier is returned as `capability` (a workflow slug or a
  broker capability id).

### `GET /jobs/{id}`

Poll a single job. Returns `status: pending|running` while in progress,
`complete` with `result` when done, `upstream_async` when the upstream API
itself returned `202`, or `failed` with `error` and `http_status` on failure.
Includes the agent-supplied `inputs`.

### `DELETE /jobs/{id}`

Cancel an in-flight job. Jobs already in a terminal state (`complete`,
`failed`, `upstream_async`) cannot be cancelled.

---

## See also

- [workflows.md](workflows.md) — how workflow runs are dispatched and traced
- [architecture.md](architecture.md) — data model and request flow
- [auth.md](auth.md) — the tenant-scoping model behind trace and job visibility
