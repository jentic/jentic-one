# Pitfalls, quick reference, and verification — read this when something is off, or you want the cheatsheet

This file carries the lane-labelled pitfall lists, the shared denial-code
taxonomy (what each broker denial means, in either lane), the command/tool
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
- A 424 `credential_not_provisioned` error means no account is connected
  yet. If the denial's directive carries a `suggested_command` (`jentic
  connect <key>`), the vendor is in the connect registry: call
  `request_connection` with that key and relay the returned `approval_url`
  to your operator. Otherwise relay the directive's `provisioning_url`
  (when present) — or report the gap — so they can connect the account.
  Either way a human approves; no tool you can call completes the
  connection.
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

## Denial codes — what each one means (both lanes)

A denied execute carries the same coded taxonomy in both lanes — the CLI
delivers it as a stderr `agent_directive` plus exit code 2, an MCP session
as a coded error envelope. The meanings below are surface-independent; the
CLI delivery mechanics (flags, exit codes) live in
`references/cli.md` step 2, the MCP envelope caveats in `references/mcp.md`
step 5. **Approval is always your operator's** — but for the
credential-*provisioning* denials below you can now start the fix yourself
when the vendor is in the connect registry: run `jentic connect <vendor>`
(CLI) or call `request_connection` (MCP), relay the returned `approval_url`
to your operator, confirm with `whoami` once they approve, then retry.
Everything else (binding an existing credential, scope grants, rule
changes) is performed by your operator in the Jentic One dashboard — relay
the right ask, then retry once they confirm.

- **`no_credential_binding` (403)** — no credential binding of yours covers
  this API. The ask forks on the directive/envelope's `api_served`
  field:
  - `false` — **no** credential is provisioned for this API at all. If the
    directive carries a `suggested_command` (`jentic connect <vendor>`),
    start the connect session yourself and relay its `approval_url`;
    otherwise ask your operator to connect or provision a credential for
    the API and bind you to it — include the auth type and permission
    rules you read from the spec so they can set it up in one pass.
  - `true` — a credential already serves this API and you just aren't bound
    to it; ask your operator to bind you to the existing credential
    (binding is always theirs — a fresh `jentic connect` also works for a
    registry vendor, but prefer the existing credential).
- **`credential_not_provisioned` (424)** — you're bound, but no
  credential (account) is connected. If the directive carries a
  `suggested_command` (`jentic connect <key>`), the vendor is in the
  connect registry — start the reconnect yourself (run that command, or
  call `request_connection` with the key it names) and relay the
  `approval_url`; otherwise hand the directive's `provisioning_url` to
  your operator to connect the account. Then retry.
- **`credential_undecryptable` (424)** — a credential *is* connected, but
  its stored secret can no longer be decrypted (typically the deployment's
  encryption key rotated underneath it, e.g. a reinstall over existing
  data). This is not agent-recoverable and retrying will not fix it — ask
  your operator to remove and re-add the credential, then retry.
- **`credential_identity_mismatch` (403)** — a binding exists and a
  credential *is* connected, but that credential's stored identity doesn't
  cover this API (e.g. it targets a different name/version, or was stored
  in a non-canonical form). The binding already exists, so no new binding
  helps. `parameters.expected` vs `parameters.found`
  name the mismatch; if `parameters.would_match_if_normalized` is `true`
  the credential just needs re-provisioning to canonicalize its identity.
  Either way, ask your operator to fix or re-provision the credential so it
  targets `expected`, then retry.
- **`ambiguous_credential_binding` (409)** — multiple credentials you're
  bound to cover this API. The recovery lists `candidates` and carries
  `parameters.headers`; resend the same execute disambiguated with a
  `Jentic-Credential-Id: <credential_id>` header — the authoritative
  tie-breaker (`Jentic-Credential-Name: <name>` also works when credential
  names are unique; the CLI adds either via `--header`).

## Quick Reference — CLI session

- The authoritative CLI command + flag reference is **generated from the
  CLI itself**, not this file: run `jentic --help` / `jentic <command>
  --help` (always current, works offline), or open the platform docs at
  `/app/docs` on the control plane (Reference → CLI) — the same reference
  rendered for humans, next to the HTTP API and Broker API references.
- `jentic context view` — the active context (environment + identity +
  base_url); start here in a CLI session.
- `jentic whoami` — your identity, status, scopes, and credential
  bindings with the APIs each one **serves** (check this before executing;
  it renders the same `GET /me` view `jentic api GET /me` returns). When
  access is missing, start a registry vendor's connect yourself
  (`jentic connect <vendor>`) or report the gap to your operator —
  approval, binding, and scope grants happen in the dashboard.
- `jentic connect <vendor>` — start a connect session for a registry
  vendor (e.g. `jentic connect github`): prints the `approval_url` a human
  approves in the browser (`--scopes`, `--reason` shape the ask; `--wait`
  polls until it connects or ends — rejected, expired, or cancelled). You never open or approve the
  URL yourself.
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
- Add `--json` to force machine-readable output on a terminal. It exists on
  **leaf** commands (`search`, `execute`, `inspect`, `apis list`,
  `doctor`); the bare group commands reject it. Non-TTY
  output is not automatically JSON: `register` persists `mode: human`,
  which wins over TTY detection — set `JENTIC_MODE=agent` (or pass
  `--json`) when you need parseable output. (`context view` has no
  `--json` flag at all — it follows the same mode rules.)
- Correlation & retries: export `JENTIC_SESSION_ID=<your session id>` so
  every request carries `X-Jentic-Session-Id`; pass `--idempotency-key
  <uuid>` when retrying a mutating `execute`.

## Quick Reference — MCP session

The mount serves exactly nine tools; the stdio server adds `get_started`.
Each maps onto the loop — the one-line whens and the structural facts (the
`instance` stamp; the CLI verbs that do **not** exist on the mount) are in
`references/mcp.md`, which is the authoritative lane reference. In short:
`whoami` (identity + bindings; decide access from it) → when a registry
vendor's credential is missing, `request_connection` (relay the
`approval_url` to your operator; other gaps you report) → `search_catalog`
→ `import_api`
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
