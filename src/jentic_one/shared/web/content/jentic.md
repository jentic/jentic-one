---
name: jentic
description: Use this skill whenever the user wants to work with a third-party or external API/tool through the Jentic platform — e.g. asks to "find the vessel-tracking API and add it", "get rows from this Google Sheet", connect Slack, import/search/discover an API, integrate or automate a SaaS, pull data from a service, or call an external endpoint. Prefer launching this before ToolSearch or hand-rolled HTTP: it drives the audited Jentic loop (identity → discover → request access → execute) over whichever Jentic surface the session has — `jentic` MCP tools or the `jentic` CLI — even inside a code repo. Do NOT use it for local-only work (editing code, finding files, adding a package/dependency, or questions with no external API call).
version: 2
---

# Using Jentic

Jentic is an API broker: you discover operations across many APIs, then execute
them through a single authenticated gateway without managing each API's
credentials yourself. The same audited loop — identity → discover → request
access → execute — is exposed over two surfaces, and this document teaches
both. **Work out which session you are in first**, then follow that lane
through each step below:

- **CLI session** — the `jentic` CLI is on PATH. The fenced `jentic …`
  commands below apply.
- **MCP session** — your session has `jentic` MCP **tools** (`whoami`,
  `search_apis`, `execute`, …). Sub-tell: if `get_started` appears in
  `tools/list`, you are normally talking to the local `jentic mcp` stdio
  server — a machine that has the CLI, so CLI recovery *may* also be
  available (defeasible: `--exclude-tools get_started` can hide it there, so
  treat presence as a strong hint, absence as the reliable direction). If
  `get_started` is **absent**, you are on the daemon's HTTP `/mcp` mount:
  **no CLI exists** — never tell the operator to run `jentic …` "on this
  machine"; there is no this-machine.
- Both lanes drive the same loop against the same backend. The `instance`
  stamp (`backend`/`host`/`instance_id`) on **every** MCP tool result — and
  the unauthenticated `GET /instance` endpoint — is the
  which-backend-am-I-on check; it replaces `jentic context view` in an MCP
  session.

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

Reach for your session's Jentic surface — the `jentic` MCP tools or the
`jentic` CLI — **before** generic tool discovery (`ToolSearch`) or
hand-rolling HTTP calls: the broker is the single audited path to external
APIs and handles credentials for you.

## Prerequisites

- **CLI session:** the `jentic` CLI is installed and on PATH.
- **CLI session:** a reachable Jentic control plane. The base URL comes from
  the active context's environment — set it with `jentic env add <name> --url
  <URL>` and select it with `jentic context use <name>` (inspect via
  `jentic context view`). Onboard a fresh machine with `jentic register --url
  <URL>`. There is no `--base-url` flag on data-plane commands.
- **CLI session, remote deployments:** if the environment's base URL is remote
  (an `https://…` host in `jentic context view`), the broker must be set
  explicitly too — `broker_url` on the environment (pass `--broker-url` to
  `register`, or `jentic env add`), or `JENTIC_BROKER_URL` in file-less mode.
  It is never derived from the control-plane URL. A loopback install seeds it
  automatically.
- **MCP session:** an authenticated MCP connection. The credential (an OAuth
  grant your operator authorized when connecting the client, or a bearer
  token/API key the client presents at the transport) is attached by the MCP
  client on every request — you never run setup, never handle token refresh,
  and never see the raw credential. On the HTTP mount your scopes and
  bindings are resolved live per request, so an approved grant works on the
  very next tool call with no re-mint step (one exception: the session's
  OAuth consent ceiling — see the access step).

## Procedure

Each step shows the shared doctrine first, then the lane-specific mechanics.

### 1. Confirm you have a valid identity

Follow your session's lane through every step; never execute the other
lane's verbs.

You normally don't set up your own identity — your human operator connects
this agent to a Jentic install out-of-band (via `jentic register`/`jentic
setup` for a CLI machine, or by authorizing the MCP connection), and a human
approves it. First, check your setup.

