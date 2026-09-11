# CLI lane — read this when your session has the `jentic` CLI on PATH

This file carries the CLI-session mechanics for every step of the Jentic
loop in `SKILL.md` (identity → discover → request access → execute): the
commands, flags, exit codes, environment variables, and failure diagnosis.
If your session drives Jentic through MCP tools instead, close this file and
read `references/mcp.md`.

## Prerequisites (CLI session)

- The `jentic` CLI is installed and on PATH.
- A reachable Jentic control plane. The base URL comes from the active
  context's environment — set it with `jentic env add <name> --url <URL>`
  and select it with `jentic context use <name>` (inspect via
  `jentic context view`). Onboard a fresh machine with `jentic register
  --url <URL>`. There is no `--base-url` flag on data-plane commands.
- Remote deployments: if the environment's base URL is remote (an
  `https://…` host in `jentic context view`), the broker must be set
  explicitly too — `broker_url` on the environment (pass `--broker-url` to
  `register`, or `jentic env add`), or `JENTIC_BROKER_URL` in file-less
  mode. It is never derived from the control-plane URL. A loopback install
  seeds it automatically.

## Step 1 — identity

```
jentic doctor
```

If the Identity section passes (a registered identity and a usable token or
API key), skip to step 2. If it does not (no context, "not registered", or
"pending"), **stop and ask your operator** to run
`jentic register --url <install URL>` and approve the agent — that step
blocks on a human and cannot be completed by an autonomous agent. (For a
local install the URL must be `http://127.0.0.1:8000`, not `localhost` — the
token audience is matched exactly.) Once approved, a token is minted and
reused automatically; you never handle raw API credentials — the CLI
attaches the bearer token for you.

If your operator self-registered this agent and handed you a one-time claim
token, bind it to a human identity with `jentic identity claim <agent-id>
--token <token>` (an agent cannot claim itself; requires an active human
context).

## Step 2 — access

See your own identity, status, scopes, and toolkit bindings (with the APIs
each one serves):

```
jentic access whoami
```

Decide access from that view first (see `SKILL.md` step 2 for the doctrine),
then file ONE composite request when something is missing:

```
jentic access request --provision <vendor/name> \
  --auth <bearer|api_key|basic|oauth2|none> \
  --rules-json '[{"effect":"allow","methods":["GET"],"path":".*"}]' \
  --reason "why you need this — shown to the human who approves it" \
  --wait
```

`--wait` blocks until a human fulfils and approves the plan in the
dashboard; once approved, the toolkit binding is live immediately — just
retry `execute`. Always pass `--reason` on **every** access request
(`--provision`, `--toolkit`, or `--scope`). You normally do **not** need
`jentic access refresh` after a `--provision` plan: bindings take effect
live, and a plan grants no new token scope. Only refresh after an approved
`scope:grant` **and** only if `whoami` flags the scope as not yet on your
token (see the stale-scope note it prints).

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
If a composite collides with an older pending request for one of its
targets, nothing is filed — drop that target or `jentic access withdraw` the
old request, then re-file. `--wait` can end `partially_approved` (exit 4):
check `jentic access status <id>` to see which items were granted before
proceeding. Without `--wait` you get a request id and an `approve_url` to
hand to your operator.

Track and manage your requests:

```
jentic access list
jentic access status <request_id>
jentic access withdraw <request_id>
```

`--wait` blocks until a human decides and sets the CLI exit code: **0** =
approved, **2** = denied — read the item's `decision_reason` (in the JSON,
or shown under the item on a TTY) to learn *why* before giving up, **3** =
still pending when `--timeout` elapsed (poll later with `jentic access
status <id>`), **4** = partially approved. `--wait`'s `--timeout` is a
duration **with a unit** — `--timeout 120s`, `2m`, `90s`. A bare number
(`--timeout 120`) is rejected. Once a request is approved, retry the
`execute` that was denied.

### The reactive path: denial directives

If you'd rather be reactive, the broker also guides you: when `execute` is
denied it prints a recovery line on stderr (the `agent_directive`) and
**exits 2**, so you can branch on the exit code instead of mistaking the 4xx
body for success. The per-code MEANINGS (what each denial signifies, the
`toolkit_serves_api` fork, `parameters.expected` vs `parameters.found`,
which recoveries an access request can and cannot fix) are shared by both
lanes and live in `references/recovery.md`; what follows is this lane's
mechanics per code:

- **`no_toolkit_binding` (403)** — with `toolkit_serves_api: false` the
  directive's `suggested_command` points at a **provisioning plan**;
  propose the auth type and permission rules you read from the API spec:

```
jentic access request --provision stripe.com/api \
  --auth bearer \
  --rules-json '[{"effect":"allow","methods":["GET"],"path":".*"}]' \
  --reason "why you need this — shown to the human who approves it" \
  --wait
```

  With `toolkit_serves_api: true` the directive instead suggests
  `jentic access request --toolkit <vendor/name> --wait`. File the suggested
  form and wait for approval.
- **`credential_not_provisioned` (424)** — the directive carries a
  `provisioning_url`: hand it to your operator to connect the account, then
  retry. Do not file an access request for it.
- **`credential_undecryptable` (424)** — ask your operator to remove and
  re-add the credential, then retry; nothing you can file fixes it.
- **`credential_identity_mismatch` (403)** — read the directive's
  `parameters.expected` vs `parameters.found` and ask your operator to fix
  or re-provision the credential so it targets `expected`, then retry.
- **`ambiguous_toolkit` (409)** — resend the same `execute` with `--header
  Jentic-Toolkit-Id=<toolkit_id>` picked from the directive's `candidates`
  (the directive also gives a copy-pasteable `suggested_command`).

