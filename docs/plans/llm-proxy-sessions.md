# Plan: LLM Proxy — Sessions view

> Branch: `feat/llm-proxy-sessions`
> Status: **planning only** — no product code yet. This doc + a single mock JSON
> are the deliverables. Nothing ships until this plan is approved.

---

## 0. TL;DR — what we are building, in one breath

A new top-level tab in the Jentic One admin UI called **LLM Proxy**. It answers
one question for whoever runs a self-hosted Jentic One: *"What did my agents
actually do, and did governance behave?"*

It has three depths:

1. **Overview** — 5 metric tiles + one chart (calls over time, stacked by
   allow/deny/error) + a **Sessions table** (one row per agent run).
2. **Session playground** — click a session and see the whole agent → subagent →
   sub-subagent tree as an interactive **trace-flow**: each agent's chain of tool
   calls flows left-to-right and merges into a final output. Hover a node for
   high-level stats; click a block to open the chat/tool detail.
3. **Deep dive** — a drawer/panel for a single tool call or chat turn with every
   field we have (method, path, verdict, credential used, latency, tokens, cost,
   raw request/response when available).

The data model is designed around **one thing we do not have today and must add:
a correlation id** that ties a model call (to the proxy) and a tool call (to the
broker) into the same session. Everything else already exists in some form.

---

## 1. The mental model — who talks to whom

This is the single most misunderstood part, so it is first. There are **three**
independent actors. The **agent is the hub**; the proxy and the broker never talk
to each other.

```
                    ┌─────────────────────────┐
                    │   The AI agent runtime   │
                    │ (Claude Code / Hermes /  │
                    │  the customer's app)     │
                    └───────────┬──────────────┘
                    thinks      │      acts
              POST /chat/…      │      POST /execute
             (messages, tools)  │     (op: airlabs.get_flights)
                    ┌───────────┴───────────┐
                    ▼                        ▼
          ┌──────────────────┐     ┌───────────────────┐
          │   LLM Proxy       │     │   Jentic Broker    │
          │   (LiteLLM)       │     │   (PBAC + execute) │
          │  logs the         │     │  allows/denies,    │
          │  reasoning/chat   │     │  runs the API call │
          └──────────────────┘     └───────────────────┘
```

- The agent calls the **proxy** to *think* (`POST /chat/completions` with the
  running message list + the tool schemas it is allowed to use). The proxy
  forwards to the real model, logs the round-trip, returns the model's answer —
  which may contain a `tool_use` ("I want to call `airlabs.get_flights`").
- The agent runtime *executes that tool* by calling the **broker**
  (`POST /execute`). The broker evaluates PBAC (allow/deny), injects the
  credential, calls the upstream API, records an `ExecutionRecord`.
- The agent feeds the tool result **back to the proxy** on the next
  `POST /chat/completions` (`…, tool_result`) so the model can continue.

The proxy therefore sees **chat + reasoning + which tools the model wanted**. The
broker sees **the actual API calls + governance verdict + credential + upstream
result**. Neither sees the whole picture alone. The Sessions view is the join.

### Why the customer's setup still lets us see this

Jentic One is **self-hosted, single-tenant**: the customer runs both the broker
*and* the LLM proxy inside their own deployment (the proxy is a planned part of
the Jentic One stack, not ours in the cloud). So both logs are the customer's own
data, sitting in their own Postgres. We are not exfiltrating transcripts from a
third party — we are reading two local logs the customer already owns and
correlating them.

---

## 2. What we already have today (proven by a real run)

On 2026-07-15 we ran a real multi-agent task through this exact stack (main
agent → 3 subagents → 6 sub-subagents; 35 real API calls to AirLabs + Google
Sheets, including GET/POST/PUT and a destructive clear). The captured data lives
in `ui/prototypes/session-data/` and is the evidence base for this plan.

| Source | Where it lives today | What it gives us | Count in the run |
| --- | --- | --- | --- |
| **Broker `execution_records`** | admin DB, exposed via `GET /executions` | actor_id, api vendor/name/version, `operation_id`, http_status, duration_ms, status, started_at, error, origin | 35 |
| **`events`** | admin DB, exposed via `GET /events` (+ SSE) | `credential.accessed` (credential_id, provider, wire_type per API), `access_request.denied`, toolkit/agent lifecycle | 87 |
| **LLM proxy capture** | LiteLLM callback → JSONL (planned: a table) | full chat transcript, system prompt, the model's `tool_use` intents, per-call token usage + latency, subagent-spawn `Agent` tool calls | 38 round-trips |

So the **raw material for chat + tool calls + credentials + verdicts all exists**.
What is missing is the glue.

---

## 3. The five gaps (what we must add) — each proven by the run

1. **No correlation id.** `execution_records.trace_id` was literally `"unknown"`
   for every one of the 35 calls. There is nothing on a broker record that says
   "this belongs to session X" or "this is the tool the model asked for in chat
   turn Y". This is the keystone gap.
