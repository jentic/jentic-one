# Frontend Sessions — what we built, and the data we'd need from the backend

> **Audience:** the backend engineer who owns the `llm-gateway` design in
> `jentic-one-internal/docs/designs/llm-gateway/`.
>
> **Purpose:** This is *not* a spec I'm asking you to build to the letter. It's a
> frontend-side "here's what we found" note. I built an experimental **LLM Proxy /
> Sessions** observability screen against two *real* captured runs, and this doc
> lays out (1) what that UI is, (2) the exact values it needs to show, (3) where
> each value comes from today (real vs. derived vs. faked), and (4) my
> **suggestions** for how the backend could supply them. Everything in the
> "suggestion" columns is a proposal — **you decide what's actually correct**,
> and then we finalise the contract together. 
>
> **Deeper background (optional):** a fuller internal implementation plan lives at
> `docs/plans/llm-gateway-and-sessions-implementation.md`. That one goes into
> backend file paths and phasing. This doc stays at the contract level.

---



## 1. TL;DR

- I built a **frontend-only** Sessions UI. It runs today on a single bundled JSON
file (`ui/src/modules/llm-proxy/mocks/sessions-mock.json`) — **no backend**.
- The mock was **modelled on two real runs** we captured on 2026-07-15 (a
"flight-ops" run and a "markets-media-brief" run), using the two trusted sources
we already have: the **LiteLLM proxy** capture and the **Jentic broker**
execution records + events.
- The UI's whole reason for existing is to **join two logs that never see each
other**: the LLM "thinking" (proxy) and the API "acting" (broker). Doing that
join needs one thing we don't have yet: a **shared correlation id**.
- Below is the field-by-field list of what the screen shows, and for each field:
what it is, why the UI needs it, whether it's **real / derived / synthesised**
today, and **my suggested source** from the backend. The `llm-gateway` design
covers the "thinking" half well; the gaps are mostly around the "acting" half and
the correlation glue.

The UI's data contract (the shapes below) lives in
`ui/src/modules/llm-proxy/api/types.ts`. It's designed so that when the real
backend lands, we reconcile the types **once** and the views don't change.

One thing up front on **your gateway design specifically**: I read it, and the
short version is the gateway's *output* (model, tokens, cost) is **already
surfaced** in our Sessions screen — but the gateway's *admin/config* side
(model allowlist editor, spend/budget dashboards, provider topology) has **no UI
yet, on purpose**. Worth noting the framing: both the UI I built **and** the
design doc I wrote are **session / observability focused** — i.e. "what did the
agents do in a run" — rather than gateway-configuration focused. That's why the
gateway's admin surface sits outside what we've built so far. My reasoning and a
proposal are in §6.

---



## 2. The experiment: what we tried and what it proved

We didn't design against guesses. We froze two real Claude Code runs and built the
UI against them (`ui/prototypes/session-data/`).

- **Run 1 "flight-ops"** (`README.md` in that folder): main agent → 3 subagents →
each 2 sub-subagents; real AirLabs + Google Sheets calls through the broker;
a full governance arc (denied → create toolkit → bind → allowed).
  - `this_run_proxy.jsonl` — **38 model calls** (the thinking half).
  - `this_run_execution_records.json` — **35 rows** (the real API calls).
  - `this_run_events.json` — **87 events** (denials, credential access, etc.).
- **Run 2 "markets-media-brief"** (`RUN2-README.md`): wider tree (7 subagent
spawns), Finnhub + NYTimes + TMDB into one Google Sheet, plus a real TMDB 401
auth-fix loop.
  - `run2_proxy.jsonl` — **134 model calls**.
  - `run2_execution_records.json` — **113 rows** (104 completed / 9 failed).
  - `run2_events.json` — **253 events**.



### The three things these real runs proved (this is the important part)

1. **The two halves are real and separate.** The proxy's `tool_uses` are
  *framework* tools (`Agent`, `Bash`, `Skill`, `Write`, `TaskUpdate`) — the real
   `GET /flights` / `POST /spreadsheets` calls are **inside the Bash command
   string**, invisible to the proxy. They only appear as structured rows in the
   broker's `execution_records`. → **The UI must merge both sources; neither alone
   tells the story.**
