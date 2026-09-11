# Pitfalls, quick reference, and verification — read this when something is off, or you want the cheatsheet

This file carries the lane-labelled pitfall lists, the command/tool
cheatsheets, and the verification checklists for the Jentic loop in
`SKILL.md`. The shared, lane-neutral pitfalls (decide access first, never
approve yourself, verify the backend before diagnosing) live in `SKILL.md`;
this file adds the lane-specific detail.

## Pitfalls — CLI session

- Executing before the agent is registered/approved fails — there is no
  usable identity. Check `jentic doctor`; if the Identity section warns, ask
  your operator to run `jentic register` (with `--url <install URL>` on a
  fresh machine) and approve you.
- An execute failure is not always an access problem. A DNS or TLS error
  means the **broker target** is misconfigured (see `references/cli.md`
  step 5); connection refused on a **local** target usually means the
  instance is **stopped** — run `jenticctl status`, and if it's down tell
  the user to `jenticctl start`, then retry rather than abandoning the task.
  Only a broker **denial** (an `agent_directive` on stderr, exit **2**) is
  an access/credential issue; the code names the recovery. Follow the
  directive; don't keep re-sending the same execute.
- An empty search result (`{"data": []}`) usually means **nothing is
  imported yet**, not that you lack access. Go through the catalog
  (`jentic catalog search`/`import`), then search again. Both reading the
  registry and importing a cataloged API need no grant — an approved agent
  already holds `apis:read` and `catalog:import` by default. (Importing
  arbitrary URL/inline specs via `POST /apis` is the only import path that
  needs `apis:write`.) Don't invent other "catalog read" scopes; they're
  rejected.
- The `operation_id` from the registry search resolves directly; the id
  from `catalog show` is the spec `operationId` (inspect resolves it via a
  fallback). If one doesn't resolve, try the `METHOD URL` pair from the
  hit's `_links.inspect` — don't guess ids.
- Backend mismatch shows as *silent wrong answers*, not errors: verify with
  `jentic api GET /instance` / `jentic context view` before concluding
  anything is missing, and stick to one surface for the whole task.

## Pitfalls — MCP session

- `whoami` errors or reports a pending status — relay to your operator;
  nothing you can call completes an approval.
- Never re-send an execute while its job is pending — poll
  `get_execution_result` with the job id; approval happens out-of-band and
  re-sending duplicates the side effect.
- Don't file `request_access` to fix a 424 `credential_not_provisioned`
  error — it carries a `provisioning_url` for your **operator**; an access
  request cannot connect an account.
