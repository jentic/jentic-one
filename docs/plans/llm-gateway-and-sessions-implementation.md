# Plan: LLM Gateway + Sessions — Implementation

> Status: **planning only** — no application code changes. This doc is the deliverable.
> It unifies two bodies of work into one buildable system:
> 1. The backend **LLM gateway** design set at
>    `jentic-one-internal/docs/designs/llm-gateway/`.
> 2. The **LLM Proxy / Sessions** observability UI already built in this repo at
>    `ui/src/modules/llm-proxy/`, running entirely on mock data
>    (see `docs/plans/llm-proxy-sessions.md`).

---

## 1. Goal & the three workstreams

We want one real system where a self-hosted Jentic One operator can answer:
*"what did my agents actually do, what did they spend, and did governance behave?"*
— backed by live data, not the bundled mock.

Getting there is **three distinct workstreams**, only one of which the design docs
describe:

**(a) Backend LLM gateway** — the new `llm_gateway` surface per
`jentic-one-internal/docs/designs/llm-gateway/`: an in-process, OpenAI-compatible
surface (`POST /llm/v1/chat/completions`, `GET /llm/v1/models`, `GET /llm/health`)
that resolves a caller to a virtual key, enforces a **model allowlist**, injects a
provider credential from the `control` store, forwards to a proxy service
(builtin / LiteLLM sidecar / custom), and records **per-call usage/cost**
(metering). Budgets are delegated to the proxy.

**(b) The correlation "keystone"** — the highest-value gap and one the design docs
do **not** cover. Today a broker execution record carries no session identity, no
verdict, no method/path, and no credential id (`trace_id` is literally `"unknown"`
for third-party clients — proven by the captured run in
`ui/prototypes/session-data/`). To make a Session real we must mint a
`session_id` (one per agent run) and a `call_id` (one per intended tool call),
stamp them on **both** the gateway's LLM turns and the broker's execution records,
and enrich each broker execution record with `verdict` / `method` / `path` /
`credential_id`. Without this there is nothing to join a chat turn to the tool call
it caused, or to group 35 calls into one run.

**(c) Real serving API + UI cutover** — a new read API (`GET /proxy/sessions` and
`GET /proxy/sessions/{id}`) that **joins** three sources — broker
`execution_records` (the tool "action" half), gateway LLM turns + usage (the
"thinking" half), and `events` (denials, credential access) — into the
`SessionBundle` the UI already expects (`ui/src/modules/llm-proxy/api/types.ts`).
Then delete the UI's mock fallback and MSW handlers, keeping the types.

### Why the gateway alone does NOT make the UI real

Two independent reasons, both grounded in the captured run
(`ui/prototypes/session-data/README.md`):

1. **The two-source problem.** The Sessions view is fundamentally a *join* of two
   logs that never see each other. The proxy/gateway sees chat, reasoning, token
   usage, and the *framework* tool-uses (`Agent`, `Bash`, `Skill`, `Write`) — but
   the real `GET /flights` lives **inside a Bash command string**, invisible to the
   gateway. The broker sees the real REST call, the verdict, the credential, and
   the upstream result — but never the chat or the subagent tree. Building the
   gateway gives us a great "thinking" half; the "action" half already exists in
   the broker. Neither alone is a Session.

2. **Missing correlation.** Even with both halves recorded, there is no key that
   ties a gateway turn to the broker execution it produced, or that groups either
   into a run. The design docs give the gateway its own `Jentic-Execution-Id`
   (`routing.md`) but define **no `session_id`/`call_id`** and say nothing about
   stamping the broker. That correlation model is workstream (b) — undocumented in
   the design set and the true keystone.

So: (a) is necessary but not sufficient; (b) is the missing spine; (c) is what the
UI actually consumes.

---

## 2. Current state

### What exists (backend)

- **Surfaces** under `src/jentic_one/`: `registry`, `control`, `admin`, `broker`,
  `auth`, `shared`. Composition root is `src/jentic_one/wiring.py`. The combined
  app is assembled in `src/jentic_one/shared/web/app_factory.py`
  (`SURFACE_MODULES`, `create_combined_app`, `create_surface_app`).
