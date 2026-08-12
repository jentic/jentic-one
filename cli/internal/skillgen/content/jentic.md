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
`jentic register` (or `jentic bootstrap`) out-of-band, which connects this
machine to a Jentic install, registers this agent, waits for a human to approve
it, and (for bootstrap) writes this skill. First, check your setup:

```
jentic doctor
```

If the Identity section passes (a registered identity and a usable token or API
key), skip to step 2. If it does not (no context, "not registered", or
"pending"), **stop and ask your operator** to run
`jentic register --url <install URL>` and approve the agent — that step blocks
on a human and cannot be completed by an autonomous agent. Once approved, a
token is minted and reused automatically; you never handle raw API
credentials — the CLI attaches the bearer token for you.

### 2. Check what you can do, and request access if needed

See your own identity, status, scopes, and which toolkits you're bound to:

```
jentic access whoami
```

Each toolkit binding lists the APIs it **serves** (`serves: [{vendor, name,
version}]`). This tells you exactly what you can already call. Combined with the
catalog (what's available to add — see step 3), it's your map of the workspace.

**Decide access from `whoami` first — do NOT execute an operation just to see
whether you have access.** A denied execute is a wasted round-trip; you can tell
in advance:

- If a binding already **serves** the API you need → you have access. Skip
  straight to `inspect`/`execute`; file no request.
- If **nothing** you're bound to serves it → you do **not** have access yet.
  Provision it **before** your first execute — do not "try execute and branch on
  the denial":

```
jentic access request --provision <vendor/name> \
  --auth <bearer|api_key|basic|oauth2|none> \
  --rules-json '[{"effect":"allow","methods":["GET"],"path":".*"}]' \
  --reason "why you need this — shown to the human who approves it" \
  --wait
```

`--wait` blocks until a human fulfils and approves the plan in the dashboard;
once approved, the toolkit binding is live immediately — just retry `execute`.
Always pass `--reason` on **every** access request (`--provision`, `--toolkit`,
or `--scope`): a human reviews it before approving and your reason is shown to
them — a clear one-liner ("fetch the user's open PRs to summarise them") is what
gets you approved faster.
You normally do **not** need `jentic access refresh` after a `--provision` plan:
bindings take effect live, and a plan grants no new token scope. Only refresh
after an approved `scope:grant` **and** only if `whoami` flags the scope as not
yet on your token (see the stale-scope note it prints).

**File once, richly — never thrash.** Work out the full access end-state up
front — from `whoami`, the catalog, and the task — and file it as **one
composite request**: every target flag repeats and combines, so a job needing
several APIs is one command the human decides in one sitting:

```
jentic access request \
  --provision slack.com/api --auth slack.com/api=bearer \
  --rules-json 'slack.com/api=[{"effect":"allow","methods":["POST"],"path":"/chat\\.postMessage"}]' \
  --provision googleapis.com/sheets --auth googleapis.com/sheets=oauth2 \
  --rules-json 'googleapis.com/sheets=[{"effect":"allow","methods":["GET"],"path":".*"}]' \
  --toolkit github.com/api \
  --reason "one reason covering the whole job" \
  --wait
```

Each `--provision` adds a full plan for that API — keep every plan complete
(auth, rules, reason), exactly as you would for a single one;
`--toolkit`/`--toolkit-id`/`--scope` add single items. With more than one
`--provision`, key `--auth` and `--rules-json` by the same
`vendor/name[/version]` you passed to `--provision` (include the version in
the key if you used one); the bare form applies when there is exactly one.
Never file duplicate or per-operation requests, and don't
withdraw-and-refile to tweak a proposal; once filed, tell your operator an
approval is waiting and hand back (or `--wait`). If a composite collides with
an older pending request for one of its targets, nothing is filed — drop that
target or `jentic access withdraw` the old request, then re-file. `--wait` can
end `partially_approved` (exit 4): check `jentic access status <id>` to see
which items were granted before proceeding.

If you'd rather be reactive, the broker also guides you: when `execute` is denied
it prints a recovery line on stderr (the `agent_directive`) and **exits 2**, so
you can branch on the exit code instead of mistaking the 4xx body for success.
The directive tells you exactly how to recover — which differs by denial:

- **`no_toolkit_binding` (403)** — nothing serves this API yet (no toolkit, and
  usually no credential). File a **provisioning plan** describing the whole path
  to first execution, and propose the auth type and permission rules you read
  from the API spec:

```
jentic access request --provision stripe.com/api \
  --auth bearer \
  --rules-json '[{"effect":"allow","methods":["GET"],"path":".*"}]' \
  --reason "why you need this — shown to the human who approves it" \
  --wait
```

  - `toolkit_serves_api: false` — **no** toolkit serves this API yet, so a bare
    `--toolkit` binding request would be denied ("No toolkit serves API …").
    Filing it now is a dead-end. File the `--provision` plan above instead: it
    describes the whole path (create toolkit, provision + bind a credential with
    your proposed rules, bind you), which a human fulfils and approves in the
    dashboard. The directive's `suggested_command` already points at `--provision`
    in this case. The plan does **not** force a new toolkit: during fulfilment
    the operator can add the credential to a toolkit they already have (the
    wizard offers both) — worth relaying when your operator mentions an
    existing toolkit they want to extend.
  - `toolkit_serves_api: true` — a toolkit already serves this API and you just
    aren't bound to it; the directive suggests `jentic access request --toolkit
    <vendor/name> --wait`. File that and wait for approval.

- **`credential_not_provisioned` (424)** — you're bound to a toolkit, but no
  credential (account) is connected. Filing an access request will **not** fix
  this; the directive carries a `provisioning_url` — hand it to your operator to
  connect the account, then retry.
- **`credential_undecryptable` (424)** — a credential *is* connected, but its
  stored secret can no longer be decrypted (typically the deployment's
  encryption key rotated underneath it, e.g. a reinstall over existing data).
  Neither an access request nor retrying will fix this — ask your operator to
  remove and re-add the credential, then retry.
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

### Proposing permission rules from the spec

`--provision` is your chance to propose the credential's auth type and its
permission rules as a **first pass** — a human reviews and edits them before
approving. Do the work up front:

1. Read the operation surface and security schemes:
   `jentic apis operations <vendor/name/version>` and
   `jentic inspect <operation_id>` show methods, paths, and the declared auth.
2. Pick `--auth` from what the spec declares (`bearer`, `api_key`, `basic`,
   `oauth2`), or `none` if the API needs no credential.
3. Translate the user's plain-English intent into rules. "Read everything, write
   only to the prod board" becomes concrete `allow`/`deny` rules with
   `methods`/`path`, e.g.
   `[{"effect":"allow","methods":["GET"],"path":".*"},
     {"effect":"allow","methods":["POST","PUT"],"path":"/boards/prod/.*"}]`.
   An `allow` rule must constrain at least one of `methods`/`path`/`operations`.
   **Be honest at the enforcement seam.** The broker matches a rule against the
   request's HTTP method, URL path, and OpenAPI operation id — **never the
   request body**. An intent that hinges on a body field ("only allow messages
   to the #general channel" when the channel is a POST-body field) **cannot be
   enforced** by these rules. Don't propose a rule that silently won't fire —
   say so, and offer the real choices: allow the operation broadly (the human
   accepts the wider grant), deny the operation entirely, or allow it and
   record the constraint as instructions you follow yourself (unenforced).
   Also sanity-check your proposal before filing: rules evaluate
   first-match-wins, so an early broad `allow` shadows every rule after it,
   and a rule set that contradicts the intent you stated in `--reason` will
   confuse the human reviewing it.
4. You never enter the credential secret and you never approve — the human fills
   the secret in the dashboard and grants the plan. You propose; they decide.

A plain `toolkit:bind` (`--toolkit`) is only the **last mile** — use it when a
toolkit for the API already exists (e.g. an operator created one) and you just
need to be bound to it. When nothing serves the API yet, `--provision` is the
right first move; a bare `--toolkit` would auto-deny.

`--toolkit`/`--provision` take a `vendor/name[/version]` reference (the broker
also suggests the exact command in its `agent_directive`). `--wait` blocks until
a human decides and sets the exit code: **0** = approved, **2** = denied —
read the item's `decision_reason` (in the JSON, or shown under the item on a
TTY) to learn *why* before giving up, **3** = still pending when `--timeout`
elapsed (poll later with `jentic access status <id>`), **4** = partially
approved. Without `--wait` you get a request id and an `approve_url` to hand to
your operator. Granting is always a human action — you file and wait, you never
approve yourself.

If you file a bare `toolkit:bind` (`--toolkit`) for an API that nothing serves
yet, approval comes back **denied** with `decision_reason: "No toolkit serves
API <vendor/name>; provision and bind a credential for it first"`. That is the
signal to file a `--provision` plan instead: it describes the missing toolkit,
credential, rules, and binding as one request the operator can fulfil and
approve in the dashboard.

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
`{"data": []}` until you import something. **Import before you search** — if the
user already named the API/vendor (e.g. "Google Sheets"), go straight to
`catalog search`/`import`; don't `search` an empty registry first and waste a
call. The discovery order is:

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
jentic access request --scope catalog:import --reason "import the Sheets API to read the user's spreadsheet" --wait
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

`jentic catalog outdated` lists registered APIs whose upstream spec changed since
import (also surfaced by `jenticctl status`). Re-importing promotes the new spec
to **live**, changing behavior, so this is an **operator** decision: **suggest**
the re-import (`jentic catalog import <vendor/name>`) to the operator and let them
run it — never silently re-import on your own.

**Before concluding "the data is gone", confirm which backend you're on.** If
APIs, credentials, or toolkits you *know* existed appear missing — or IDs look
unfamiliar — you may be talking to a **different** backend than you expect. A
hosted (`remote`) Jentic install and a `local` self-hosted one have independent
registries and credentials, and the CLI, an agent, or an MCP server can each be
bound to a different one. Check the backend your base URL serves before
diagnosing data loss:

```
jentic context view        # shows the active context's base_url
curl -s "<base-url>/instance"   # e.g. http://127.0.0.1:8000/instance on a default local install
```

The unauthenticated `/instance` response reports `backend` (`local` / `remote` —
the install's declared `server.backend`), `canonical_base_url`, `host`, and an
opaque `instance_id` (null when telemetry is off). If it's not the backend you
meant to use (e.g. an MCP server still on a remote backend while you imported
locally), repoint that client at the right base URL rather than
importing/searching again.

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

**Diagnose an `execute` failure by its symptom, not the exit code alone.** A
broker **denial** prints an `agent_directive` on stderr (exit **2**). An
error naming DNS, TLS, timeout, or connection refused is a **transport
failure** — usually exit **1**, but exit **2** (`resolve … failed`) when the
`operation_id` lookup hits an unreachable control plane — with two causes:

> Exit **2** broadly means "this request cannot succeed **as asked**" — a
> broker denial, a failed operation resolve, or missing local context (e.g. no
> active context configured). Don't blind-retry an exit 2: change the ask, fix
> the config, or request access. Exit **3** (still pending) and the transient
> transport failures are the retryable ones.

- **Wrong target (DNS or TLS error).** The broker target resolves as
  built-in default (`https://127.0.0.1:8100`) < `~/.jentic/config.yaml`
  (`broker.scheme` / `broker.host`, recorded by `jenticctl install`) <
  flags. `lookup broker.jentic.ai: no such host` means the config points at
  the hosted broker from a local install; a TLS error against a local
  target usually means `broker.scheme` should be `http`. Fix the config, or
  override per call:

```
jentic execute <operation_id> --broker-scheme http --broker-host 127.0.0.1:8100
```

- **Stopped instance (connection refused on a local target).** If the target
  is already local (`127.0.0.1` / `localhost`) and the connection is
  *refused*, the target is right and the instance is probably **not
  running** (rebooted machine, Docker not started). Health-check before
  concluding anything: `jenticctl status` reports whether the control-plane
  server and broker are reachable. If they're down, do not retry, guess, or
  quietly give up — tell the user plainly, e.g. *"Your Jentic One instance
  appears to be stopped, which is why I can't reach {API}. Restart it with
  `jenticctl start` (then `jenticctl status` to confirm), and I'll retry."*
  After the restart, retry the original call and continue the task.

## Quick Reference

- The authoritative command + flag reference is **generated from the CLI
  itself**, not this file: run `jentic --help` / `jentic <command> --help`
  (always current, works offline), or open the platform docs at `/app/docs` on
  the control plane (Reference → CLI) — the same reference rendered for humans,
  next to the HTTP API and Broker API references.
- `jentic context view` — the active context: identity, environment, base_url,
  and granted directories (start here).
- `jentic access whoami` — your identity, status, scopes, and toolkit bindings
  with the APIs each one **serves** (check this before executing or provisioning).
- `jentic access request` — ask a human for access. `--provision <vendor/name>`
  files the whole path to first execution as one plan when nothing serves the
  API yet; `--toolkit <vendor/name>` asks to be bound to an **existing**
  toolkit; `--scope <scope>` requests a missing scope. All target flags repeat
  and combine into **one composite request**. Always pass `--reason`; add
  `--wait` to block on approval (see Procedure for full examples).
- `jentic access list | status <id> | withdraw <id>` — track your requests.
- `jentic access refresh` — re-mint your token after an approved **scope**
  grant that `whoami` flags as not yet on your token. Bindings need no
  refresh — they are live on approval.
- `jentic catalog search "<query>"` / `jentic catalog import <vendor/name>` —
  find and import APIs (import first; `search` only sees imported operations).
- `jentic search "<query>"` → `jentic inspect <operation_id>` →
  `jentic execute <operation_id | METHOD:URL>` — discover, inspect, and call
  operations through the broker (use the full upstream URL; the broker is a
  forward proxy, not a path router).
- `jentic register` / `jentic bootstrap` — operator commands that create and
  approve this identity (they block on human approval; not for autonomous use).
- `jentic doctor` — read-only self-check of THIS agent's setup (config/state
  dirs, resolvable identity, a usable token, control-plane reachability, clock
  skew). Run it first when something is off but you're not sure what; it never
  mints tokens or writes anything. `--json` for a parseable report. (This is the
  agent-side sibling of `jenticctl doctor`, which needs operator tooling.)
- `jentic api <METHOD> <path>` — a `gh api`-style authenticated passthrough to
  the control plane for endpoints without a dedicated command. It self-describes:
  `jentic api ops` lists available operations and `jentic api describe <METHOD>
  <path>` prints one operation's parameters, so you can discover a new route and
  its inputs without leaving the CLI. Pass a JSON body with `-d '<json>'`, `-d @file`,
  or piped stdin.
- `jentic history export --trace <trace_id>` — export the execution history of
  one trace (JSON envelope with `schema_version`/`trace_id`), for auditing what
  you have run. `--trace` is required; take the id from an `execute --json`
  response or from `jentic events watch`.
- `jentic events watch` — stream live execution/approval events for this
  identity (long-running; Ctrl-C to stop).
- `--dry-run` / `--export-plan` — on a mutating command (`execute`,
  `apis import`), validate and print the request that WOULD be sent (a machine
  plan with `--export-plan`) **without** sending it. Use it to preview a call —
  including the exact broker URL and headers — before committing side effects.
- `jenticctl status` / `jenticctl start` — health-check and restart the local
  deployment; check this first when a local target refuses connections.
- Add `--json` to force machine-readable output on a terminal (works on
  `search`, `execute`, `inspect`, `apis`, `access`, `context view`, `doctor`).
- **Correlation & retries**: export `JENTIC_SESSION_ID=<your session id>` and
  every request carries it as `X-Jentic-Session-Id`, so operators can group all
  of your calls in server logs; each `execute` also sends a fresh W3C
  `traceparent`. When you must retry a mutating call (POST/PUT), pass
  `--idempotency-key <uuid>` to `execute` — the server can then de-duplicate,
  and the CLI treats the request as safe for its transport-level retries.

## Pitfalls

- Calling `execute` before the agent is registered and approved fails — there is
  no token. Check `jentic doctor`; if the Identity section warns, ask your
  operator to run `jentic register` (with `--url <install URL>` on a fresh
  machine) and approve you.
- `search` returning `{"data": []}` usually means **nothing is imported yet**,
  not that you lack access. Run `jentic catalog search` → `jentic catalog
  import`, then search again. Both reading the registry and importing a
  cataloged API need no grant — an approved agent already holds `apis:read` and
  `catalog:import` by default. (Importing arbitrary URL/inline specs via `POST
  /apis` is the only import path that needs `apis:write`.) Don't invent other
  "catalog read" scopes; they're rejected.
- **Verify which backend you're talking to before diagnosing "missing" APIs
  or credentials.** If your session also has Jentic **MCP tools**
  (`search_apis`, `list_credentials`, `execute`, …), they may be bound to a
  **different backend** than this CLI — typically the hosted cloud workspace
  vs the local install — and nothing in their responses says which one
  replied. The symptom is *silent wrong answers*, not errors: an API the user
  just imported "doesn't exist", credentials "disappeared", or operation ids
  from one surface don't resolve on the other. Before concluding anything is
  missing or broken, check where each surface points — `jentic context view`
  shows this CLI's `base_url`, and `curl -s <base-url>/instance` reports
  which backend serves it (see "confirm which backend you're on" in step 3);
  ask your operator which backend the MCP server was configured against —
  and stick to one surface for the whole task.
- An `execute` failure is not always an access problem. A DNS or TLS error
  means the **broker target** is misconfigured (see step 5); connection
  refused on a **local** target usually means the instance is **stopped** —
  run `jenticctl status`, and if it's down tell the user to
  `jenticctl start`, then retry rather than abandoning the task. Only a
  broker **denial** (an `agent_directive` on stderr, exit **2**) is an
  access/credential issue:
  - **403 `no_toolkit_binding`** → check the directive's
    `parameters.toolkit_serves_api`. If `true`, a toolkit already serves the API
    and you just aren't bound — run the `jentic access request --toolkit …` it
    suggests and wait for approval. If `false`, nothing serves the API yet and a
    bare `--toolkit` bind would be **denied** ("No toolkit serves API …"); the
    directive suggests `--provision` instead — file that plan (propose `--auth`
    and `--rules-json` from the spec, pass `--reason`) and your operator fulfils
    it in the dashboard, into a new toolkit **or one they already have** — an
    existing toolkit never has to be recreated.
  - **424 `credential_not_provisioned`** → the directive gives a
    `provisioning_url` for your operator to connect an account (an access
    request won't help).
  - **424 `credential_undecryptable`** → the connected credential's secret
    can't be decrypted anymore; retrying won't help — ask your operator to
    remove and re-add the credential.
  - **403 `credential_identity_mismatch`** → a bound credential exists but its
    identity doesn't cover this API (`parameters.expected` vs
    `parameters.found`). An access request won't help — ask your operator to fix
    or re-provision the credential so it targets `expected`, then retry.
  Follow the directive; don't keep re-sending the same `execute`.
- You file and wait for access; you can't approve your own requests.
- **Don't execute to test access.** `whoami` already tells you what your bindings
  **serve**; if the API you need isn't there, `--provision` it and wait — don't
  fire a `execute` you expect to be denied just to read the recovery directive.
  The directive is a fallback for surprises, not a discovery step.
- The `operation_id` from `search`/`apis operations` resolves directly; the id
  from `catalog show` is the spec `operationId` (`inspect` resolves it via a
  fallback). If one doesn't resolve, try the `METHOD URL` pair from the hit's
  `_links.inspect` — don't guess ids.

## Verification

- `jentic doctor` reports the Identity section healthy (registered, token OK).
- After `jentic catalog import <vendor/name>`, `jentic search "<something in
  that API>"` returns at least one result.
- A known-allowed `jentic execute …` (pointed at the right broker) returns a 2xx
  response body.