2. **The subagent tree lives only in the proxy** (via `Agent` tool-use spawns +
  distinct system prompts per agent). The broker cannot see the tree at all.
3. **There is no key to stitch them together.** In *both* runs, every
  `execution_records` row has `trace_id = "unknown"`, and `events.trace_id` is
   `NULL`. So today there is no native way to (a) group N calls into one run,
   (b) attribute a call to a specific subagent, or (c) link a call to the proxy
   turn that caused it. **This missing correlation id is the single biggest thing
   the UI needs from the backend.**

A note on run 2: it did improve one thing — the `execution.completed/failed`
events now carry an `execution_id` that joins cleanly to `execution_records.id`
(in run 1 that was `None`). So execution↔event linkage is close; run↔call and
turn↔call correlation is still missing.

---



## 3. The data-flow we're assuming (and where the gap is)

```
                    ┌─────────────────────────────────────┐
                    │           AI agent runtime            │
                    │  (one run = one session; each intended│
                    │   tool call could carry a call id)    │
                    └───────┬───────────────────────┬───────┘
             "think"        │                       │       "act"
   proxy / llm-gateway  ────┘                       └────  broker
   sees: chat, model, tokens,                       sees: real REST call,
         latency, framework tool_uses,                    verdict, credential,
         subagent spawns                                  http_status, duration
             │                                                 │
             ▼                                                 ▼
     proxy capture / gateway usage                     execution_records + events
             │                                                 │
             └───────────────┬─────────────────────────────────┘
                             ▼   ← THE GAP: no shared id to join on
                  GET /proxy/sessions[/{id}]  →  one SessionBundle
                             ▼
                  ui/src/modules/llm-proxy  (today: mock JSON instead)
```

Everything the UI shows is a projection of that joined bundle. The endpoints the UI
already calls (and would love the backend to eventually serve) are:

- `GET /proxy/sessions` → a list of session summaries.
- `GET /proxy/sessions/{id}` → the full `SessionBundle` for one run.

These paths/shapes are just our current assumption — rename/reshape them however
fits the backend; we'll reconcile the types once.

---



## 4. The values the UI needs (field-by-field)

Legend for the **"today"** column:

- **Real** — taken straight from a trusted source (proxy or broker/events).
- **Derived** — computed by us from a real value (e.g. method from `operation_id`).
- **Synthesised** — *made up by us* so the screen could render; **not** from the run.

The **"Suggested source"** column is my proposal only — your call on what's right.

### 4.1 Session (one agent run) — `ProxySession`

One row in the top-level table; one run.