- **Broker execution path.** `src/jentic_one/broker/web/routers/execute.py`
  (`_handle`) is the web edge: discovery → `select_toolkit` → **PBAC evaluation**
  (`rule_evaluator.evaluate(...)`) → credential injection → pipeline → persist.
  The pipeline lives in `src/jentic_one/broker/services/execution/pipeline.py` and
  is driven by `run_execution` in
  `src/jentic_one/broker/services/execution/service.py`.
- **Execution records.** ORM at
  `src/jentic_one/admin/core/schema/execution_records.py`; write path
  `src/jentic_one/shared/executions/ingest.py` (`record_execution`); repo
  `src/jentic_one/admin/repos/execution_record_repo.py`; read API
  `src/jentic_one/admin/web/routers/executions.py` (`GET /executions`,
  `GET /executions/{id}`); view schema
  `src/jentic_one/admin/services/schemas/executions.py` (`ExecutionView`).
  Columns today: `id`, `toolkit_id`, `trace_id`, `started_at`, `duration_ms`,
  `status`, `operation_id`, `api_vendor/name/version/host`, `pinned_revisions`,
  `http_status`, `error`, `actor_id`, `actor_type`, `origin`. **No** `session_id`,
  `call_id`, `verdict`, `method`, `path`, or `credential_id`.
- **Governance verdict** is computed at
  `src/jentic_one/broker/repos/rule_evaluator.py` (`evaluate_rules`, first-match-
  wins, default-deny) and **not persisted**. An allow is only implied by the
  presence of a completed record; a deny emits a `broker.pbac_denied` event
  (`EventType.PBAC_DENIED` in `src/jentic_one/shared/models/events.py`) via
  `emit_event_best_effort` in `execute.py` and produces **no** execution record.
- **Events.** ORM `src/jentic_one/admin/core/schema/events.py`; single entry point
  `src/jentic_one/shared/events/__init__.py` (`emit_event`, `emit_credential_access`).
  `credential.accessed` events carry `credential_id`, `provider`, `wire_type`, api
  tuple in `data`, but their `execution_id` is `None` (emitted in
  `src/jentic_one/broker/services/credentials/orchestrator.py`, not linked to the
  execution). The UI's `AccessDenial` maps to `access_request.denied` events.
- **Control credentials.** ORM `src/jentic_one/control/core/schema/credentials.py`
  (`type`, `name`, `api_vendor/name/version`, `provider`, `active`, …). This is
  where gateway provider keys would live. **No** flag marking a credential as
  gateway-owned.
- **Config.** `AppConfig` in `src/jentic_one/shared/config.py` (line ~784); `apps`
  list defaults to `["registry","admin","control","auth"]`. No `llm_gateway`
  config block.

### What exists (UI)

- Module `ui/src/modules/llm-proxy/` mirrors the backend layer shape:
  - `api/types.ts` — the `SessionBundle` contract (`ProxySession`, `ProxyAgent`,
    `ProxyCall`, `ChatTurn`, `AccessDenial`, `ProxyCharts`, `FinalOutput`).
  - `api/client.ts` — repository tier; fetches `/proxy/sessions` and
    `/proxy/sessions/{id}` through the shared `apiRequest`, and **falls back to the
    bundled mock** on 404 / network error (`shouldUseLocalFallback`).
  - `api/hooks.ts` — TanStack Query hooks (`useSessions`, `useSession`).
  - `mocks/handlers.ts` — MSW serving `/proxy/sessions*` from `lib/mockData`.
  - `lib/mockData.ts` + `mocks/sessions-mock.json` — the demo dataset.
- Prototype evidence + generators in `ui/prototypes/session-data/`
  (`build_mock.py`, `enrich_mock.py`, `README.md`, the two real runs).

### What does NOT exist

- No `src/jentic_one/llm_gateway/` module (confirmed — glob returns nothing).
- No `/proxy/sessions` route anywhere in `src/` (confirmed — grep returns nothing).
- No `session_id` / `call_id` anywhere in the data model.
- No persisted verdict / method / path / credential on execution records.
- No LLM usage/metering store.
- `openapi-sketch.yaml` referenced by the design README is **missing** from the
  design folder (only the 6 markdown docs exist).