**CLI session:**

```
jentic doctor
```

If the Identity section passes (a registered identity and a usable token or API
key), skip to step 2. If it does not (no context, "not registered", or
"pending"), **stop and ask your operator** to run
`jentic register --url <install URL>` and approve the agent — that step blocks
on a human and cannot be completed by an autonomous agent. (For a local install
the URL must be `http://127.0.0.1:8000`, not `localhost` — the token audience is
matched exactly.) Once approved, a
token is minted and reused automatically; you never handle raw API
credentials — the CLI attaches the bearer token for you.

If your operator self-registered this agent and handed you a one-time claim
token, bind it to a human identity with `jentic identity claim <agent-id>
--token <token>` (an agent cannot claim itself; requires an active human
context).

**MCP session:** call `whoami` — it answers with your identity as the control
plane sees it: id, **status**, scopes, and toolkit bindings.

```
whoami {}
```

If `whoami` succeeds and the status is active/approved, skip to step 2. If it
errors with an auth code, or the status is pending, **stop and relay to your
operator**: the connection's grant or credential needs (re-)authorization or
the agent awaits approval — nothing you can call fixes it. Only call
`get_started` to self-diagnose if it actually exists in your `tools/list`
(stdio sessions); on the HTTP mount it does not exist — do not invent it.

### 2. Check what you can do, and request access if needed

