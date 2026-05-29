## Summary

Port the **Monitor page** from `jentic-webapp` into `jentic-mini-parallel` — a unified Overview / Execution Log / Jobs view backed by the `executions` and `jobs` tables. While porting, fix one real production security bug discovered along the way (Cookie request-side leak in the broker), tighten dev tooling, and add a real-call e2e smoke that exercises every new column end-to-end including `parent_trace_id` via a registered Arazzo workflow.

> **Reviewer attention:** the most important change is `fix(security): strip Cookie request header in broker forwards` (`17f5bec`). Tracked upstream as [`jentic/jentic-mini#457`](https://github.com/jentic/jentic-mini/issues/457). Recommend reviewing that commit first.

> **Branch state:** merged with `origin/main` at `22186db` to absorb #443/#444/#445 (dep bumps), #447 (Discover + Workspace surfaces), and #458 (axe sweep). Conflict resolution notes are in the merge commit body. Pre-merge tip is preserved at the local tag `ia/monitor-page-pre-merge` for rollback.

## What ships

### Monitor page (UI)

- Three tabs replacing the old `TracesPage` + `JobsPage`:
  - **Overview** — health strip, breakdown by toolkit / API / agent, sparkline trend lines per row, daily bar chart, top-N bubble chart
  - **Execution Log** — paginated trace table with filters (status, toolkit, API, agent, time-range), detail sheet, and a new `JobBadge` inline link rendered when a trace is part of a job
  - **Jobs** — new tab listing async/workflow jobs with status/kind/toolkit/agent filters, 15s polling, detail sheet with cancel button, and a back-link to the originating trace
- Cross-link surface
  - Trace → job: `JobBadge` in the Execution Log row + "Linked Context" section in the trace detail sheet
  - Job → trace: clickable `trace_id` in the job detail sheet
  - Loopback-only `parent_trace_id` line surfaces "part of workflow X" in the trace detail sheet when a broker call was spawned by a workflow

### Backend surface for Monitor

- `executions.job_id` and `executions.parent_trace_id` columns + indexes (`alembic/versions/0007_monitor_links.py` extended in place per the existing branch policy; idempotent via `PRAGMA table_info` checks)
- `write_trace` extended to persist both fields; `UPSERT` uses `COALESCE(existing, excluded)` so partial updates don't clear the cross-link
- `broker.py` reads `X-Jentic-Parent-Trace` only from loopback addresses (`127.0.0.1`, `::1`, `localhost`); external callers can't spoof workflow parentage
- `workflows.py` sets `X-Jentic-Parent-Trace` on the `arazzo-runner` subprocess session so child broker hops auto-attribute back to the workflow trace
- `GET /jobs` gains a `kind` filter (`workflow|broker`) and accepts comma-separated `status` values (e.g. `status=pending,running`)
- `GET /traces/usage` returns 12-bucket `trend` arrays per top row so the sparklines have real data

### Security fix — `fix(security)` (`17f5bec`)

When a human browser session calls the broker, urllib/the browser attaches `Cookie: jentic_session=…` to every request. The broker's `_HOP_BY_HOP` set previously did **not** include `cookie`, so the admin session JWT was forwarded verbatim to the upstream API and — for async dispatch — echoed back into `jobs.result.body`, where any admin reading `GET /jobs/{id}` could harvest it.

I caught this while writing the e2e smoke: my urllib `CookieJar` carried over the admin session, the broker forwarded it to httpbin, httpbin echoed it into the response body, and the broker stored that body in the jobs table.

**Fix**: add `"cookie"` to `_HOP_BY_HOP`. Pinned by `tests/test_broker_header_hygiene.py`, which also pins the `X-Jentic-*` strip behavior so a careless refactor can't reopen the leak.

This is the request-side counterpart to [`jentic/jentic-mini#56`](https://github.com/jentic/jentic-mini/issues/56) (response-side, already fixed). The reasoning that closed-out the response-side discussion ("the agent is the legitimate caller, response headers are application data") does not apply request-side: the agent's `Cookie` targets *Jentic*, not the upstream. Production impact is zero for `tk_`-keyed agent traffic (no cookies); browser/admin-session traffic stops leaking the JWT.

Same fix is needed in production `jentic-mini`. Tracked there as [`jentic/jentic-mini#457`](https://github.com/jentic/jentic-mini/issues/457).

### Dev tooling

- `scripts/e2e_smoke.py` — real-call probe against `httpbin.org` (no secrets, no catalog setup). Logs in as admin, mints a `tk_…` key, fires 6 sync + 3 async broker calls, polls `/jobs/{id}` to terminal state, asserts each async execution row carries the expected `job_id` cross-link. Then **registers an inline OpenAPI + 2-step Arazzo workflow against httpbin, runs it, and asserts both child broker traces land in `executions` with `parent_trace_id == workflow_trace_id`** — proving the `X-Jentic-Parent-Trace` loopback header is honoured end-to-end through the runner subprocess. Idempotent. ~13s end-to-end.
- `scripts/seed_monitor_data.py` — `DB_PATH` env now overrides the hard-coded container path so the script also runs on the host.
- `compose.parallel.yml` — isolated parallel docker stack on host ports `5180` (API) and `5181` (Vite UI), so Monitor work doesn't disturb the regular `:5173/:8900` dev stack.
- `ui/vite.config.ts` — added `/agents` to the SPA proxy table (was falling through to `index.html`, breaking the agents page in dev).

## Commits (in order)

| SHA | Type | What |
|---|---|---|
| `f5785b5` | `feat(api)` | Backend surface for the Monitor page |
| `c9a02ca` | `chore(infra)` | Add parallel docker compose stack for Monitor work |
| `430c5c5` | `feat(jobs)` | `kind` filter + comma-separated status on `GET /jobs` |
| `324d597` | `feat(traces)` | Sparkline trends + `executions ↔ jobs` cross-link columns |
| `4477a7e` | `test(backend)` | Jobs filter parity, sparkline trends, trace cross-links |
| `3a83574` | `feat(ui)` | Monitor page port with Jobs tab and cross-links |
| `3c97fe1` | `chore(seed)` | Correlate executions to jobs and fix kind/status drift |
| `eb07381` | `refactor(branch)` | Satisfy ruff lint+format on monitor-page commits |
| `d9135fb` | `fix(ui)` | Proxy `/agents` through Vite dev server to backend |
| `7308adf` | `feat(scripts)` | Add `e2e_smoke.py` for the parallel stack |
| `17f5bec` | **`fix(security)`** | **Strip Cookie request header in broker forwards** |
| `77c87e3` | `test(broker)` | Pin request-side header hygiene rules |
| `dfc4b21` | `chore(seed)` | Make `DB_PATH` configurable via env for host runs |
| `b756d22` | `test(scripts)` | Cover workflow `parent_trace_id` end-to-end in `e2e_smoke` |
| `22186db` | `merge` | Merge `origin/main` (Discover + Workspace, dep bumps); see commit body for conflict notes |

## Diff size

- 14 feature commits + 1 merge commit, ~+19k / −10k lines vs `origin/main` post-merge
- Backend / migrations / tests / scripts: unchanged from feature work
- UI: most additions are the regenerated OpenAPI client + new Monitor components; `TracesPage.tsx` and `JobsPage.tsx` are removed and replaced by `MonitorPage.tsx` and per-tab components

## Test plan

- [x] Backend: full pytest run after merge — **569 passed, 1 skipped (network), 12 deselected** (pre-existing flaky `test_no_auth_api_forwards_without_credentials` + `@pytest.mark.network` suite)
- [x] Cookie hygiene test was verified to fail without the broker fix and pass with it
- [x] Frontend after merge: `npm run lint` (0 errors, 201 pre-existing warnings), `npm run build` (clean), `npm run test:run` (**60 test files, 464 tests pass**)
- [x] **E2E smoke against the post-merge stack — fully green**:
  - 6 sync + 3 async broker calls, all return expected status
  - 3/3 async jobs reach `complete`
  - 3/3 execution↔job cross-links verified (`trace.job_id == dispatched job_id`)
  - **2/2 workflow child traces verified** (`trace.parent_trace_id == workflow_trace_id`)
  - Idempotency: re-running leaves no orphan keys, INSERT OR REPLACE on apis/workflows
- [x] Manual UI smoke at `http://localhost:5181/monitor` — all three tabs render, filters work, JobBadge clicks open the Jobs detail sheet, JobDetail trace-link opens the Execution Log detail sheet
- [x] Post-merge SPA route smoke (`/monitor`, `/discover`, `/workspace`, `/agents`) — all serve correctly through the Vite proxy
- [ ] **Reviewer to verify**: open `http://localhost:5181/agents` after a fresh login, confirm the agents list loads (regression check for the Vite proxy fix)
- [ ] **Reviewer to verify**: hit the broker from a browser logged in as admin (UI "try it" buttons, devtools fetch with `credentials: 'include'`), then `GET /jobs/{recent_id}` and confirm `result.body` contains no `jentic_session=…` substring
- [ ] **Reviewer to verify**: `/discover` and `/workspace` (added by #447) still work end-to-end after the merge, since the Monitor branch took main's versions of `RefreshButton` / `SearchInput` / `SegmentedToggle` / `SheetPrimitive`

## Related

- [`jentic/jentic-mini#457`](https://github.com/jentic/jentic-mini/issues/457) — same Cookie-leak bug filed upstream against production `jentic-mini`. The patch in this PR applies cleanly there.
- [`jentic/jentic-mini#56`](https://github.com/jentic/jentic-mini/issues/56) — response-side header-strip analogue. Already closed completed; this PR is the request-side counterpart.
- Does not touch [`jentic/jentic-mini#74`](https://github.com/jentic/jentic-mini/issues/74) (CSRF on cookie-authenticated routes) — related but distinct concern, separate PR.

## Out of scope (follow-ups)

- Real authenticated broker call in `e2e_smoke.py` (e.g. GitHub or OpenAI) — would also exercise credential injection paths. Skipped because it requires secrets in CI; the no-auth httpbin path covers cross-link wiring identically. The workflow coverage commit (`b756d22`) closed the previously-open gap on `parent_trace_id`.
- `AgentsPage` should distinguish 401/403 (re-auth) from 5xx / network / unexpected-content errors. Today any failure surfaces as "Could not load agents. Log in as admin and try again.", which is what masked the Vite proxy bug in this branch. Small UX polish, separate PR.

## Squash merge

Per repo convention (`/Users/manuel/Desktop/jentic-mini-workspace/.cursor/rules/git-conventions.mdc`): use **squash + merge**. Suggested squash commit message:

```
feat(ui): Monitor page port with cross-linked traces/jobs (#PR)

Replaces the separate TracesPage and JobsPage with a unified Monitor
page (Overview / Execution Log / Jobs). Adds executions.job_id and
executions.parent_trace_id cross-link columns, a Jobs tab with kind/
status filters, and inline JobBadge links in the Execution Log.

Includes one production security fix discovered while writing the e2e
smoke: the broker was forwarding the admin Cookie header (jentic_session
JWT) to upstream APIs and into stored job result bodies. Stripped via
_HOP_BY_HOP and pinned by a regression test. See the fix(security)
commit; tracked upstream as jentic/jentic-mini#457.

- Backend: parent_trace_id + job_id columns, kind filter on /jobs,
  sparkline trends on /traces/usage, X-Jentic-Parent-Trace loopback
  honoring in broker, threaded from arazzo-runner subprocess session
- UI: Monitor page with three tabs, cross-link badges, job detail
  sheet with cancel
- Security: strip Cookie from broker request forwards (request-side
  analogue of #56)
- Dev: parallel docker compose stack, e2e_smoke.py against httpbin
  (sync + async + workflow with parent_trace_id assertions), Vite
  /agents proxy fix, DB_PATH env on seed script
```