---

## 3. Target architecture — end-to-end data flow

The agent runtime is the hub. It talks to the **gateway** to think and to the
**broker** to act; the two never talk to each other. A thin client integration
mints `session_id` (per run) and `call_id` (per intended tool call) and stamps
them on both sides. A serving API joins the two logs plus events into one
`SessionBundle`.

```
                       ┌──────────────────────────────┐
                       │       AI agent runtime         │
                       │  (holds session_id; mints a    │
                       │   call_id per intended tool)   │
                       └───────┬───────────────┬────────┘
              think            │               │            act
   POST /llm/v1/chat/completions               │   POST /{upstream_url} (broker)
   headers: Jentic-Session-Id                  │   headers: Jentic-Session-Id,
            (+ call_id on the turn             │            Jentic-Call-Id
             that emits a tool_use)            │
             ▼                                 ▼
   ┌──────────────────────┐          ┌──────────────────────────┐
   │  LLM Gateway surface  │          │        Broker             │
   │  - model allowlist    │          │  - discovery              │
   │  - cred injection      │          │  - PBAC verdict (allow/   │
   │    (control store)     │          │    deny)  <-- persist it  │
   │  - forward to proxy    │          │  - cred injection         │
   │  - record usage/cost   │          │  - upstream call          │
   └──────────┬────────────┘          └──────────┬───────────────┘
              │ writes                             │ writes (enriched)
              ▼                                    ▼
   ┌──────────────────────┐          ┌──────────────────────────┐
   │  llm_usage_records    │          │   execution_records        │
   │  (metering store)     │          │   + session_id, call_id,   │
   │  session_id, call_id, │          │     verdict, method, path, │
   │  model, tokens, cost  │          │     credential_id          │
   └──────────┬────────────┘          └──────────┬───────────────┘
              └──────────────┬────────────────────┘
                             ▼   + events (denied, credential.accessed)
                 ┌─────────────────────────────────┐
                 │  GET /proxy/sessions[/{id}]       │
                 │  joins on session_id / call_id →  │
                 │  SessionBundle (types.ts)          │
                 └───────────────┬───────────────────┘
                                 ▼
                       ui/src/modules/llm-proxy  (mock fallback removed)
```

Mapping to the UI contract (`ui/src/modules/llm-proxy/api/types.ts`):

- A **`ProxyCall`** is a broker `execution_records` row, enriched with `session_id`,
  `call_id`, `verdict`, `method`, `path`, `credential_id`, and — from the joined
  gateway turn — `turn_id`, `tokens_in/out`, `cost_usd`.
- A **`ChatTurn`** is a gateway LLM turn (`first_user_msg`, `assistant_text`,
  `tool_uses`, `model`, `usage`, `latency_ms`), grouped by `session_id` and linked
  to a `ProxyCall` by `call_id`.
- A **`ProxyAgent`** tree is derived from the gateway's `Agent` tool-use spawns
  (parent turn → child session), all sharing a `root_session_id`. The broker cannot
  see this tree — it only exists in gateway data.
- **`AccessDenial`** rows come from `access_request.denied` events; PBAC denies come
  from newly-persisted deny verdicts (or `broker.pbac_denied` events until denies
  get their own record).
- **`ProxyCharts.calls_over_time`** and **`SessionTiles`** are aggregations over the
  joined calls.

---

## 4. Phase-by-phase plan

Phases are ordered by value and dependency. Phase 0 (the keystone) unblocks
everything the UI actually needs and is independent of the gateway; the gateway
(Phases 1–2) can proceed in parallel.

### Phase 0 — Correlation keystone (highest value, doc-undescribed)

**Scope.** Introduce `session_id` + `call_id` and enrich broker execution records
so a Session can be joined at all. No new UI.

**Client integration / headers.**
- Define two `Jentic-*` headers alongside the existing namespace in
  `src/jentic_one/broker/core/headers.py` (`JenticHeader`): `Jentic-Session-Id`
  and `Jentic-Call-Id`. Opaque UUID-ish strings, no PII.
