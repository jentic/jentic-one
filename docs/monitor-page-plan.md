# Monitor Page — Plan

A grounded plan for closing the gaps between the production `jentic-webapp`
Monitor surface and ours, narrowed to the items I'd block a release on.
Verified against the codebase on `ia/monitor-page` — every claim cites the
file and line it came from.

## Framing

The webapp's Monitor is itself a **thin observability floor**, not a rich
HTTP debugger. It has no per-step rendering, no request body, no response
body, no retry buttons, no copy-to-clipboard. Most of the parity gaps the
audit surfaces (latency colouring, HTTP status pills, child-trace lists,
method/URL split) are polish, not blockers.

The real problems are narrower and I'm only including them here:

1. **Two drawer panels (`Inputs`, `Outputs`) are wired up but always empty**
   because the backend doesn't return the fields the frontend reads.
2. **Workflow step data is captured but not rendered.** The drawer shows
   `stepCount` as a number while `trace.steps[]` carries everything.
3. **One filter bug + two scope leaks** that the audit caught and we
   haven't fixed.
4. **One small table-readability gap:** you can't tell broker rows from
   workflow rows at a glance.

Everything else is deferred — listed in [What we're explicitly _not_ doing](#what-were-explicitly-not-doing)
so it doesn't haunt a future PR description.

## What's broken today, with citations

### 1. The drawer's `Inputs` / `Outputs` panels are dead code

`MonitorPage.tsx:388-395` reads `(trace as Record<string, unknown>).request`
and `…response` after fetching `GET /traces/{id}`. Neither field exists on
the response — `routers/traces.py:689-708` returns exactly:

```
id, toolkit_id, agent_id, operation_id, workflow_id, spec_path, status,
http_status, duration_ms, error, created_at, completed_at, job_id,
parent_trace_id, api_id, api_name, steps[], _links.self
```

There is no `request`, no `response`, no `inputs`, no `outputs`. The two
`<pre>` blocks in `ExecutionDetailSheet.tsx:275-295` are gated on
`Object.keys(execution.inputs).length > 0`, which is always false because
`MonitorPage` writes `inputs: {}` on row click (`MonitorPage.tsx:376`)
and the `request` lookup that's supposed to populate it always returns
`undefined`. The user sees no error, just no data.

**Same dead code lives on `TraceDetailPage.tsx:185-208`** — the standalone
trace page renders `Request` and `Response` cards gated on `trace.request`
/ `trace.response`, neither of which is on the wire. Same fix has to land
there too.

**Per-trace persistence today** (broker side): `routers/broker.py:622-640`
writes only `operation_id = f"{method}/{host}{path}"` plus status / code /
duration / error. No request body, no headers, no query params, no
response body anywhere on the broker write path.

**Per-trace persistence today** (workflow side): `routers/workflows.py:875-887`
calls `write_trace(...)` with `step_outputs=...` but **does not pass the
workflow-level `inputs` or `outputs`**, and **does not pass `job_id`** —
even on the async path. Async runs persist `inputs` on `jobs.inputs`
(`routers/jobs.py:81-87`) and `update_job(..., trace_id=...)` stamps
the forward link (`routers/jobs.py:101-122`), but the reverse direction
(`executions.job_id`) stays NULL for async workflow runs. `_job_response`
(`routers/jobs.py:198-231`) never returns the `inputs` field anyway, so
the front end can't reach them either way. Sync workflow runs leave no
input artifact at all.

### 2. Workflow steps are captured but not rendered in the drawer

`routers/traces.py:93-112` writes one `execution_steps` row per Arazzo
step on workflow completion, with `step_id`, `http_status`, `output`
(the entire `step_data` dict JSON-encoded — overloaded), and `error`.
The schema columns `operation`, `status`, `inputs`, `completed_at` are
present in `0001_baseline.py:175-189` but **the writer skips them**
(verified: lines 100-112 only `INSERT … (id, execution_id, step_id,
http_status, output, error)`).

`GET /traces/{id}` returns `steps[]` (`routers/traces.py:673-687`) and
the frontend type `TraceStepOut` (`ui/src/api/types.ts:219-231`)
already declares the shape. The drawer reads only `stepCount`
(`ExecutionDetailSheet.tsx:206-210`); the standalone `TraceDetailPage`
renders steps but isn't linked from the Monitor.

### 3. Filter bug: `/traces/usage` `api_id` substring vs exact

`routers/traces.py:422-424`:

```python
if api_id is not None:
    where_parts.append("operation_id LIKE ?")
    params.append(f"%/{api_id}/%")
```

`/traces` switched to exact-match on `executions.api_id` in the Option A
work (commit `5369f8a`). `/traces/usage` was missed. The OpenAPI doc
string at `routers/traces.py:368-371` still claims "substring match on
operation_id" — also stale, fix in the same change. Already flagged in
`docs/agent-e2e-skill-plan.md:78-82` as a five-line fix; rolling it in
here.

Note: the surrounding filters in `get_usage` use unqualified column
names (`toolkit_id = ?`, `agent_id = ?` at lines 416-421) because the
stats / buckets queries are single-table. The fix is `api_id = ?`
**without** the `e.` prefix, to match style. The `top` query joins
`apis` separately at lines 503-512 and isn't affected.

### 4. `active_now` is not tenant-scoped

`routers/traces.py:471-476`:

```python
async with db.execute(
    "SELECT COUNT(*) FROM jobs WHERE status IN ('pending', 'running')"
) as cur:
    active_now = int((await cur.fetchone())[0] or 0)
```

No `_trace_scope_clause`. An OAuth agent calling `/traces/usage` sees the
global in-flight count across every tenant, including admin-initiated
jobs.

### 5. `GET /jobs` is not tenant-scoped

`routers/jobs.py:286-322` builds `where_parts` from query params only —
no equivalent of `_trace_scope_clause`. An authenticated OAuth agent who
sends `GET /jobs` with no `agent_id` filter receives every tenant's
jobs.

### 6. Table doesn't distinguish broker rows from workflow rows

`ExecutionTable.tsx:88-95` renders the Toolkit cell as a single muted
text. The webapp's equivalent (`ExecutionTable.tsx:188-199` over there)
shows toolkit name on line 1 and `executionType` (`Operation` /
`Workflow`) capitalised on line 2. We already derive `executionType` —
`monitor-transformers.ts:136` sets it from `isWorkflow`, and it's on
`ExecutionLogEntry` at `monitor/types.ts:101`. UI just doesn't render it.

## What we're going to do

Six items, sequenced for landability — earlier ones are independent and
small, later ones cluster around a single migration.

### M1. Render `executionType` subtitle in the Toolkit cell *(2-line UI fix)*

`ExecutionTable.tsx:88-95` → render `row.executionType` capitalised under
`row.toolkitName`. No backend change, no schema change.

### M2. `/traces/usage` `api_id` filter: column equality, not substring

`routers/traces.py:422-424` → swap to `api_id = ?` (no `e.` prefix —
matches the unqualified-column style of the surrounding filters at
lines 416-421). Update the stale OpenAPI description at lines 368-371
to match the `/traces` wording. Update fixture seed in any usage test
that relied on substring semantics (check `tests/test_traces_usage.py`).

### M3. Tenant-scope `active_now`

`routers/traces.py:471-476` → add a `_trace_scope_clause`-equivalent
predicate keyed on `jobs.toolkit_id` / `jobs.agent_id`. The fields are
already on the `jobs` table since migration 0007. Use the same
admin/agent/toolkit-key dispatch as `_trace_scope_clause` does for
`executions`.

### M4. Tenant-scope `GET /jobs`

`routers/jobs.py:286-322` → factor out a `_jobs_scope_clause(request)`
mirroring `_trace_scope_clause`, apply to both `list_jobs` and
`get_job_route` (`:382-414`). Cross-tenant `GET /jobs/{id}` should 404,
not 403, to match the existing trace pattern at `routers/traces.py:661-663`.

### M5. Render workflow steps in the drawer

The data is already there — `trace.steps[]` is on the wire today. The
standalone `TraceDetailPage.tsx:132-183` already renders `step_id`,
`operation`, `http_status`, `status`, `error` (the "Steps" card). Port
that block into `ExecutionDetailSheet.tsx`, gated on
`execution.executionType === 'workflow' && execution.steps?.length > 0`.

Caveat: that block reads `step.operation` and `step.status`, neither of
which the writer currently populates (see #2). The render block stays
defensive — falsy checks already gate each pill — so it'll display the
sparser data we have today and improve as the writer fix lands. **Both
fixes ship together** so the drawer never renders an empty Steps card.

Nice-to-have within the same change: make each step row expandable to
show the kitchen-sink JSON we currently dump into `execution_steps.output`.

The writer fix: `routers/traces.py:100-112` should populate `operation`,
`status`, `inputs` (when the runner has them), and `completed_at`. Pure
writer change, no migration needed — those columns already exist on the
table per `0001_baseline.py:175-189`.

### M6. Fix workflow Inputs / Outputs end-to-end

This is the only multi-layer change in the plan. The webapp does it by
having dedicated `workflow_executions` and `operation_executions` tables
with `inputs` / `outputs` columns. We already collapse these into a
single `executions` table — adding two columns is the cleanest move.

**Schema** *(new migration 0008)*: add `executions.inputs TEXT`,
`executions.outputs TEXT` (both JSON-encoded, both nullable). No
backfill — legacy rows render as today (panel hidden by the
`Object.keys.length > 0` guard).

**Writer (workflow side)**:

- `routers/workflows.py:875-887` (sync path) → pass the workflow input
  bundle the runner received and the assembled `result_data["outputs"]`
  on success.
- Async path: `routers/workflows.py:542-549` already calls `create_job`
  with `inputs`. The async write_trace at `:875-887` runs from inside
  the dispatched task, so we have access to the same `inputs` and
  `result_data["outputs"]` there. **Same change should pass `job_id`
  too** — today the async branch never sets `executions.job_id`, even
  though `update_job` stamps the forward link `jobs.trace_id`. Without
  the reverse link, `parent_trace_id`-style child-trace lookups don't
  work for workflows. Plumb the `job_id` from the surrounding scope
  through to `write_trace`.
- `write_trace(...)` (`routers/traces.py:38-114`) gains optional
  `inputs: dict | None = None`, `outputs: dict | None = None` kwargs.
  JSON-encode at the boundary, store on the new columns. The UPSERT
  branch should `COALESCE(executions.inputs, excluded.inputs)` and
  `COALESCE(executions.outputs, excluded.outputs)` so the initial
  in-flight insert doesn't blow away outputs added on completion (same
  pattern the existing `job_id` / `parent_trace_id` / `api_id` use at
  lines 72-74).

**Writer (broker side) — DEFERRED**: capturing broker request body /
response body is a **redaction-and-PII decision**, not just a code
change. Bodies routinely contain bearer tokens, API keys, and customer
PII. Doing this without a redaction story is a privacy regression
relative to the current "we don't store it at all" stance.

This plan **does not** ship broker body capture. The drawer's Inputs /
Outputs panels stay empty for broker rows — but they'll populate for
workflow rows, which is where the user-experience pain is. Broker body
capture is its own scoped piece of work; tracked as a non-goal below.

**Reader**:

- `routers/traces.py:649-708` → select the two new columns, return them
  as `inputs` and `outputs` on `TraceOut`. Drop the misleading comment
  at line 680 about "inputs stored in output col" while we're in there.
- `models.py` `TraceOut` → add `inputs: dict | None`, `outputs: dict |
  None` with descriptions matching the workflow-only semantics.

**Frontend**:

- `MonitorPage.tsx:382-412` → read `trace.inputs` and `trace.outputs`
  directly instead of the dead `trace.request` / `trace.response`. Drop
  the `as Record<string, unknown>` casts and the index-signature
  loophole.
- `TraceDetailPage.tsx:185-208` → same fix on the standalone trace page.
  Currently renders empty "Request" / "Response" cards gated on dead
  fields; rename to "Inputs" / "Outputs" and read from `trace.inputs` /
  `trace.outputs`.
- `ui/src/api/types.ts` `TraceOut` (lines 198-217) → add
  `inputs?: Record<string, unknown> | null`, `outputs?: Record<string,
  unknown> | null`. Note: the type already has `[key: string]: unknown`
  catch-all (line 216), which is why the dead `trace.request` reads
  compile today — explicit fields make this a real contract.
- Regenerate `ui/openapi.json` and the generated TS client (same dance
  as commit `5369f8a`).

**Bonus, same change**: surface `inputs` on the Jobs surface too. The
column already stores them (`routers/jobs.py:81-87`); `_job_response`
just doesn't return the field. One-line addition at
`routers/jobs.py:198-231`. The `JobDetailSheet` gets an Inputs panel
identical in shape to the trace one.

## Sequencing

| Step | Independent of | Touches |
|------|----------------|---------|
| M1 (executionType subtitle) | everything | 1 UI file |
| M2 (api_id filter bug) | everything | 1 backend file, 1 test |
| M3 (active_now scope) | everything | 1 backend file, new test |
| M4 (GET /jobs scope) | everything | 1 backend file, new tests |
| M5 (render steps + fill in step columns) | M6 | 1 UI file, 1 backend writer |
| M6 (workflow inputs/outputs end-to-end) | needs migration 0008 | migration + 4 backend files + 4 frontend files + tests |

M1–M4 are tiny independent commits, ideally one PR each (or grouped as
"monitor parity round 1"). M5 stands alone. M6 is the headline change
and gets its own PR with a careful migration review.

## What we're explicitly _not_ doing

These came up in the audit. I considered each and chose to defer.

- **Broker request / response body capture.** Real privacy / redaction
  work blocks this. The Inputs / Outputs panels will remain empty for
  broker rows — accepted gap, called out to the user every time someone
  opens a broker trace in the drawer (the existing seed-only notice
  isn't quite right for this case; we should adapt its copy in M6).
- **Workflow progress event stream** (the webapp's `progressEvents[]`).
  Real feature, real lift, no acute pain — defer until someone asks for
  live workflow tailing.
- **Child trace reverse listing** in the drawer. Schema supports it
  (`idx_executions_parent_trace`); UI doesn't query it. Defer; parent
  link in the Linked Context card is enough today.
- **Latency colour-grading on the Duration column.** Polish.
- **HTTP status as a separate metric pill.** The status pill colour
  already conveys success/failure; raw code is one click away on the
  standalone trace page.
- **Cross-link buttons that deep-link the linked row.** Today they
  switch the active tab (`MonitorPage.tsx:357-371`). The TODO note
  there is honest; deep-linking by id is a follow-up.
- **Method / URL split on broker rows.** Would require a schema change
  (split `executions.operation_id`) for cosmetic gain.
- **Copy-to-clipboard affordances.** Polish, not blocking.
- **`capability_id` filter exposed on the Execution Log table.**
  Backend supports it (`routers/traces.py:222-230`); UI hides it. Niche.
- **`'all'` time-range option in the page-level toggle.** Type
  (`monitor/types.ts:8`) allows it; toggle doesn't expose it. Niche;
  defer until someone asks.
- **Async-job retry / re-dispatch.** Cancel exists; retry is a separate
  product question.
- **Webapp's history-row `tracked_execution_id` indirection.** Their
  model is "history row links to a detail row in another table"; ours
  is "trace row IS the detail row." We don't need this layer.

## Concrete file map

For the implementer, the exact spots:

| File | Lines | What changes |
|------|-------|--------------|
| `ui/src/components/monitor/execution-log/ExecutionTable.tsx` | 88-95 | M1 — render `row.executionType` subtitle |
| `src/routers/traces.py` | 422-424 | M2 — `api_id = ?` not `LIKE`; matching unqualified style |
| `src/routers/traces.py` | 368-371 | M2 — fix stale OpenAPI `api_id` description |
| `src/routers/traces.py` | 471-476 | M3 — wrap `active_now` count with scope clause keyed on `jobs.{toolkit_id,agent_id}` |
| `src/routers/jobs.py` | 286-322, 382-414 | M4 — new `_jobs_scope_clause` helper, applied to list + get; 404 cross-tenant |
| `src/routers/traces.py` | 100-112 | M5 (writer) — populate `operation`, `status`, `inputs`, `completed_at` on `execution_steps` insert |
| `ui/src/components/monitor/execution-log/ExecutionDetailSheet.tsx` | after 211 | M5 (UI) — port the steps render block from `ui/src/pages/TraceDetailPage.tsx:132-183` |
| `alembic/versions/0008_executions_inputs_outputs.py` | new | M6 — `ALTER TABLE executions ADD COLUMN inputs TEXT; ADD COLUMN outputs TEXT;` |
| `src/routers/traces.py` | 33-114 | M6 — `write_trace` accepts `inputs`, `outputs`; UPSERT preserves on completion |
| `src/routers/workflows.py` | 875-887 | M6 — pass `inputs`, `result_data["outputs"]`, **and `job_id`** (async path) |
| `src/routers/traces.py` | 649-708 | M6 — select + return `inputs`, `outputs`; drop the misleading line-680 comment |
| `src/models.py` `TraceOut` | 734+ | M6 — add `inputs: dict \| None`, `outputs: dict \| None` |
| `src/routers/jobs.py` | 198-231 | M6 — include `inputs` in `_job_response` |
| `ui/src/pages/MonitorPage.tsx` | 388-395 | M6 — read `trace.inputs` / `trace.outputs` directly |
| `ui/src/pages/TraceDetailPage.tsx` | 185-208 | M6 — same dead-code fix on the standalone page |
| `ui/src/api/types.ts` `TraceOut` | 198-217 | M6 — add `inputs?`, `outputs?` |
| `ui/openapi.json`, generated client | — | M6 — regenerate |

## Test additions per item

- **M2** — `tests/test_traces_usage.py`: add a row whose `operation_id`
  contains `"github.com"` as a substring but whose `api_id` is
  `"stripe.com"` (the underlying API). Assert that `?api_id=github.com`
  excludes it.
- **M3** — new test: seed two jobs with different tenants, assert non-admin
  callers see only their own count via `/traces/usage`.
- **M4** — new test: same seeding, assert non-admin callers see only their
  own jobs via `/jobs`; assert cross-tenant `GET /jobs/{id}` returns 404.
- **M5** — writer test: workflow run → assert `execution_steps` rows
  have `operation` and `status` populated. UI: vitest snapshot on the
  drawer with a workflow trace.
- **M6** — writer test: workflow run with inputs and outputs → assert
  both fields land on `executions` and round-trip through `GET
  /traces/{id}`. Async workflow run → assert `executions.job_id` is
  stamped (currently NULL — regression guard). Reader test: trace
  without inputs/outputs (broker row) returns nulls and the panel-hidden
  contract holds. Frontend smoke: standalone trace page no longer
  renders empty Request/Response cards for broker rows.

## Decision points the implementer can make

- M5 step-row UX: simple list vs collapsible JSON. I'd default to
  collapsed-by-default, expand-on-click — matches the rest of the
  drawer's density.
- M6 size limit: do we cap `inputs` / `outputs` at e.g. 64 KB? Probably
  yes, with a `truncated: true` marker. The Arazzo runner can produce
  big bundles. Discuss in PR review.
- M6 redaction: workflow inputs are user-supplied (the agent already
  saw them). I'd do **no** automatic redaction at write time — but
  document clearly that broker bodies are out of scope precisely because
  they need redaction.

— end —