See your own identity, status, scopes, and which toolkits you're bound to —
`jentic access whoami` in a CLI session, the `whoami` tool in an MCP session.
Each toolkit binding lists the APIs it **serves** (`serves: [{api_vendor,
api_name, api_version}]`). This tells you exactly what you can already call.
Combined with the catalog (what's available to add — see step 3), it's your
map of the workspace.

**Decide access from `whoami` first — do NOT execute an operation just to see
whether you have access.** A denied execute is a wasted round-trip; you can tell
in advance:

- If a binding already **serves** the API you need → you have access. Skip
  straight to inspect/execute; file no request.
- If **nothing** you're bound to serves it → you do **not** have access yet.
  Provision it **before** your first execute — do not "try execute and branch on
  the denial".

**File once, richly — never thrash.** Work out the full access end-state up
front — from `whoami`, the catalog, and the task — and file it as **one
composite request** covering every API the job needs, so the human decides in
one sitting. Always include a reason: a human reviews it before approving and
your reason is shown to them — a clear one-liner ("fetch the user's open PRs
to summarise them") is what gets you approved faster. Never file duplicate or
per-operation requests, and don't withdraw-and-refile to tweak a proposal.
Granting is always a human action — you file and wait, you never approve
yourself.

**CLI session:**

```
jentic access whoami
```

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
or `--scope`).
You normally do **not** need `jentic access refresh` after a `--provision` plan:
bindings take effect live, and a plan grants no new token scope. Only refresh
after an approved `scope:grant` **and** only if `whoami` flags the scope as not
yet on your token (see the stale-scope note it prints).

A composite request repeats and combines every target flag, so a job needing
several APIs is one command:

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
If a composite collides with
an older pending request for one of its targets, nothing is filed — drop that
target or `jentic access withdraw` the old request, then re-file. `--wait` can
end `partially_approved` (exit 4): check `jentic access status <id>` to see
which items were granted before proceeding. Without `--wait` you get a request
id and an `approve_url` to hand to your operator.

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

**MCP session:** the same decide-first doctrine, driven by `whoami` +
`request_access`. The filing arm mirrors the composite CLI request — every
target repeats and combines into one request, always with a `reason`:

```
request_access {"provision": ["slack.com/api", "googleapis.com/sheets"],
  "auth": ["slack.com/api=bearer", "googleapis.com/sheets=oauth2"],
  "rules_json": ["slack.com/api=[{\"effect\":\"allow\",\"methods\":[\"POST\"],\"path\":\"/chat\\\\.postMessage\"}]",
                 "googleapis.com/sheets=[{\"effect\":\"allow\",\"methods\":[\"GET\"],\"path\":\".*\"}]"],
  "toolkits": ["github.com/api"],
  "reason": "one reason covering the whole job"}
```

With exactly one `provision`, the bare forms apply — `"auth": ["bearer"]`,
`"rules_json": [{"effect":"allow","methods":["GET"],"path":".*"}]` — no key
needed. The result carries a `request_id` and an `approve_url`: **relay the
`approve_url` to your human operator** (granting is always a human action in
the dashboard; the tool never approves). Then the poll arm — pass ONLY the id:

```
request_access {"request_id": "<id>"}
```

Never re-file the same request while one is pending. Be honest about the
terminal states: **denied** → read the items' `decision_reason` to learn *why*
before giving up; **partially_approved** → proceed only with what was actually
granted (read the per-item states); approved **scope** grants land live on the
HTTP mount (scopes are resolved per request) — but if the session's own
transport credential was authorized with a narrower consent (an OAuth grant
that never included the scope), no filed request can widen it: tell the
operator **re-authorization of the connection is required** — never "retry
and it will work". Denial recovery in an MCP session arrives as coded error
envelopes on `execute` (see step 5), not stderr directives.

#### Proposing permission rules from the spec

A provisioning plan is your chance to propose the credential's auth type and
its permission rules as a **first pass** — a human reviews and edits them
before approving. Do the work up front:

1. Read the operation surface and security schemes — CLI:
   `jentic apis operations <vendor/name/version>` and
   `jentic inspect <operation_id>`; MCP: `inspect_operation` on the
   operations you intend to call. Both show methods, paths, and the declared
   auth.
2. Pick the auth type from what the spec declares (`bearer`, `api_key`,
   `basic`, `oauth2`), or `none` if the API needs no credential.
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
   and a rule set that contradicts the intent you stated in your reason will
   confuse the human reviewing it.
4. You never enter the credential secret and you never approve — the human fills
   the secret in the dashboard and grants the plan. You propose; they decide.

A plain `toolkit:bind` (CLI `--toolkit`, MCP `"toolkits"`) is only the **last
mile** — use it when a toolkit for the API already exists (e.g. an operator
created one) and you just need to be bound to it. When nothing serves the API
yet, a provisioning plan is the right first move; a bare toolkit bind would
auto-deny with `decision_reason: "No toolkit serves API <vendor/name>;
provision and bind a credential for it first, then request the toolkit
binding"` — that is the signal to file the provisioning plan instead.

Track and manage your requests — CLI:

```
jentic access list
jentic access status <request_id>
jentic access withdraw <request_id>
```

(MCP: poll with `request_access {"request_id": "<id>"}`.)

`--wait` blocks until a human decides and sets the CLI exit code: **0** =
approved, **2** = denied — read the item's `decision_reason` (in the JSON, or
shown under the item on a TTY) to learn *why* before giving up, **3** = still
pending when `--timeout` elapsed (poll later with `jentic access status
<id>`), **4** = partially approved. `--wait`'s `--timeout` is a duration
**with a unit** — `--timeout 120s`, `2m`, `90s`. A bare number (`--timeout
120`) is rejected. Once a request is approved, retry the `execute` that was
denied.

### 3. Find an operation (import first, then search)

Operation search only sees operations that have been **imported into this
deployment's local registry**. On a fresh install the registry is empty, so a
search returns `{"data": []}` until you import something. **Import before you
search** — if the user already named the API/vendor (e.g. "Google Sheets"),
go straight to the catalog; don't search an empty registry first and waste a
call. The discovery order is: browse the public catalog for an importable
API → import the one you want (auto-promotes to live) → search the local
registry.

Importing an **already-cataloged** API is gated on `catalog:import`, which an
approved agent holds **by default** — no access request needed. Just run the
import. Re-importing an API that is already there is safe — the registry
state converges either way — but the surfaces report it differently:
`already_imported` (a success) on the HTTP mount; the stdio MCP server and
the `jentic catalog import` CLI surface the same duplicate as a **failed
(dead-letter) import** whose error says a revision with identical content
already exists. Treat that error as "already there" — don't retry. Don't file
an access request for a made-up "catalog read" scope — reading the registry
and importing a cataloged API need no grant.