| Field                     | What it is / why the UI needs it                                      | Today                                 | Suggested source                                                                                      |
| ------------------------- | --------------------------------------------------------------------- | ------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `id`                      | The session id — groups all calls/turns of one run. **The keystone.** | **Synthesised** (we minted it)        | A real `session_id`, minted once per run by the agent/client integration, sent to both proxy & broker |
| `title`                   | Human label for the run (e.g. "Flight-Ops…")                          | Derived (from the agent's first task) | First user message / task summary from the proxy turn                                                 |
| `agent_id` / `actor_id`   | Which agent/actor ran it                                              | **Real** (broker + proxy)             | Broker `actor_id`; proxy agent id                                                                     |
| `started_at` / `ended_at` | Run time span                                                         | Derived (min/max of call times)       | Min/max timestamp across the session's calls+turns                                                    |
| `status`                  | Overall run state                                                     | Derived                               | Aggregate of call outcomes                                                                            |
| `tiles`                   | Headline counts: calls, agents, apis, **cost_usd, tokens**            | Mixed (see below)                     | calls/agents/apis real; cost/tokens from gateway usage                                                |
| `apis_touched`            | Which vendors were hit                                                | **Real** (broker api tuple)           | Broker `api_vendor` distinct list                                                                     |




### 4.2 Tool call (one broker execution) — `ProxyCall`

The heart of the screen. One real API call the agent made through the broker.


| Field                                                            | What it is / why the UI needs it                     | Today                                                                                                                                     | Suggested source                                                                                         |
| ---------------------------------------------------------------- | ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `execution_id`                                                   | Links to the broker execution row                    | **Real** (run 2) / `None` (run 1)                                                                                                         | `execution_records.id`                                                                                   |
| `call_id`                                                        | Correlates this call to the chat turn that caused it | **Synthesised**                                                                                                                           | A real `call_id` minted per intended tool call, echoed by proxy turn + broker                            |
| `session_id`                                                     | Groups the call into its run                         | **Synthesised**                                                                                                                           | Same `session_id` as the session                                                                         |
| `agent_id` / `actor_id` / `actor_type`                           | Who made the call                                    | **Real**                                                                                                                                  | Broker record                                                                                            |
| `api_vendor` / `api_name` / `api_version`                        | Which API                                            | **Real**                                                                                                                                  | Broker api tuple                                                                                         |
| `operation_id`                                                   | The API operation                                    | **Real**                                                                                                                                  | Broker record                                                                                            |
| `method` / `path`                                                | HTTP verb + path (shown in the call row)             | **Derived** (from `operation_id` via the registry)                                                                                        | Persist on the broker record (available at the broker edge) — or we keep deriving                        |
| `summary`                                                        | Short human description of the call                  | Derived                                                                                                                                   | From operation metadata                                                                                  |
| `verdict` (`allow`/`deny`)                                       | Did governance allow it? Drives the color coding     | **Synthesised**                                                                                                                           | **The broker already computes this** (PBAC `rule_evaluator`) but doesn't persist it — please persist it  |
| `status` (`completed`/`denied`/`failed`)                         | Outcome                                              | **Real** for completed/failed; deny synthesised                                                                                           | Broker record; denies need a home (see §5)                                                               |
| `http_status`                                                    | Upstream status code                                 | **Real**                                                                                                                                  | Broker record                                                                                            |
| `duration_ms`                                                    | How long it took                                     | **Real** (but broker-internal only, ~ms)                                                                                                  | Broker record — note it's *not* end-to-end latency                                                       |
| `started_at`                                                     | When                                                 | **Real**                                                                                                                                  | Broker record                                                                                            |
| `error`                                                          | Error text on failure                                | **Real**                                                                                                                                  | Broker record                                                                                            |
| `destructive`                                                    | Is it a write/delete? (UI warns)                     | Derived                                                                                                                                   | Operation metadata                                                                                       |
| `credential_id` / `credential_provider` / `credential_wire_type` | Which credential was used                            | **Partially real** — `credential.accessed` events carry `credential_id` but don't link to the execution (join by api+timestamp heuristic) | Stamp `credential_id` on the execution record, and set `execution_id` on the `credential.accessed` event |
| `trace_id`                                                       | (legacy)                                             | **Real but useless** — always `"unknown"`                                                                                                 | Don't use for grouping; replaced by `session_id`                                                         |
| `tokens_in` / `tokens_out` / `cost_usd`                          | Per-call token/cost                                  | **Synthesised**                                                                                                                           | Gateway usage record, joined by `call_id` (these are LLM-turn costs, not REST-call costs)                |
| `turn_id`                                                        | The chat turn that produced this call                | **Synthesised**                                                                                                                           | Join on `call_id`                                                                                        |
| `request` (params + body, redacted)                              | What was sent (Level-3 drawer)                       | Derived / redacted                                                                                                                        | Broker, **redacted** server-side                                                                         |
| `response_snippet`                                               | Short redacted response                              | Synthesised                                                                                                                               | Broker, **redacted** server-side                                                                         |
| `timeline` (queued/policy/credential/upstream ms)                | Lifecycle bar in the drawer                          | **Synthesised**                                                                                                                           | Broker stage timings, if available (nice-to-have)                                                        |
| `rule` (matched governance rule)                                 | Which rule matched (deny explainer)                  | **Synthesised**                                                                                                                           | The PBAC `rule_evaluator` knows the matched rule id/effect — persist it                                  |
| `scopes_required` / `scopes_granted` / `grant_hint`              | "how to fix a deny" help                             | **Synthesised**                                                                                                                           | Governance metadata (nice-to-have)                                                                       |
| `synthesised`                                                    | **Internal flag** we set on the 2 fake demo rows     | n/a                                                                                                                                       | Delete entirely once data is real                                                                        |


> Note on the two fake rows: the mock includes **one deny + one error call that
> never happened**, flagged `"synthesised": true`, purely so the UI could show all
> three outcome states (allow/deny/error). They should vanish the moment real data
> flows.



### 4.3 Chat turn (one LLM round-trip) — `ChatTurn`

The "thinking" half. This is exactly what the `llm-gateway` design already produces.


| Field                               | What it is / why the UI needs it                               | Today              | Suggested source                                       |
| ----------------------------------- | -------------------------------------------------------------- | ------------------ | ------------------------------------------------------ |
| `turn_id`                           | Id for the turn                                                | Derived            | Gateway turn / usage record id                         |
| `agent_id`                          | Which agent was thinking                                       | **Real** (proxy)   | Gateway (per-turn identity)                            |
| `ts`                                | When                                                           | **Real**           | Gateway                                                |
| `model`                             | Which model                                                    | **Real**           | Gateway (`ChatResponse.model`)                         |
| `n_messages`                        | Thread length                                                  | **Real**           | Gateway                                                |
| `first_user_msg` / `assistant_text` | The prompt + reply (Level-3 drawer)                            | **Real** (but raw) | Gateway, **redacted** (see content-logging note in §5) |
| `tool_uses`                         | Framework tools the turn emitted (incl. `Agent` spawns → tree) | **Real**           | Gateway `response_tool_uses`                           |
| `latency_ms`                        | Turn latency                                                   | **Real**           | Gateway                                                |
| `usage`                             | Token usage object                                             | **Real**           | Gateway `UsageRecord`                                  |
| `status`                            | Turn status                                                    | **Real**           | Gateway                                                |




### 4.4 Agent tree node — `ProxyAgent`

The subagent graph on the canvas. **Only the proxy/gateway can see this.**


| Field                                                        | What it is                  | Today                             | Suggested source                                                                   |
| ------------------------------------------------------------ | --------------------------- | --------------------------------- | ---------------------------------------------------------------------------------- |
| `id` / `actor_id` / `name` / `role`                          | Node identity               | **Real** (proxy)                  | Gateway turns                                                                      |
| `parent_id` / `depth`                                        | Tree position               | **Derived** (from `Agent` spawns) | Gateway `Agent` tool-use lineage                                                   |
| `subagent_type` / `spawned_at`                               | Node metadata               | Derived                           | Gateway                                                                            |
| `stats` / `rollup` (calls/allow/deny/error/cost/tokens/apis) | Per-node and subtree totals | **Derived** (aggregated)          | Aggregated by the serving API from calls+turns sharing this node's session lineage |




### 4.5 Access denial — `AccessDenial`


| Field                                                           | What it is               | Today           | Suggested source                                                                                                                               |
| --------------------------------------------------------------- | ------------------------ | --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `request_id` / `status` / `summary` / `created_at` / `actor_id` | A blocked access request | **Real** (thin) | `access_request.denied` events — but they carry only `{status, request_id, actor}`, no api/session, so we can't place them on the timeline yet |




### 4.6 Charts + final output


| Field                                               | What it is                  | Today       | Suggested source                                     |
| --------------------------------------------------- | --------------------------- | ----------- | ---------------------------------------------------- |
| `charts.calls_over_time` (allow/deny/error buckets) | Stacked activity chart      | **Derived** | Aggregation over joined calls by `session_id` + time |
| `final_output.summary`                              | The run's closing synthesis | **Real**    | Last root-agent turn text (redacted)                 |


---



## 5. Suggestions for the design (you decide)

These line up with things your design docs already flag as TBD. Framed as
proposals — happy to be overruled.

1. **Add a** `session_id` **+** `call_id` **correlation model.** This is the keystone and
  it's the one thing that makes the whole screen possible. Your `routing.md`
   already gives the gateway a per-call `Jentic-Execution-Id`, but there's nothing
   that (a) groups a run or (b) links a proxy turn to the broker call it triggered.
   *Suggestion:* the client/agent integration mints one `session_id` per run and one
   `call_id` per intended tool call, and sends both as `Jentic-*` headers to **both**
   the gateway and the broker; both sides persist them. Whoever owns minting is your
   call — I just need the same id to appear on both halves. (And both sides should
   tolerate the ids being absent — degrade to a flat, ungrouped call list.)
2. **Persist the broker's governance verdict.** The broker already *computes*
  allow/deny (PBAC `rule_evaluator`) but only an allow leaves a record; a deny
   emits a `broker.pbac_denied` event and **no** execution row. The UI needs deny
   calls on the same timeline as allow calls. *Suggestion:* persist `verdict`
   (+ matched rule id/effect) on the execution record, and consider giving a
   preflight deny its own terminal record. Your call whether denies get a record or
   stay events-only — but if events-only, we'll need api + session on the event.
3. **Stamp** `method` **/** `path` **/** `credential_id` **on the execution record.** All three
  are known at the broker edge. Today we derive method/path from `operation_id` and
   guess the credential by api+timestamp. Persisting them removes the guesswork.
   (Also: set `execution_id` on `credential.accessed` events so that link is clean.)
4. **Clarify "gateway model allowlist" vs "broker PBAC".** Your `guardrails.md`
  marks this relationship TBD. In the UI they collapse into one `verdict`, but
   they're really **two different gates on two different call types** (may this
   caller use `gpt-4o`? vs. may this agent call `GET /flights`?). Worth stating that
   a Session interleaves both, so we render them consistently.