Always follow the `agent_directive`'s `suggested_command` /
`provisioning_url` rather than assuming which recovery applies. You can also
request access proactively before you're denied. To propose rules from the
spec, read the operation surface first: `jentic apis operations
<vendor/name/version>` and `jentic inspect <operation_id>` show methods,
paths, and the declared auth.

## Step 3 — find an operation (import first, then search)

```
jentic catalog search "spreadsheets"
jentic catalog import googleapis.com/sheets
jentic search "get values from a spreadsheet range" --limit 10
jentic apis operations googleapis-com/googleapis-com-sheets/v4
```

Re-importing an API that is already there is safe — the registry state
converges — but this lane surfaces the duplicate as a **failed
(dead-letter) import** whose error says a revision with identical content
already exists. Treat that error as "already there" — don't retry. (The HTTP
MCP mount reports the same duplicate as an `already_imported` success — a
surface difference, not a state difference.)

If `import` unexpectedly fails with `403 … requires one of: catalog:import`
— e.g. you were approved before `catalog:import` became a default scope and
weren't re-granted — request it, wait for a human to approve, refresh your
token, then retry:

```
jentic access request --scope catalog:import --reason "import the Sheets API to read the user's spreadsheet" --wait
jentic access refresh
jentic catalog import googleapis.com/sheets
```

To register an API that is **not** in the catalog, upload its OpenAPI spec
directly with `jentic apis import <file|url> --vendor <vendor> --name <name>
--version <version>` (reads a local file inline or fetches a URL; async,
prints a job id). This needs `apis:write` rather than `catalog:import`.

`search` returns JSON when piped. Each hit carries both a registry
`operation_id` and a `_links.inspect` (a `/inspect?id=METHOD%20URL` link).
Pass the `operation_id` straight to `inspect`/`execute` — it resolves by
registry key — or use the `METHOD URL` pair the link decodes to. (The id
shown by `jentic catalog show` is the spec's `operationId`; `inspect`
accepts that too, via a fallback, but the `operation_id` from
`search`/`apis operations` is the most direct.)

If `search` returns no results, it prints a hint to run `jentic catalog
search` / `jentic catalog import` first — that almost always means nothing
relevant is imported yet. Import and search again.

`jentic catalog outdated` lists registered APIs whose upstream spec changed
since import (also surfaced by `jenticctl status`). Re-importing promotes
the new spec to **live**, changing behavior, so this is an **operator**
decision: **suggest** the re-import (`jentic catalog import <vendor/name>`)
to the operator and let them run it — never silently re-import on your own.
(`jentic catalog refresh` rebuilds the catalog manifest from upstream but
requires `org:admin`, so it too is an operator action.)

To read the connected backend's identity facts in this lane:

```
jentic context view        # shows the active context's environment + base_url
jentic api GET /instance   # reads the connected backend's identity (auth attached)
```

## Step 4 — inspect

```
jentic inspect "$(jentic search 'get spreadsheet values' --json | jq -r '.data[0].operation_id')"
jentic inspect 'GET https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/{range}'
```

On a 404, `inspect` prints the reason and a hint on stderr and exits 2 (it
is not silent). If you passed the id from `catalog show` and it didn't
resolve, use the `operation_id` from `search`/`apis operations`, or the
`METHOD URL` pair that the hit's `_links.inspect` decodes to.

## Step 5 — execute

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
> broker denial, a failed operation resolve, or missing local context (e.g.
> no active context configured). Don't blind-retry an exit 2: change the
> ask, fix the config, or request access. Exit **3** (still pending) and the
> transient transport failures are the retryable ones.

- **Wrong target (DNS or TLS error).** The broker target resolves as
  built-in default (`https://127.0.0.1:8100`) < the active environment's
  `broker_url` in `~/.config/jentic/config.yaml` < flags. `lookup
  broker.jentic.ai: no such host` means the environment points at the hosted
  broker from a local install; a TLS error like `server gave HTTP response
  to HTTPS client` against a local target means the broker is plain http but
  the target resolved to https. `jentic register` seeds `broker_url` for a
  loopback install; otherwise set it on the environment, or override per
  call:

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

- **Stopped instance (connection refused on a local target).** If the
  target is already local (`127.0.0.1` / `localhost`) and the connection is
  *refused*, the target is right and the instance is probably **not
  running** (rebooted machine, Docker not started). Health-check before
  concluding anything: `jenticctl status` reports whether the control-plane
  server and broker are reachable. If they're down, do not retry, guess, or
  quietly give up — tell the user plainly, e.g. *"Your Jentic One instance
  appears to be stopped, which is why I can't reach {API}. Restart it with
  `jenticctl start` (then `jenticctl status` to confirm), and I'll retry."*
  After the restart, retry the original call and continue the task.

## Correlation, retries, and dry runs

- Export `JENTIC_SESSION_ID=<your session id>` and every request carries it
  as `X-Jentic-Session-Id`, so operators can group all of your calls in
  server logs; each `execute` also sends a fresh W3C `traceparent`. When you
  must retry a mutating call (POST/PUT), pass `--idempotency-key <uuid>` to
  `execute` — the server can then de-duplicate, and the CLI treats the
  request as safe for its transport-level retries.
- `--dry-run` / `--export-plan` — on a mutating CLI command (`execute`,
  `apis import`), validate and print the request that WOULD be sent (a
  machine plan with `--export-plan`) **without** sending it. Use it to
  preview a call — including the exact broker URL and headers — before
  committing side effects.
- Add `--json` to force machine-readable output on a terminal (works on
  `search`, `execute`, `inspect`, `apis`, `access`, `doctor`).
  `context view` has no `--json` flag — it emits JSON automatically in
  agent/non-TTY mode.