2. **Governance columns exist but are empty.** The record has no `verdict`,
   no rule id, no matched-rule reason. Allows are implied by the presence of a
   completed record; denies live in a *separate* `events` row
   (`access_request.denied`) with only a `request_id` — not joined to anything.
3. **`credential.accessed` events are not linked to the execution.** Their
   `execution_id` was `None`; we can only match them to a call by API name, not
   precisely. We want the credential stamped on the execution itself.
4. **No method / path on the record.** The broker stores `operation_id`
   (`op_4fa4cf71b51cf…`) but not the human-facing `GET /flights`. We can derive
   it from the API spec (this plan's mock does exactly that), but the backend
   should store it so the UI never has to.
5. **No session/subagent identity.** All 35 calls share one `actor_id`
   (`agnt_…`). The broker cannot see that they came from 9 different subagents —
   only the proxy saw the `Agent` spawns. Session + agent tree can only be
   reconstructed by joining proxy ↔ broker on the correlation id from gap #1.

---

## 4. The keystone fix — `session_id` + `call_id`

The Jentic client integration (the thin layer the customer's agent runtime uses
to reach both the proxy and the broker) mints two ids and stamps them on **both**
sides:

- **`session_id`** — one per agent run. Stamped on every `POST /chat/completions`
  (proxy) and every `POST /execute` (broker) for that run. This is what groups a
  table row and populates the playground.
- **`call_id`** — one per *intended tool call*. The model emits a `tool_use`
  (proxy sees it); the runtime forwards the same `call_id` to `POST /execute`
  (broker sees it). This is the precise chat-turn ↔ execution join, and lets the
  trace-flow draw "the model asked for X → the broker allowed/denied → result".

Both are opaque strings the client generates (UUID-ish). No PII. They flow as
headers/body fields; the broker persists them on `execution_records`, the proxy
persists them on its capture row. The Sessions API then joins on them.

Subagent identity (the tree) is derived from the proxy's `Agent` tool_use spawns
(parent turn → child session), so depth ≥ 1 nodes get their own `session_id`
child, all sharing a `root_session_id`.

---

## 5. The mock data — one JSON, the single source of truth

While there is no backend, the UI reads **one file**:
`ui/prototypes/session-data/mock/sessions-mock.json` (generated by
`build_mock.py` from the real run). When the backend lands, the shape of the API
responses matches this file section-for-section, so we swap the import for
fetches with near-zero UI churn.

Top-level keys:

| Key | What it is | How real it is |
| --- | --- | --- |
| `sessions[]` | one row per agent run; carries `tiles` (calls/agents/apis/cost/tokens) + `apis_touched` | run is real; cost/tokens synthesised |
| `agents[]` | the tree: `id, parent_id, depth, role, subagent_type, name`, plus `stats` (own) and `rollup` (subtree) | tree shape real (main→3→6); leaf assignment deterministic |
| `calls[]` | one per tool call: `call_id, session_id, agent_id, method, path, operation_id, summary, verdict, status, http_status, duration_ms, credential_id/provider/wire_type, trace_id, tokens_in/out, cost_usd, destructive` | 35 real broker calls + method/path derived from spec + credential from events; **2 synthesised** calls (1 deny, 1 error) flagged `"synthesised": true` so all verdict/outcome states render |
| `chat[]` | one per proxy round-trip: `first_user_msg, assistant_text, tool_uses[], model, usage, latency_ms` | fully real from the proxy capture |
| `denials[]` | the real `access_request.denied` events (`request_id`, actor) | fully real |
| `charts.calls_over_time[]` | 1-minute buckets `{t, allow, deny, error}` for the stacked chart | real timestamps |

**Provenance rule:** anything that came from the run is real; anything we could
not observe is either *derived from the API spec* (method/path) or *synthesised*
and flagged. The `note` field in the JSON restates this. When wiring the real
backend, delete every `"synthesised": true` object.

---

## 6. The UI spec

Tab name: **LLM Proxy** (top-level nav, sibling of Monitor). Inside it, the
"Sessions" experience. Module lives at `ui/src/modules/llm-proxy/` mirroring the
backend surface, following the repo's Router→Service→Repository frontend rule
(views → `api/hooks` → `api/client` → `@/shared/api`).

### 6.1 Overview (Level 1)

- **5 metric tiles**, no noise, **no "denied" tile** (per your call): `Sessions`,
  `Tool calls`, `APIs touched`, `Est. cost`, `Tokens`. (Denies are visible in the
  chart + as a red segment on rows — a tile would over-index on failure.)
- **One primary chart** directly below tiles: **calls over time, stacked by
  outcome** (allow / deny / error). A quiet **"See more charts"** button reveals
  secondary charts later (cost over time, calls by API, latency histogram) —
  built behind that button so the default view stays clean.
- **Sessions table** — much richer than the prototype: columns = agent/session
  title, started, duration, #agents (with a tiny tree glyph), #calls, a compact
  allow/deny/error mini-bar, APIs (logo chips), est. cost, status. Full filter
  bar (see 6.4). Row click → playground.

### 6.2 Playground (Level 2) — the trace-flow

We commit to **idea 7b, the interactive trace-flow**, informed by 7a's hover:

- The root agent is the leftmost node. Its subagents branch out; each subagent's
  **tool calls render as a horizontal chain of blocks that flow rightward and
  merge into that subagent's final output**, which flows back up into the parent.
- **Nodes (agents):** hover → a card with `rollup` stats (calls, allow/deny/error
  split, cost, tokens, APIs). The card **persists while you scroll** (pinned) so
  you can compare — this is where 7a's idea is kept.
- **Blocks (tool calls):** color-coded by verdict (green allow / red deny /
  amber error). Click → opens the deep-dive drawer (Level 3). Destructive calls
  get a small warning glyph.
- **Chat is secondary but present:** each agent node has a "chat" affordance;
  clicking a *block* can also show the exact chat turn that requested it (via
  `call_id`). Priority is tool calls; chat is one click away, never in your face.
- Layout: think a clean DAG/flow canvas (pan/zoom), not a spreadsheet. Depth-2
  nodes collapse into their parent until expanded, so the default view is
  legible even with 9 agents.

### 6.3 Deep dive (Level 3) — single call / turn

A right-side drawer. For a **tool call**: method + path, operation_id, verdict
with rule reason, credential used (provider + wire_type, id redacted-friendly),
http_status, duration, tokens/cost, upstream error if any, and the linked chat
turn. For a **chat turn**: model, system-prompt hash, the user message,
assistant text, the tool_uses it emitted, token usage + latency.

### 6.4 Filters (Sessions tab)

Date range, agent (by name), API/vendor, verdict (allow/deny/error), method
(GET/POST/PUT/…), destructive-only, status, min duration, has-denials. Filters
apply to both the table and, when inside a session, the playground's visible
blocks.

---

## 7. Phasing (what order we build in, once approved)

- **Phase 0 (this branch):** plan doc + mock JSON. ✅
- **Phase 1 — UI on mock:** module scaffold, nav tab, tiles, primary chart,
  Sessions table with filters, reading only `sessions-mock.json`. No backend.
- **Phase 2 — Playground on mock:** the trace-flow canvas, hover/pinned stats,
  clickable blocks, deep-dive drawer.
- **Phase 3 — polish:** secondary charts behind "See more charts", export,
  saved filters.

Scope you asked to polish first: **Phases 1–2** (tab, tiles, chart, table,
playground) on the mock.

### Future scope — connect the real backend

Everything above runs entirely on `sessions-mock.json`. Wiring it to a real
backend is deliberately **out of scope for now** and tracked as future work. When
we pick it up, it is roughly two steps:

1. **Record the data at the source.** Add `session_id`/`call_id` to the client
   integration + broker + proxy so calls can be grouped into sessions (the
   keystone fix from §4); stamp `verdict`, `method`, `path`, `credential_id` onto
   `execution_records`; persist proxy captures to a table. This fills the five
   gaps in §3 and produces no new UI.
2. **Serve the data + cut over.** Expose `GET /sessions` and `GET /sessions/{id}`
   (tree + calls + chat joined on the ids), then swap the mock-JSON import for
   these fetches. Because the mock is already shaped to the target API schema
   (§5), this cutover is near-zero UI churn.

---

## 8. Gotchas we already know about

- **`trace_id` is a red herring** — it is `"unknown"` for third-party clients;
  do not group sessions by it. Group by the new `session_id`.
- **Denies live in a different table** than allows today. Until the real backend
  lands the playground must merge `calls[]` (allows) with `denials[]`/synthesised
  denies to show the full governance picture; the mock already does this.
- **Credential precision:** matching by API name is lossy when two toolkits share
  an API. The real backend must stamp `credential_id` on the execution to be exact.
- **Subagents are invisible to the broker.** The tree *only* exists in proxy
  data; if the proxy is disabled the UI degrades to a flat call list (still
  useful). Design the playground to handle "1 agent, N calls" gracefully.
- **Redaction:** proxy transcripts and upstream request/response bodies can carry
  secrets/PII. Anything shown in Level 3 must go through `shared/redaction.py`
  before it reaches the client.
- **Cost/tokens are estimates** unless the proxy's `usage` is trustworthy per
  model; label them "est." in the UI (the tiles already say "Est. cost").
- **Mock ≠ contract yet.** Treat `sessions-mock.json` as the *proposed* API
  shape. When the real backend defines the OpenAPI, reconcile field names once and
  regenerate the client.

---

## 9. Files in this branch

- `docs/plans/llm-proxy-sessions.md` — this plan.
- `ui/prototypes/session-data/mock/sessions-mock.json` — the consolidated mock.
- `ui/prototypes/session-data/build_mock.py` — generator (real run → mock).
- `ui/prototypes/session-data/this_run_*.{json,jsonl}` — raw evidence from the run.
- `ui/prototypes/session-data/README.md` — dataset guide.
- `ui/prototypes/sessions.html` — the earlier throwaway prototype (kept for
  reference only; the real build supersedes it).