- The agent-side client integration mints one `session_id` per run and one
  `call_id` per intended tool call, and sends them on both the broker
  `POST /{upstream_url}` and the gateway `POST /llm/v1/chat/completions`.
- Parse them at the broker edge in `execute.py::_context_from_discovery` and carry
  them on `ExecuteRequestContext`
  (`src/jentic_one/broker/core/schemas.py`) as `session_id` / `call_id`.
  Note: `trace_id` here is `"unknown"` for third-party callers — **do not** reuse
  it for grouping (this is the confirmed red herring).

**Persist the verdict, method, path, credential.**
- The verdict is already computed at `execute.py` line ~491
  (`rule_evaluator.evaluate(...)`), and the deny branch currently only emits
  `PBAC_DENIED` and raises `ActionDeniedError` with **no execution record**.
  Change: thread the boolean (and, ideally, the matched rule id/effect from
  `rule_evaluator`) into the persisted record; **also persist a record for
  preflight denies** (a terminal record with `verdict="deny"`, no upstream call)
  so denies land on the same timeline as allows.
- `method` and `path` are available at the edge (`method`,
  `urlparse(upstream_url).path`) — pass them into `record_execution`.
- `credential_id` is known at injection time (`resolved.credential_id` in
  `orchestrator.py`) — return it from `_resolve_credentials` and stamp it on the
  record (and set `execution_id` on the `credential.accessed` event so that link is
  no longer `None`).

**Data-model / migration changes** (admin schema — same schema as executions):
- Add nullable columns to
  `src/jentic_one/admin/core/schema/execution_records.py`: `session_id`,
  `call_id`, `verdict` (`allow`/`deny`), `method`, `path`, `credential_id`, and
  optionally `matched_rule_id` / `matched_effect`. New Alembic migration under
  `src/jentic_one/migrations/admin/versions/` (follow the existing
  `*_add_*_to_execution_records.py` files as templates; add indexes on
  `session_id` and `(session_id, started_at)`).
- Extend `record_execution`
  (`src/jentic_one/shared/executions/ingest.py`) and
  `ExecutionRecordRepository.create`
  (`src/jentic_one/admin/repos/execution_record_repo.py`) with the new kwargs.
- Extend `ExecutionView` / `ExecutionFilter`
  (`src/jentic_one/admin/services/schemas/executions.py`) so the new fields read
  back and can be filtered by `session_id`.

**Maps to UI contract:** fills `ProxyCall.session_id`, `call_id`, `verdict`,
`method`, `path`, `credential_id`, `execution_id` — the exact fields the mock
currently synthesises or derives (see `build_mock.py` `OP_MAP` deriving method/path
from `operation_id`).

**Effort:** medium. Mostly additive columns + threading values already in scope at
the call site; the one design decision is whether preflight denies get a record or
stay events-only (recommended: give them a record).

### Phase 1 — Backend LLM gateway module (per the design docs)

**Scope.** Build the `llm_gateway` surface exactly as
`jentic-one-internal/docs/designs/llm-gateway/module-interface.md` lays out, so
callers get an OpenAI-compatible surface, allowlist enforcement, credential
injection, and per-call usage capture.

**Files/modules to add** (new surface, mirrors `core / services / web`):
```
src/jentic_one/llm_gateway/
├── core/
│   ├── protocols.py     # LLMProxyClient, LLMAdminClient Protocols (module-interface.md)
│   ├── schemas.py       # ChatRequest, ChatResponse, UsageRecord, ProviderUsageRecord,
│   │                    #   AgentCredential, BudgetWindow
│   └── allowlist.py     # model allowlist enforcement (guardrails.md)
├── clients/
│   ├── litellm.py       # HTTP client → LiteLLM sidecar (base_url)
│   └── builtin.py       # built-in proxy (OQ-2: inline vs proxy/ submodule)
├── services/
│   ├── gateway.py       # GatewayService.chat(): precall checks → client → record usage
│   └── admin.py         # GatewayAdminService (wraps LLMAdminClient, admin-only)
└── web/
    ├── app.py           # OpenAI-compatible routes; get_routers()/install_on_app()
    └── deps.py          # get_ctx, require_identity
```