**CLI session:**

```
jentic catalog search "spreadsheets"
jentic catalog import googleapis.com/sheets
jentic search "get values from a spreadsheet range" --limit 10
jentic apis operations googleapis-com/googleapis-com-sheets/v4
```

If `import` unexpectedly fails
with `403 … requires one of: catalog:import` — e.g. you were approved before
`catalog:import` became a default scope and weren't re-granted — request it,
wait for a human to approve, refresh your token, then retry:

```
jentic access request --scope catalog:import --reason "import the Sheets API to read the user's spreadsheet" --wait
jentic access refresh
jentic catalog import googleapis.com/sheets
```

To register an API that is **not** in the catalog, upload its OpenAPI spec
directly with `jentic apis import <file|url> --vendor <vendor> --name <name>
--version <version>` (reads a local file inline or fetches a URL; async, prints
a job id). This needs `apis:write` rather than `catalog:import`.

`search` returns JSON when piped. Each hit carries both a registry
`operation_id` and a `_links.inspect` (a `/inspect?id=METHOD%20URL` link). Pass
the `operation_id` straight to `inspect`/`execute` — it resolves by registry key
— or use the `METHOD URL` pair the link decodes to. (The id shown by `jentic
catalog show` is the spec's `operationId`; `inspect` accepts that too, via a
fallback, but the `operation_id` from `search`/`apis operations` is the most
direct.)

If `search` returns no results, it prints a hint to run `jentic catalog search`
/ `jentic catalog import` first — that almost always means nothing relevant is
imported yet. Import and search again.

`jentic catalog outdated` lists registered APIs whose upstream spec changed since
import (also surfaced by `jenticctl status`). Re-importing promotes the new spec
to **live**, changing behavior, so this is an **operator** decision: **suggest**
the re-import (`jentic catalog import <vendor/name>`) to the operator and let them
run it — never silently re-import on your own. (`jentic catalog refresh` rebuilds
the catalog manifest from upstream but requires `org:admin`, so it too is an
operator action.)

**MCP session:** the same order, tool for tool — `search_catalog` →
`import_api` → `search_apis`:

```
search_catalog {"query": "spreadsheets", "limit": 10}
import_api {"api_id": "googleapis.com/sheets"}
search_apis {"query": "get values from a spreadsheet range", "limit": 10}
```

`import_api` runs the import as a job and tracks it in-process: on completion
it returns `{job_id, status, revisions, promoted}` with the imported revisions
promoted live. A duplicate import converges — re-importing is safe — but only
the HTTP mount reports it as an `already_imported` **success**; the stdio
server surfaces the same duplicate as a failed (dead-letter) import whose
error says identical content already exists — read that as "already there",
never as something to retry. If the result carries a non-terminal `status`
(queued, tracking timed out), poll `get_execution_result` with the `job_id`
from that result rather than re-importing. Each `search_apis` hit carries the
`operation_id` to pass straight to `inspect_operation`/`execute`.

**Before concluding "the data is gone", confirm which backend you're on.** If
APIs, credentials, or toolkits you *know* existed appear missing — or IDs look
unfamiliar — you may be talking to a **different** backend than you expect. A
hosted (`remote`) Jentic install and a `local` self-hosted one have independent
registries and credentials, and a CLI, an agent, or an MCP server can each be
bound to a different one. The primary check is the **`instance` stamp**: every
MCP tool result carries `instance` (`backend`, `host`, `instance_id`), and the
unauthenticated `GET /instance` endpoint reports the same identity
(`backend` is `local`/`remote` — the install's declared `server.backend` —
plus `canonical_base_url`, `host`, and an opaque `instance_id`, null when
telemetry is off). In a CLI session, read the same facts with:

```
jentic context view        # shows the active context's environment + base_url
jentic api GET /instance   # reads the connected backend's identity (auth attached)
```