5. **Reconcile the content-logging non-goal with the UI showing chat.** Your
  `README.md` lists prompt/response content logging as out of scope, but the UI's
   Level-3 drawer shows chat turns + request/response snippets. *Suggestion:* scope
   in **redacted** content only (routed through `shared/redaction.py`), or tell us
   the UI should show turn *metadata* + redacted snippets only, never stored raw
   content. Either is fine for us — we just need to know the rule.
6. **The** `openapi-sketch.yaml` **referenced in your README is missing.** Whenever the
  gateway surface firms up, an OpenAPI sketch (even generated) would let us
   reconcile our `types.ts` against the real thing in one pass.

---

## 6. Your LLM gateway — how the frontend would surface it (later)

You asked me to read the gateway design and see how it lines up with how we've
been thinking. It lines up well — but I want to be explicit about **what we have
UI for today and what we deliberately don't**, so this isn't a silent gap.

### What's already surfaced (no new UI needed)

The gateway's per-call **output** — `model`, token `usage`, `estimated_cost_usd`,
latency — is exactly the "thinking" half our Sessions screen already renders. It
flows straight into:
- `ChatTurn` (model / usage / latency / assistant text), and
- the cost/token fields on `ProxyCall` + the session `tiles` + the activity chart.

So the moment the gateway produces real usage records (joined by `call_id`, per
§5.1), the most valuable part of the gateway is **already visualised** — we don't
need to build anything new for it.