**Routes** (`routing.md`): `POST /llm/v1/chat/completions`, `GET /llm/v1/models`
(filtered by allowlist), `GET /llm/health` (unauth). RFC 9457 problem+json errors
(`model_not_allowed` 403, `credential_not_found` 403, `proxy_unavailable` 502,
`upstream_error` 502). `Jentic-Execution-Id` / `Jentic-Model` / `traceparent`
response headers — **and** echo `Jentic-Session-Id` / `Jentic-Call-Id` from
Phase 0 so gateway turns join to broker calls.

**Wiring.**
- Register in `SURFACE_MODULES` in
  `src/jentic_one/shared/web/app_factory.py` and add `"llm_gateway"` to the
  `apps` default / config in `src/jentic_one/shared/config.py` (`AppConfig`).
- Add an `LlmGatewayConfig` block to `config.py` (`provider: builtin|litellm|custom`,
  `base_url`, `timeout_s`) per `configuration.md`. The active `LLMProxyClient` is
  chosen from config at startup (Context-based wiring, like every other service).
- Credential injection reuses the `control` store
  (`src/jentic_one/control/core/schema/credentials.py`) — the gateway resolves a
  provider key and injects it as the outbound `Authorization` header to the proxy,
  the same pattern the broker uses.

**Allowlist vs PBAC.** The model allowlist (`guardrails.md`) is gateway-specific
policy, evaluated agent → user → global-default. It is **not** the broker's
`toolkit_permission_rules` PBAC. Keep them separate (see doc gap in §5): PBAC grants
access to the gateway surface; the allowlist governs which models within it.

**Metering hook.** `GatewayService.chat` calls `record_llm_usage(...)` after each
completed call (see Phase 2 for the store). It writes `session_id` / `call_id`
(from headers) so usage rows join to both the Session and the specific chat turn.

**Doc gap to close:** author the missing `openapi-sketch.yaml` in the design set
(or generate it from the FastAPI app) as part of this phase.

**Maps to UI contract:** produces the raw material for `ChatTurn`
(`model`, `usage`, `latency_ms`) and the token/cost fields on `ProxyCall`.

**Effort:** large (new surface, two client implementations, config, wiring).
Streaming is deferred (§6).

### Phase 2 — Metering store + spend reporting

**Scope.** Persist per-call LLM usage (`metering.md`) and expose spend reporting
through the admin surface.

**Data-model.** Open decision (OQ-3): co-locate with executions in the **admin**
schema or a new schema. Recommendation: a new `llm_usage_records` table in the
admin schema (alongside `execution_records`), so the serving API (§3) can join
sessions across both tables in one DB. Columns follow `ProviderUsageRecord`
(`module-interface.md`): `request_id`, `agent_id`/`actor_id`, `credential_id`,
`model`, `prompt_tokens`, `completion_tokens`, `total_tokens`,
`estimated_cost_usd`, `tags` — **plus** `session_id`, `call_id`, `started_at`.
New ORM under `src/jentic_one/admin/core/schema/`, migration under
`src/jentic_one/migrations/admin/versions/`, repo under
`src/jentic_one/admin/repos/`, and a `record_llm_usage` writer in
`src/jentic_one/shared/executions/` (sibling to `record_execution`) so the gateway
never imports admin ORM directly.

**Reporting.** Add spend-by-agent / user / model endpoints to the admin surface
(near `src/jentic_one/admin/web/routers/monitoring.py`), filterable by date range
and tags. Current-period spend is queried from the proxy on-demand via
`LLMAdminClient.get_agent_spend` (the proxy is source of truth for spend); the
`llm_usage_records` table is Jentic One's own call log for the Sessions view and
historical reporting, **not** a spend mirror.

**Budgets** are delegated to the proxy (`guardrails.md`); hard spend limits are out
of scope. `GatewayAdminService` write-through (create key / set budget) is optional
in this phase.

**Maps to UI contract:** completes `ChatTurn.usage`, `SessionTiles.cost_usd` /
`tokens`, and `ProxyCharts` cost aggregations.

**Effort:** medium.

### Phase 3 — Real `/proxy/sessions` serving API + UI cutover