If it's not the backend you meant to use (e.g. an MCP server on a remote
backend while you imported locally), repoint that client at the right base URL
rather than importing/searching again.

### 4. Inspect the operation's contract

Resolve an operation to its method, path, parameters, and schemas before
calling it. Pass the identifier from the search hit, or a `METHOD URL` pair.

**CLI session:**

```
jentic inspect "$(jentic search 'get spreadsheet values' --json | jq -r '.data[0].operation_id')"
jentic inspect 'GET https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/{range}'
```

On a 404, `inspect` prints the reason and a hint on stderr and exits 2 (it is
not silent). If you passed the id from `catalog show` and it didn't resolve, use
the `operation_id` from `search`/`apis operations`, or the `METHOD URL` pair
that the hit's `_links.inspect` decodes to.

**MCP session:**

```
inspect_operation {"operation_id": "op_abc123"}
inspect_operation {"operation_id": "GET:https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/{range}"}
```

Always inspect before you execute — the contract names the parameters and the
security requirements you'll propose rules against.

### 5. Execute through the broker

Send the request through the Jentic broker. The broker is a transparent forward
proxy, so the target is the **full upstream URL** (scheme + host + path), not a
host-relative path. Reference an `operation_id` from the search/inspect step —
the surface fills in the upstream URL for you — or pass `METHOD:URL` directly.
The broker authenticates you and injects the stored upstream credential
server-side; credentials never pass through your session.

**CLI session:**

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
  built-in default (`https://127.0.0.1:8100`) < the active environment's
  `broker_url` in `~/.config/jentic/config.yaml` < flags. `lookup
  broker.jentic.ai: no such host` means the environment points at the hosted
  broker from a local install; a TLS error like `server gave HTTP response to
  HTTPS client` against a local target means the broker is plain http but the
  target resolved to https. `jentic register` seeds `broker_url` for a loopback
  install; otherwise set it on the environment, or override per call:

```
jentic register --url <control-plane URL> --broker-url <broker URL>   # fills a missing broker_url
jentic env add <env> --url http://127.0.0.1:8000 --broker-url http://127.0.0.1:8100 --force
jentic execute <operation_id> --broker-scheme http --broker-host 127.0.0.1:8100
```

- **Missing broker on a remote install (`RESOLVE_FAILED`, exit 2).** If the
  environment's base_url is remote and no `broker_url` is set, `execute`
  refuses up front (it never dials the local default for a remote control
  plane). This means the environment was onboarded without a broker: set it
  with `jentic register --url <URL> --broker-url <broker URL>` (or
  `JENTIC_BROKER_URL` in file-less mode) — ask the operator for the broker
  URL; do **not** assume a local broker.

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

**MCP session:** call `execute` — or `execute_read` for any pure GET/HEAD
read (**prefer `execute_read` for reads**: same envelope, same flow, no
request body, and clients approve read-only tools more readily; it rejects
any other HTTP method — use `execute` for those):

```
execute {"operation_id": "op_abc123", "inputs": {"limit": 10}}
execute_read {"operation_id": "GET:https://sheets.googleapis.com/v4/spreadsheets/{id}/values/{range}", "inputs": {"id": "ABC", "range": "A1:Z10"}}
```

**The job-poll idiom.** A held or async execution does not answer inline: a
202 HELD response (human approval required) or a tracked import returns a
**job envelope** (`{job_id, status, …}`). Poll it with the job id until the
status is terminal (`completed`, `failed`, `cancelled`, `dead_letter`):

```
get_execution_result {"job_id": "<id from the held response>"}
```

**Never re-send the original call while a job is pending** — approval happens
out-of-band, and re-sending duplicates the side effect.

**The recovery mapping.** What the CLI lane surfaces as a stderr
`agent_directive` + exit codes arrives on MCP as a **coded error envelope**:
`{error_code, error, actionable_step, next_tool?, …, instance}`. Follow
`next_tool` **when present**. Two caveats on the HTTP mount:

- The mount drops any `next_tool` pointer that is not one of its served
  tools, so auth-code errors there typically carry **no** pointer — that
  means "relay to the operator", not "guess a tool".
- `actionable_step` prose (and the pinned tool descriptions) may still name
  stdio-only tools or `jentic` CLI verbs (`get_started`, `jentic register`,
  …). Read those as **operator guidance to relay**, never as tools for you
  to call.

And know the CLI-only arms: a `credential_not_provisioned` (424) denial
carries a `provisioning_url` — relay it to your operator to connect the
account; there is nothing an MCP tool can do to fix it (do **not** file
`request_access` for it). The denial taxonomy in step 2
(`no_toolkit_binding`, `credential_undecryptable`,
`credential_identity_mismatch`, `ambiguous_toolkit`) applies unchanged — the
same codes, delivered in the envelope instead of stderr.

## Quick Reference

- The authoritative CLI command + flag reference is **generated from the CLI
  itself**, not this file: run `jentic --help` / `jentic <command> --help`
  (always current, works offline), or open the platform docs at `/app/docs` on
  the control plane (Reference → CLI) — the same reference rendered for humans,
  next to the HTTP API and Broker API references.
- `jentic context view` — the active context (environment + identity + base_url); start here in a CLI session.
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
- `jentic register` / `jentic setup` — operator commands that create and
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
- `--dry-run` / `--export-plan` — on a mutating CLI command (`execute`,
  `apis import`), validate and print the request that WOULD be sent (a machine
  plan with `--export-plan`) **without** sending it. Use it to preview a call —
  including the exact broker URL and headers — before committing side effects.
- `jenticctl status` / `jenticctl start` — health-check and restart the local
  deployment; check this first when a local target refuses connections.
- Add `--json` to force machine-readable output on a terminal (works on
  `search`, `execute`, `inspect`, `apis`, `access`, `doctor`). `context view`
  has no `--json` flag — it emits JSON automatically in agent/non-TTY mode.
- **Correlation & retries (CLI):** export `JENTIC_SESSION_ID=<your session id>`
  and every request carries it as `X-Jentic-Session-Id`, so operators can group
  all of your calls in server logs; each `execute` also sends a fresh W3C
  `traceparent`. When you must retry a mutating call (POST/PUT), pass
  `--idempotency-key <uuid>` to `execute` — the server can then de-duplicate,
  and the CLI treats the request as safe for its transport-level retries.
- **MCP session — the mount serves exactly nine tools; each maps onto the
  loop:**
- `whoami` — your identity, status, scopes, and toolkit bindings with the APIs
  each one serves; start here and decide access from it.
- `search_apis` — search the imported registry for operations by
  natural-language query; each hit carries the `operation_id`.
- `inspect_operation` — one operation's full contract (method, URL,
  parameters, schemas, security); always inspect before executing.
- `execute` — run an operation through the broker (full upstream URL; the
  broker injects the credential server-side).
- `execute_read` — the GET/HEAD-only variant of `execute`; prefer it for
  every pure read.
- `get_execution_result` — poll a job id (from a held 202 execute or a
  non-terminal import) until its status is terminal.
- `search_catalog` — find importable APIs when the registry search comes up
  empty.