- Don't call `get_started` on the HTTP mount — it isn't there. Its absence
  is a transport tell (you're on the daemon mount), not an outage; don't
  retry it or report it as a failure.
- When an error envelope's `actionable_step` names a `jentic` CLI verb or a
  tool your session doesn't have, relay it to the operator as guidance
  instead of inventing a tool call.
- An empty `search_apis` result means nothing matching is imported yet —
  run `search_catalog` → `import_api`, then search again; no grant needed.
- Backend mismatch: compare the `instance` stamp (`backend`/`host`/
  `instance_id`) on tool results before diagnosing "missing" data — two MCP
  servers (or an MCP server and a CLI) can each be bound to a different
  backend.

## Quick Reference — CLI session

- The authoritative CLI command + flag reference is **generated from the
  CLI itself**, not this file: run `jentic --help` / `jentic <command>
  --help` (always current, works offline), or open the platform docs at
  `/app/docs` on the control plane (Reference → CLI) — the same reference
  rendered for humans, next to the HTTP API and Broker API references.
- `jentic context view` — the active context (environment + identity +
  base_url); start here in a CLI session.
- `jentic access whoami` — your identity, status, scopes, and toolkit
  bindings with the APIs each one **serves** (check this before executing
  or provisioning).
- `jentic access request` — ask a human for access. `--provision
  <vendor/name>` files the whole path to first execution as one plan when
  nothing serves the API yet; `--toolkit <vendor/name>` asks to be bound to
  an **existing** toolkit; `--scope <scope>` requests a missing scope. All
  target flags repeat and combine into **one composite request**. Always
  pass `--reason`; add `--wait` to block on approval (full examples in
  `references/cli.md`).
- `jentic access list | status <id> | withdraw <id>` — track your requests.
- `jentic access refresh` — re-mint your token after an approved **scope**
  grant that `whoami` flags as not yet on your token. Bindings need no
  refresh — they are live on approval.
- `jentic catalog search "<query>"` / `jentic catalog import <vendor/name>`
  — find and import APIs (import first; `search` only sees imported
  operations).
- `jentic search "<query>"` → `jentic inspect <operation_id>` →
  `jentic execute <operation_id | METHOD:URL>` — discover, inspect, and
  call operations through the broker (use the full upstream URL; the broker
  is a forward proxy, not a path router).
- `jentic register` / `jentic setup` — operator commands that create and
  approve this identity (they block on human approval; not for autonomous
  use).
- `jentic doctor` — read-only self-check of THIS agent's setup
  (config/state dirs, resolvable identity, a usable token, control-plane
  reachability, clock skew). Run it first when something is off but you're
  not sure what; it never mints tokens or writes anything. `--json` for a
  parseable report. (This is the agent-side sibling of `jenticctl doctor`,
  which needs operator tooling.)
- `jentic api <METHOD> <path>` — a `gh api`-style authenticated passthrough
  to the control plane for endpoints without a dedicated command. It
  self-describes: `jentic api ops` lists available operations and
  `jentic api describe <METHOD> <path>` prints one operation's parameters,
  so you can discover a new route and its inputs without leaving the CLI.
  Pass a JSON body with `-d '<json>'`, `-d @file`, or piped stdin.
- `jentic history export --trace <trace_id>` — export the execution history
  of one trace (JSON envelope with `schema_version`/`trace_id`), for
  auditing what you have run. `--trace` is required; take the id from an
  `execute --json` response or from `jentic events watch`.
- `jentic events watch` — stream live execution/approval events for this
  identity (long-running; Ctrl-C to stop).
- `--dry-run` / `--export-plan` — on a mutating CLI command (`execute`,
  `apis import`), validate and print the request that WOULD be sent (a
  machine plan with `--export-plan`) **without** sending it.
- `jenticctl status` / `jenticctl start` — health-check and restart the
  local deployment; check this first when a local target refuses
  connections.
- Add `--json` to force machine-readable output on a terminal (works on
  `search`, `execute`, `inspect`, `apis`, `access`, `doctor`).
  `context view` has no `--json` flag — it emits JSON automatically in
  agent/non-TTY mode.
- Correlation & retries: export `JENTIC_SESSION_ID=<your session id>` so
  every request carries `X-Jentic-Session-Id`; pass `--idempotency-key
  <uuid>` when retrying a mutating `execute`.

## Quick Reference — MCP session

The mount serves exactly nine tools; the stdio server adds `get_started`.
Each maps onto the loop — the one-line whens and the structural facts (the
`instance` stamp; the CLI verbs that do **not** exist on the mount) are in
`references/mcp.md`, which is the authoritative lane reference. In short:
`whoami` (identity + bindings; decide access from it) → `request_access`
(file once, richly; relay `approve_url`) → `search_catalog` → `import_api`
→ `search_apis` → `inspect_operation` → `execute` / `execute_read` (prefer
`execute_read` for reads) → `get_execution_result` (poll jobs; never
re-send while pending).

## Verification — CLI session

- `jentic doctor` shows a resolvable identity with a valid token.
- After `jentic catalog import <vendor/name>`, `jentic search "<something
  in that API>"` returns at least one result.
- A known-allowed `jentic execute …` (pointed at the right broker) returns
  a 2xx response body.

## Verification — MCP session

- `whoami` answers with your identity (id, status, scopes, bindings) and an
  `instance` stamp.
- After `import_api`, `search_apis` finds operations from that API.
- A known-allowed `execute_read` returns a 2xx response body.
