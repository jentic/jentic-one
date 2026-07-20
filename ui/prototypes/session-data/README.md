# Session reference dataset — "flight-ops" run (2026-07-15)

This folder is a **frozen, real dataset** from one deep Claude Code run, captured so we can
design the LLM Proxy / Sessions UI against real data instead of guesses. Nothing here is
mocked — it's exactly what the two trusted sources (LiteLLM proxy + Jentic broker) recorded.

## The run

Prompt: orchestrate a "flight operations tracker" — main agent spawns **3 subagents in
parallel**, each spawning **2 sub-subagents** (3-level tree), making real AirLabs (GET) +
Google Sheets (GET/POST/PUT + a destructive delete) calls through the Jentic broker, merging
up into one report. The agent even self-serviced the toolkit binding (it holds `toolkits:write`),
so the run shows the full governance arc: **denied → create toolkit → bind → all allowed**.

## Files

| File                              | Source                                | What it is                                                                                                                                                                                     |
| --------------------------------- | ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `flight-ops-report.md`            | the agent                             | The agent's own final report: call log, tree, spreadsheet URL. Human narrative of the run.                                                                                                     |
| `this_run_proxy.jsonl`            | LiteLLM proxy capture                 | 38 model calls. Full transcript turns, system prompt, response text, `response_tool_uses`, tokens, latency. **This is the "thinking" half.**                                                   |
| `this_run_execution_records.json` | broker → admin DB `execution_records` | 35 rows. The **real API calls** with status, http_status, duration, operation_id, api tuple, actor. **This is the "action" half.**                                                             |
| `this_run_events.json`            | admin DB `events`                     | 87 events incl. 35 `credential.accessed` (carry `credential_id`), 35 `execution.completed`, plus the setup arc (`toolkit.created`, `credential.bound_to_toolkit`, `access_request.denied`, …). |
| `.capture_boundary_lines`         | —                                     | Line offset in `~/litellm/capture_full.jsonl` where this run began (so the proxy slice is reproducible).                                                                                       |

## What this run PROVES about our data (the important part)

### The two halves are real and separate

- **Proxy `tool_uses` are framework tools, NOT API operations.** The names captured are
  `Agent` (subagent spawn), `Bash` (the shell that runs `jentic execute …`), `Skill`, `Write`,
  `TaskUpdate`. The actual `GET /flights`, `POST /spreadsheets` etc. are **invisible to the proxy** —
  they're inside the Bash command string. They only appear as structured rows in the broker's
  `execution_records`. → The UI MUST merge both sources; neither alone tells the story.
- **The subagent tree lives in the proxy**, via `Agent` tool_uses (records 2 and 19 spawn the
  subagents) + distinct system prompts / message-thread lengths per agent. The broker cannot see it.

### The gaps from our plan canvas, now confirmed with real rows

- **G1 — no session/correlation id.** Every one of the 35 `execution_records` has
  `trace_id = "unknown"`. There is no key to (a) group the 35 calls into one run, (b) attribute a
  call to a specific subagent, or (c) link a call to the proxy turn that caused it.
- **G2 — governance columns missing.** No `http_method`, `request_path`, `matched_rule_id`,
  `matched_effect`, `credential_id`, `tokens`, `cost`, or bodies on `execution_records`.
  `api_host` is NULL. (Method IS derivable from `operation_id` via the registry.)
- **G3 — denies uncorrelated.** The 4 preflight 403 denies produced **no execution_records** —
  only thin `access_request.denied` events. Not placeable on a timeline yet.
- **Bonus — credential_id exists, but disconnected.** `credential.accessed` events DO carry
  `credential_id` + api tuple, but their `trace_id`/`execution_id` are `None`, so they can only be
  joined to an execution by (api + nearest timestamp) heuristics — not a clean key.

## How to use this for design

- Level 1 tiles / table + Level 3 tool-call detail → drive from `this_run_execution_records.json`
  (+ derive method/path from operation_id, + join `credential.accessed` for credential).
- Chat / tokens / cost / subagent tree (Level 2 playground) → drive from `this_run_proxy.jsonl`.
- For the first UI pass we build to the **target** shape with MSW mocks modeled on THIS data, then
  swap real endpoints in as the backend phases (correlation id, columns, proxy sink) land.

## Reproduce the proxy slice

```
tail -n +$(( $(cat .capture_boundary_lines) + 1 )) ~/litellm/capture_full.jsonl
```