- `import_api` — import a catalog API into the registry; re-import is safe —
  the mount reports a duplicate as an `already_imported` success (the stdio
  server reports the same duplicate as a failed dead-letter import; either
  way it's already there).
- `request_access` — file ONE composite access request (provision/toolkits/
  scopes + reason), or poll a filed one with `{"request_id": "<id>"}`; relay
  `approve_url` to the human.
- **MCP structural facts:** every tool result carries an `instance` stamp
  (`backend`/`host`/`instance_id`) — your which-backend check. `get_started`
  and all `jentic` CLI verbs (`setup`, `access refresh`, `context`, `env`,
  `doctor`, `api`, `history`, `events`) do **not** exist on the HTTP mount —
  do not invent them.

## Pitfalls

- Executing before the agent is registered/approved fails — there is no
  usable identity. CLI session: check `jentic doctor`; if the Identity section
  warns, ask your operator to run `jentic register` (with `--url <install
  URL>` on a fresh machine) and approve you. MCP session: `whoami` errors or
  reports a pending status — relay to your operator; nothing you can call
  completes an approval.
- An empty search result (`{"data": []}`) usually means **nothing is imported
  yet**, not that you lack access. Go through the catalog (`jentic catalog
  search`/`import`, or `search_catalog`/`import_api`), then search again. Both
  reading the registry and importing a cataloged API need no grant — an
  approved agent already holds `apis:read` and `catalog:import` by default.
  (Importing arbitrary URL/inline specs via `POST /apis` is the only import
  path that needs `apis:write`.) Don't invent other "catalog read" scopes;
  they're rejected.
- **Verify which backend you're talking to before diagnosing "missing" APIs
  or credentials.** The primary check is the **`instance` stamp**
  (`backend`/`host`/`instance_id`) on every MCP tool result, and `jentic api
  GET /instance` / `jentic context view` in a CLI session. Two surfaces in
  one session (a CLI and an MCP server, or two MCP servers) can each be bound
  to a **different backend** — typically a hosted cloud workspace vs a local
  install. The symptom of a mismatch is *silent wrong answers*, not errors:
  an API the user just imported "doesn't exist", credentials "disappeared",
  or operation ids from one surface don't resolve on the other. Before
  concluding anything is missing or broken, compare the stamps (see
  "confirm which backend you're on" in step 3) — and stick to one surface
  for the whole task.
- An execute failure is not always an access problem. **CLI session:** a DNS
  or TLS error means the **broker target** is misconfigured (see step 5);
  connection refused on a **local** target usually means the instance is
  **stopped** — run `jenticctl status`, and if it's down tell the user to
  `jenticctl start`, then retry rather than abandoning the task. Only a
  broker **denial** (an `agent_directive` on stderr, exit **2** — or the
  coded error envelope on MCP) is an access/credential issue; the code names
  the recovery (see steps 2 and 5). Follow the directive/envelope; don't
  keep re-sending the same execute.
- You file and wait for access; you can't approve your own requests.
- **Don't execute to test access.** `whoami` already tells you what your
  bindings **serve**; if the API you need isn't there, file the provisioning
  plan and wait — don't fire an execute you expect to be denied just to read
  the recovery directive. The directive is a fallback for surprises, not a
  discovery step.
- The `operation_id` from the registry search resolves directly; the id from
  `catalog show` is the spec `operationId` (inspect resolves it via a
  fallback). If one doesn't resolve, try the `METHOD URL` pair from the hit's
  `_links.inspect` — don't guess ids.
- **MCP session:** never re-send an execute while its job is pending — poll
  `get_execution_result` with the job id; approval happens out-of-band and
  re-sending duplicates the side effect.
- **MCP session:** don't file `request_access` to fix a 424
  `credential_not_provisioned` error — it carries a `provisioning_url` for
  your **operator**; an access request cannot connect an account.
- **MCP session:** don't call `get_started` on the HTTP mount — it isn't
  there. Its absence is a transport tell (you're on the daemon mount), not an
  outage; don't retry it or report it as a failure.
- **MCP session:** when an error envelope's `actionable_step` names a
  `jentic` CLI verb or a tool your session doesn't have, relay it to the
  operator as guidance instead of inventing a tool call.

## Verification

- CLI session: `jentic doctor` shows a resolvable identity with a valid token.
- CLI session: after `jentic catalog import <vendor/name>`, `jentic search
  "<something in that API>"` returns at least one result.
- CLI session: a known-allowed `jentic execute …` (pointed at the right
  broker) returns a 2xx response body.
- MCP session: `whoami` answers with your identity (id, status, scopes,
  bindings) and an `instance` stamp.
- MCP session: after `import_api`, `search_apis` finds operations from that
  API.
- MCP session: a known-allowed `execute_read` returns a 2xx response body.