**Scope.** Add the read API the UI already speaks and remove the mock fallback.

**Serving API** (mount near admin/monitor —
`src/jentic_one/admin/web/routers/` and register in
`src/jentic_one/admin/web/app.py::get_routers`, or a small `sessions` router on the
gateway surface; admin is the natural home since it owns executions + events):
- `GET /proxy/sessions` → list of `ProxySession` summaries: group
  `execution_records` + `llm_usage_records` by `session_id`, compute `tiles`
  (calls, agents, apis, cost, tokens) and `apis_touched`.
- `GET /proxy/sessions/{id}` → the full `SessionBundle`:
  - `calls[]` — execution records for the session (with verdict/method/path/
    credential from Phase 0), joined by `call_id` to the gateway turn for
    tokens/cost/`turn_id`.
  - `chat[]` — gateway LLM turns for the session.
  - `agents[]` — tree derived from `Agent` tool-use spawns in the turns.
  - `denials[]` — `access_request.denied` events for the actor/session.
  - `charts.calls_over_time` — 1-minute buckets by verdict/outcome.
  - `final_output` — the root agent's closing synthesis, if recorded.
- **Redaction:** any request/response body or chat text passes through
  `src/jentic_one/shared/redaction.py` before leaving the server (see §6).

**UI cutover** (types unchanged):
- Remove the mock fallback in `ui/src/modules/llm-proxy/api/client.ts`
  (`shouldUseLocalFallback`, `bundleForLocal`, `listSessionsLocal`) so failures
  surface as real errors.
- Remove `ui/src/modules/llm-proxy/mocks/handlers.ts` (and its entry in the
  append-only `ui/src/mocks/handlers.ts` registry), `lib/mockData.ts`, and
  `mocks/sessions-mock.json`.
- Reconcile field names once against the real OpenAPI and regenerate the shared
  client if needed (`@/shared/api`). Keep `api/types.ts` as the contract.

**Maps to UI contract:** this *is* the `SessionBundle` producer; near-zero view
churn because `types.ts` was designed to this shape.

**Effort:** medium (the join logic) + small (UI deletion).

### Phase 4 — UI adjustments

**Scope.** Small follow-ups once real data flows.

- Drop the `synthesised` flag handling — delete the 2 synthesised demo calls' code
  paths; every `ProxyCall` is now real (per `build_mock.py`'s provenance rule:
  "delete every `synthesised: true` object").
- Wire any newly-available real fields (e.g. `rule` / `matched_rule_id`,
  `scopes_required/granted`, `grant_hint`) now that the backend persists verdict +
  rule metadata.
- **Content redaction UX.** The design set lists prompt/response content logging as
  a non-goal (`README.md` out-of-scope), yet the UI's Level-3 drawer shows chat
  turns and request/response snippets. Ensure the UI only ever renders
  server-redacted content (never raw), and clearly label estimated cost/tokens as
  "est." (the tiles already do).

**Effort:** small.

---

## 5. Doc gaps to fix in the design set

The design docs describe a solid gateway but leave the Sessions story incomplete.
Fixes to land in `jentic-one-internal/docs/designs/llm-gateway/`:

1. **Session/call correlation model is undocumented.** The docs give the gateway a
   per-call `Jentic-Execution-Id` (`routing.md`) but define no `session_id` /
   `call_id`, and never mention stamping the broker. This is the keystone
   (Phase 0). Add a short "correlation" design doc describing the two ids, who mints
   them (the client integration), how they flow as `Jentic-*` headers, and where
   each side persists them.
2. **Broker-governance vs model-allowlist conceptual mismatch.** `guardrails.md`
   flags the PBAC-vs-allowlist relationship as TBD. The Sessions UI shows a single
   `verdict` (allow/deny) that today comes only from the broker's
   `toolkit_permission_rules`. Clarify that the gateway's model allowlist and the
   broker's PBAC are **different gates** producing verdicts on **different call
   types** (LLM turn vs tool call), and that a Session interleaves both.
3. **Content-logging non-goal vs UI showing chat.** `README.md` lists
   prompt/response content logging as out of scope, but the Sessions Level-3 drawer
   renders chat turns + request/response snippets. Reconcile: either scope in
   *redacted* content capture explicitly (routed through
   `shared/redaction.py`), or state the UI shows only gateway turn metadata +
   redacted snippets, never stored raw content.