### What would need a *new* screen (and doesn't exist yet)

The rest of the gateway is **admin/configuration**, which is a different surface
from our Sessions *observability* screen:
- a **model allowlist editor** (agent → user → global-default scopes, per
  `guardrails.md`),
- a **spend / budget dashboard** (spend by agent/user/model, budget windows, per
  `metering.md`), and
- a **provider topology / health** view (builtin vs LiteLLM vs custom, `/llm/health`,
  per `configuration.md` + `routing.md`).

### Our position

**We're intentionally *not* building the gateway admin UI right now**, because:
1. the design is still TBD (and `openapi-sketch.yaml` doesn't exist yet), so any UI
   would be guessing at a contract that will change;
2. it would be *another* mock UI. The Sessions screen we already have is itself a
   mock UI — I built it to explore how we could map things coming out of the LLM
   proxy — so adding the gateway admin screens now just means a second mock surface
   to build and maintain on top of an unfinalised design;
3. it's config/admin, not observability — low overlap with what we built, so
   deferring it costs us nothing on the Sessions work.

---

Once we've agreed on the §5 suggestions, I'll reconcile `ui/src/modules/llm-proxy/api/types.ts`
to the finalised contract and we can lock it in. Nothing in the UI is hard to
change — it was built to this exact bundle shape on purpose, so the closer the real
API is to §4, the less churn on our side.