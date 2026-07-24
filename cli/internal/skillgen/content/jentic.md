---
name: jentic
description: Use this skill whenever the user wants to work with a third-party or external API/tool through the Jentic platform — e.g. asks to "find the vessel-tracking API and add it", "get rows from this Google Sheet", connect Slack, import/search/discover an API, integrate or automate a SaaS, pull data from a service, or call an external endpoint. Prefer launching this before ToolSearch or hand-rolled HTTP: it drives the audited Jentic CLI loop (identity → discover → request access → execute), even inside a code repo. Do NOT use it for local-only work (editing code, finding files, adding a package/dependency, or questions with no external API call).
version: 1
---

# Using Jentic from the CLI

Jentic is an API broker: you discover operations across many APIs, then execute
them through a single authenticated gateway without managing each API's
credentials yourself. The `jentic` CLI is the agent-facing entrypoint.

## When to Use

- You need to call a third-party API (Stripe, GitHub, Slack, …) but don't have
  its SDK or credentials wired up.
- The user asks to **find, add, import, connect, or search for an API/tool**
  ("find the vessel-tracking API and add it", "get rows from this Google
  Sheet") — treat these as Jentic tasks, not local-repo or generic tool-search
  tasks, even when you are running inside a code repository.
- You want to discover what operations exist for a capability ("create a
  payment", "list pull requests") instead of reading raw OpenAPI specs.
- You are an agent that should drive real API calls through one audited broker.

Reach for this skill's `jentic` CLI **before** generic tool discovery
(`ToolSearch`) or hand-rolling HTTP calls: the broker is the single audited path
to external APIs and handles credentials for you.

## Prerequisites

- The `jentic` CLI is installed and on PATH.
- A reachable Jentic control plane (the base URL; defaults to the local
  install). Override with `--base-url` or `config.yaml`.

## Procedure

### 1. Confirm you have a valid identity

You normally don't set up your own identity — your human operator runs
`jentic bootstrap` (or `jentic register`) out-of-band, which registers this
agent, waits for a human to approve it, and writes this skill. First, check that
you already have a usable token:

```
jentic profile list
```

If the active profile shows a valid token, skip to step 2. If it does not (no
token, or "not registered"), **stop and ask your operator** to run
`jentic bootstrap` and approve the agent — that step blocks on a human and
cannot be completed by an autonomous agent. Once approved, tokens are saved to
the active profile and reused automatically; you never handle raw API
credentials — the CLI attaches the bearer token for you.

### 2. Check what you can do, and request access if needed

See your own identity, status, scopes, and which toolkits you're bound to:

```
jentic access whoami
```

The broker enforces per-operation access. When `execute` is denied it prints a
recovery line on stderr (the `agent_directive`) and **exits 2**, so you can
branch on the exit code instead of mistaking the 4xx body for success. The
directive tells you exactly how to recover — which differs by denial:

- **`no_toolkit_binding` (403)** — you're not bound to a toolkit for this API.
  The directive's `parameters.toolkit_serves_api` tells you which recovery
  applies:
  - `toolkit_serves_api: true` — a toolkit already serves this API; you just
    aren't bound to it. File a binding request and wait for approval:

```
jentic access request --toolkit stripe.com/api --wait
```

  - `toolkit_serves_api: false` — **no** toolkit serves this API yet, so a
    toolkit-binding request would be denied ("No toolkit serves API …"). Filing
    it now is a dead-end. First a credential must be connected (provisioned) and
    bound to a toolkit — that is what makes a toolkit serve the API. Ask your
    operator to connect (bind) a credential for this API, then file the
    `jentic access request --toolkit …` binding and retry.

- **`credential_not_provisioned` (424)** — you're bound to a toolkit, but no
  credential (account) is connected. Filing an access request will **not** fix
  this; the directive carries a `provisioning_url` — hand it to your operator to
  connect the account, then retry.
- **`credential_identity_mismatch` (403)** — a toolkit *is* bound and a
  credential *is* connected, but that credential's stored identity doesn't cover
  this API (e.g. it targets a different name/version, or was stored in a
  non-canonical form). Filing an access request will **not** fix this — the
  binding already exists. The directive's `parameters.expected` vs
  `parameters.found` name the mismatch; if `parameters.would_match_if_normalized`
  is `true` the credential just needs re-provisioning to canonicalize its
  identity. Either way, ask your operator to fix or re-provision the credential
  so it targets `expected`, then retry.
- **`ambiguous_toolkit` (409)** — multiple toolkits you're bound to serve this
  API. The directive lists `candidates`; resend the same `execute` with
  `--header Jentic-Toolkit-Id=<toolkit_id>` (the directive also gives a
  copy-pasteable `suggested_command`).

Always follow the `agent_directive`'s `suggested_command` / `provisioning_url`
rather than assuming which recovery applies. You can also request access
proactively before you're denied.

`--toolkit` takes a `vendor/name[/version]` reference (the broker also
suggests the exact command in its `agent_directive`). `--wait` blocks until a
human decides and sets the exit code: **0** = approved, **2** = denied —
read the item's `decision_reason` (in the JSON, or shown under the item on a
TTY) to learn *why* before giving up, **3** = still pending when `--timeout`
elapsed (poll later with `jentic access status <id>`), **4** = partially
approved. Without `--wait` you get a request id and an `approve_url` to hand to
your operator. Granting is always a human action — you file and wait, you never
approve yourself.

A `toolkit:bind` is the **last mile** (agent → an existing toolkit), not the
first. It only succeeds if a toolkit **already exists** for that API with a
credential bound to it. If none does yet, approval comes back **denied** with
`decision_reason: "No toolkit serves API <vendor/name>; provision and bind a
credential for it first"` — the request is resolved (no longer pending), but
nothing was granted. You **cannot** fix this yourself: tell your operator to
create a toolkit and connect (bind) a credential for that API in the dashboard,
then re-file the request once they confirm. The same denial applies if you
named a `--toolkit-id` / credential that isn't visible to your approver.

Track and manage your requests:

```
jentic access list
jentic access status <request_id>
jentic access withdraw <request_id>
```

`--wait`'s `--timeout` is a duration **with a unit** — `--timeout 120s`, `2m`,
`90s`. A bare number (`--timeout 120`) is rejected. Once a request is approved,
retry the `execute` that was denied.

### 3. Find an operation (import first, then search)

`search` only sees operations that have been **imported into this deployment's
local registry**. On a fresh install the registry is empty, so `search` returns
`{"data": []}` until you import something. The discovery order is:

1. Browse the public catalog for an importable API:

```
jentic catalog search "spreadsheets"
```

2. Import the one you want into the local registry (auto-promotes to live):

```
jentic catalog import googleapis.com/sheets
```

   Importing an **already-cataloged** API is gated on `catalog:import`, which an
   approved agent holds **by default** — no access request needed. Just run the
   import. (This is narrower than importing arbitrary URL/inline specs via
   `POST /apis`, which still needs `apis:write`.) If `import` unexpectedly fails
   with `403 … requires one of: catalog:import` — e.g. you were approved before
   `catalog:import` became a default scope and weren't re-granted — request it,
   wait for a human to approve, refresh your token, then retry:

```
jentic access request --scope catalog:import --wait
jentic access refresh
jentic catalog import googleapis.com/sheets
```

3. Now search the local registry, or list an API's operations directly:

```
jentic search "get values from a spreadsheet range" --limit 10
jentic apis operations googleapis-com/googleapis-com-sheets/v4
```

`search` returns JSON when piped. Each hit carries both a registry
`operation_id` and a `_links.inspect` (a `/inspect?id=METHOD%20URL` link). Pass
the `operation_id` straight to `inspect`/`execute` — it resolves by registry key
— or use the `METHOD URL` pair the link decodes to. (The id shown by `jentic
catalog show` is the spec's `operationId`; `inspect` accepts that too, via a
fallback, but the `operation_id` from `search`/`apis operations` is the most
direct.)

If `search` returns no results, it prints a hint to run `jentic catalog search`
/ `jentic catalog import` first — that almost always means nothing relevant is
imported yet. Both **reading** the registry and **importing a cataloged API**
need no request — an approved agent already holds `apis:read` and
`catalog:import` by default, so just import and search again. Don't file an
access request for a made-up "catalog read" scope.

### 4. Inspect the operation's contract

Resolve an operation to its method, path, parameters, and schemas before
calling it. Pass the inspect identifier from `search`/`apis operations`, or a
`METHOD URL` pair:

```
jentic inspect "$(jentic search 'get spreadsheet values' --json | jq -r '.data[0].operation_id')"
jentic inspect 'GET https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/{range}'
```

On a 404, `inspect` prints the reason and a hint on stderr and exits 2 (it is
not silent). If you passed the id from `catalog show` and it didn't resolve, use
the `operation_id` from `search`/`apis operations`, or the `METHOD URL` pair
that the hit's `_links.inspect` decodes to.

### 5. Execute through the broker

Send the request through the Jentic broker. The broker is a transparent forward
proxy, so the target is the **full upstream URL** (scheme + host + path), not a
host-relative path. Reference an `operation_id`/inspect id from `search`/
`inspect` — the CLI fills in the upstream URL for you — or pass `METHOD:URL`
directly.

```
jentic execute <operation_id> --query limit=10
jentic execute GET:https://sheets.googleapis.com/v4/spreadsheets/{id}/values/{range} --path id=ABC --path range=A1:Z10
```

**Local installs target a local broker.** The built-in default broker host is
`broker.jentic.ai`, which is unreachable on a local deployment — a DNS error
like `lookup broker.jentic.ai: no such host` means the broker target is wrong,
**not** an access problem. Point `execute` at the local broker via
`~/.jentic/config.yaml` (`broker.scheme` / `broker.host`) or per-call flags:

```
jentic execute <operation_id> --broker-scheme http --broker-host 127.0.0.1:8100
```

## Quick Reference

- `jentic profile list` — see profiles and which is active (start here).
- `jentic access whoami` — your identity, status, scopes, toolkit bindings.
- `jentic access request --toolkit <vendor/name> [--wait --timeout 120s]` —
  ask a human to grant access; prints an `approve_url`. (Scopes for an approved
  agent are granted automatically; you do not request catalog/registry scopes.)
- `jentic access list | status <id> | withdraw <id>` — track your requests.
- `jentic access refresh` — re-mint your token so a newly granted scope takes
  effect. After an approved `scope:grant`, `whoami` shows it under granted
  scopes but your current token can't use it until you refresh.
- `jentic catalog search "<query>"` — find importable APIs in the public catalog.
- `jentic catalog import <vendor/name>` — import an API into the local registry
  (required before `search` can find its operations). Gated on `catalog:import`,
  a default agent scope — no access request needed.
- `jentic search "<query>"` — discover imported operations (JSON when piped);
  pass a hit's `operation_id` to `inspect`/`execute`.
- `jentic apis operations <vendor/name/version>` — list an imported API's
  operations and their ids.
- `jentic inspect <operation_id | "METHOD URL">` — see method, path, params, schemas.
- `jentic execute <operation_id | METHOD:URL>` — call it through the broker. Use
  the full upstream URL (e.g. `POST:https://api.example.com/v1/users`); the
  broker is a forward proxy, not a path router. On a local install, target the
  local broker (`--broker-scheme http --broker-host 127.0.0.1:8100` or
  `~/.jentic/config.yaml`).
- `jentic register` / `jentic bootstrap` — operator commands that create and
  approve this identity (they block on human approval; not for autonomous use).
- Add `--json` to force machine-readable output on a terminal.

## Pitfalls

- Calling `execute` before the agent is registered and approved fails — there is
  no token. Check `jentic profile list`; if there's no valid token, ask your
  operator to run `jentic bootstrap` / `jentic register` and approve you.
- `search` returning `{"data": []}` usually means **nothing is imported yet**,
  not that you lack access. Run `jentic catalog search` → `jentic catalog
  import`, then search again. Both reading the registry and importing a
  cataloged API need no grant — an approved agent already holds `apis:read` and
  `catalog:import` by default. (Importing arbitrary URL/inline specs via `POST
  /apis` is the only import path that needs `apis:write`.) Don't invent other
  "catalog read" scopes; they're rejected.
- An `execute` failure is not always an access problem. A DNS/transport error
  (`lookup broker.jentic.ai: no such host`, connection refused — exit **1**)
  means the **broker target** is wrong; on a local install point at the local
  broker. Only a broker **denial** (exit **2**, with an `agent_directive` on
  stderr) is an access/credential issue:
  - **403 `no_toolkit_binding`** → check the directive's
    `parameters.toolkit_serves_api`. If `true`, run the `jentic access request …`
    it suggests and wait for approval. If `false`, no toolkit serves the API yet
    and a binding request would be denied ("No toolkit serves API …") — ask your
    operator to connect (bind) a credential for that API first, then file the
    binding request and retry (you can't self-serve the credential step).
  - **424 `credential_not_provisioned`** → the directive gives a
    `provisioning_url` for your operator to connect an account (an access
    request won't help).
  - **403 `credential_identity_mismatch`** → a bound credential exists but its
    identity doesn't cover this API (`parameters.expected` vs
    `parameters.found`). An access request won't help — ask your operator to fix
    or re-provision the credential so it targets `expected`, then retry.
  Follow the directive; don't keep re-sending the same `execute`.
- You file and wait for access; you can't approve your own requests.
- The `operation_id` from `search`/`apis operations` resolves directly; the id
  from `catalog show` is the spec `operationId` (`inspect` resolves it via a
  fallback). If one doesn't resolve, try the `METHOD URL` pair from the hit's
  `_links.inspect` — don't guess ids.

## Verification

- `jentic profile list` shows your profile with a valid token.
- After `jentic catalog import <vendor/name>`, `jentic search "<something in
  that API>"` returns at least one result.
- A known-allowed `jentic execute …` (pointed at the right broker) returns a 2xx
  response body.