4. **Missing `openapi-sketch.yaml`.** Referenced in `README.md`'s document table
   but absent from the folder. Author it (or generate from the FastAPI app) in
   Phase 1.
5. **TBD statuses everywhere.** Every design doc row in `README.md` is marked
   `TBD`, and several specifics (metering schema/location, PBAC interaction,
   built-in proxy topology OQ-2, credential flagging OQ-5) are explicitly deferred.
   Resolve them during implementation planning and update the docs (this plan
   proposes concrete answers in §6).

---

## 6. Open questions / decisions needed

- **Where the metering store lives (OQ-3).** Recommendation: `llm_usage_records`
  in the **admin** schema alongside `execution_records`, so the serving API joins
  sessions across both in one DB without cross-schema queries. Alternative: a
  dedicated schema if LLM data volume/retention differs materially.
- **Preflight-deny records.** Should a PBAC deny produce an `execution_records` row
  (with `verdict="deny"`, no upstream call) instead of an event only? Recommended
  **yes** — it puts denies on the same timeline the UI already draws and removes the
  "denies live in a different table" gotcha. Decide the status value for a deny row.
- **How gateway-owned provider creds are flagged in `control` (OQ-5).** Options: a
  `kind`/`purpose` field on `credentials`, a dedicated credential subtype, or a tag.
  Recommendation: a nullable `purpose` column (`"gateway"`) so the gateway resolver
  filters cleanly and it stays orthogonal to the polymorphic `type`.
- **Built-in proxy topology (OQ-2).** Inline in the gateway vs a `proxy/` submodule.
  Recommendation: start inline (narrow scope: OpenAI + Anthropic), refactor to a
  submodule only if it grows multi-provider.
- **Streaming (deferred).** `routing.md`/`metering.md` describe SSE passthrough with
  a partial-stream `incomplete: true` metering edge case. Keep deferred; when built,
  usage is read from the final chunk. The Sessions view already treats cost/tokens
  as "est.".
- **Redaction policy.** Confirm exactly what the serving API may return: redacted
  request params + body, a short redacted response snippet, and gateway turn text —
  all through `shared/redaction.py`. This gates whether the content-logging non-goal
  needs relaxing (doc gap #3).
- **`session_id` / `call_id` minting owner.** Confirmed as the client integration
  (the thin layer the agent runtime uses). The gateway/broker only read + persist;
  they should tolerate absent ids (degrade to a flat, ungrouped call list, per the
  "1 agent, N calls" graceful-degradation rule).

---

## 7. Suggested sequencing & rough effort

Dependency graph:

```
Phase 0 (keystone) ──┬──> Phase 3 (serving API) ──> Phase 4 (UI cutover)
                     │
Phase 1 (gateway) ───┼──> Phase 2 (metering) ──────┘
                     │        (Phase 3 needs 0 for calls;
                     └────────  1+2 for chat/usage)
```

- **Start Phase 0 first and alone.** It is the highest-value, doc-undescribed gap,
  independent of the gateway, and it makes the *tool-call* half of every Session
  real. After Phase 0, the serving API could already return real `calls[]` +
  `denials[]` (no chat yet) — a meaningful partial cutover.
- **Run Phase 1 (gateway) in parallel** with Phase 0 — different files, different
  surface. Phase 2 (metering) depends on Phase 1.
- **Phase 3 (serving API)** needs Phase 0 for calls and Phases 1–2 for chat/usage;
  it can ship incrementally (calls-only, then chat/usage joined in).
- **Phase 4 (UI)** is last and small; do it as the serving API stabilises.

Rough effort: **Phase 0** medium (~additive migration + threading), **Phase 1**
large (new surface + 2 clients + config/wiring), **Phase 2** medium, **Phase 3**
medium (join logic) + small (UI deletion), **Phase 4** small. Critical path to a
"real UI, tool calls only" milestone is just Phase 0 → Phase 3(partial); the full
chat/cost experience needs Phases 1–2 as well.


