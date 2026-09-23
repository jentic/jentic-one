# MCP lane — read this when your session has `jentic` MCP tools

This file carries the MCP-session mechanics for every step of the Jentic
loop in `SKILL.md` (identity → discover → check access → execute): the
tool surface, call shapes, error envelopes, and recovery. If your session
drives Jentic through the `jentic` CLI instead, close this file and read
`references/cli.md`.

## Which MCP flavor are you on?

Two servers expose the same loop; **check `tools/list` to tell them apart**:

- If `get_started` appears in `tools/list`, you are normally talking to the
  local `jentic mcp` **stdio server** — a machine that has the CLI, so CLI
  recovery *may* also be available (defeasible: `--exclude-tools
  get_started` can hide it there, so treat presence as a strong hint,
  absence as the reliable direction). The stdio server serves **ten** tools:
  the nine below plus `get_started`.
- If `get_started` is **absent**, you are on the daemon's **HTTP `/mcp`
  mount**: **no CLI exists** — never tell the operator to run `jentic …` "on
  this machine"; there is no this-machine. The mount serves exactly the
  **nine** tools below.

Both flavors drive the same loop against the same backend; every tool result
carries an `instance` stamp (`backend`/`host`/`instance_id`) — your
which-backend-am-I-on check (the unauthenticated `GET /instance` endpoint
reports the same identity: `backend` is `local`/`remote` — the install's
declared `server.backend` — plus `canonical_base_url`, `host`, and an opaque
`instance_id`, null when telemetry is off).

## Prerequisites (MCP session)

An authenticated MCP connection. The credential (an OAuth grant your
operator authorized when connecting the client, or a bearer token/API key
the client presents at the transport) is attached by the MCP client on every
request — you never run setup, never handle token refresh, and never see the
raw credential. On the HTTP mount your permissions and bindings are resolved live
per request, so an approved grant works on the very next tool call with no
re-mint step (one exception: the session's OAuth consent ceiling — see the
access step).

## Step 1 — identity

Call `whoami` — it answers with your identity as the control plane sees it:
id, **status**, permissions, and credential bindings.

```
whoami {}
```

If `whoami` succeeds and the status is active/approved, skip to step 2. If
it errors with an auth code, or the status is pending, **stop and relay to
your operator**: the connection's grant or credential needs
(re-)authorization or the agent awaits approval — nothing you can call fixes
it. Only call `get_started` to self-diagnose if it actually exists in your
`tools/list` (stdio sessions); on the HTTP mount it does not exist — do not
invent it.

## Step 2 — access

The decide-first doctrine (see `SKILL.md` step 2) is driven by `whoami`:
read your bindings and the APIs they serve, and decide up front whether the
job is coverable. When a credential is missing for a vendor in the
deployment's connect registry, **start the connection yourself** with
`request_connection`:

```
request_connection {"vendor": "github", "reason": "read open PRs to summarise them"}
```

It returns `{session_id, approval_url, resolved_flow}` — relay the
`approval_url` to your human operator, who opens it in their browser and
approves the connection and its scopes (you cannot open or approve it, and
the tool never polls). Once they confirm, call `whoami` to see the new
binding, then retry the blocked call. Optionally shape the ask with
`requested_scopes` (vendor scope names; write scopes are flagged for the
approver) and a `reason` the approver sees.

For everything else, **report the gap to your human operator in one
complete summary** — the API (vendor/name), the auth type the spec
declares, the operations you intend to call, your proposed permission
rules, and why. Approval is always a human action, and so are binding an
existing credential and scope grants: the operator acts in the Jentic One
dashboard; your job is to relay the gap, not to grant it.

Bindings resolve live per request on the HTTP mount, so once your operator
confirms, the very next tool call sees the new access — just retry what was
blocked. One exception: if the session's own transport credential was
authorized with a narrower consent (an OAuth grant that never included a
scope you need), no dashboard grant can widen it — tell the operator
**re-authorization of the connection is required** — never "retry and it
will work". Denial recovery in an MCP session arrives as coded error
envelopes on `execute` (see step 5), not stderr directives.

To propose permission rules from the spec (the doctrine and honesty rules
are in `SKILL.md` step 2), read the operation surface first with
`inspect_operation` on the operations you intend to call — it shows methods,
paths, and the declared auth.

## Step 3 — find an operation (import first, then search)

The same order as the doctrine, tool for tool — `search_catalog` →
`import_api` → `search_apis`:

```
search_catalog {"query": "spreadsheets", "limit": 10}
import_api {"api_id": "googleapis.com/sheets"}
search_apis {"query": "get values from a spreadsheet range", "limit": 10}
```

`import_api` runs the import as a job and tracks it in-process: on
completion it returns `{job_id, status, revisions, promoted}` with the
imported revisions promoted live. A duplicate import converges —
re-importing is safe — but only the HTTP mount reports it as an
`already_imported` **success**; the stdio server surfaces the same duplicate
as a failed (dead-letter) import whose error says identical content already
exists — read that as "already there", never as something to retry. If the
result carries a non-terminal `status` (queued, tracking timed out), poll
`get_execution_result` with the `job_id` from that result rather than
re-importing. Each `search_apis` hit carries the `operation_id` to pass
straight to `inspect_operation`/`execute`.

If APIs or credentials you know existed appear missing, compare `instance`
stamps before diagnosing (see `SKILL.md` step 3): an MCP server on a remote
backend while you imported locally answers with *silent wrong answers*, not
errors. Repoint that client at the right base URL rather than
importing/searching again.

## Step 4 — inspect

```
inspect_operation {"operation_id": "op_abc123"}
inspect_operation {"operation_id": "GET:https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/{range}"}
```

Always inspect before you execute — the contract names the parameters and
the security requirements you'll propose rules against.

## Step 5 — execute

Call `execute` — or `execute_read` for any pure GET/HEAD read (**prefer
`execute_read` for reads**: same envelope, same flow, no request body, and
clients approve read-only tools more readily; it rejects any other HTTP
method — use `execute` for those):

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

**Never re-send the original call while a job is pending** — approval
happens out-of-band, and re-sending duplicates the side effect.

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

And know the recovery split: a `credential_not_provisioned` (424) or an
unserved `no_credential_binding` (403) denial is **provisioning-shaped** —
its envelope points `next_tool` at `request_connection`. When the directive
carries a `suggested_command` naming the registry key, start the fix
yourself (`request_connection` with that key) and relay the `approval_url`;
a denial whose recovery carries only a `provisioning_url` is for your
operator — relay it so they can connect the account. The denial taxonomy
(`no_credential_binding`, `credential_undecryptable`,
`credential_identity_mismatch`,
`ambiguous_credential_binding` — the per-code meanings are surface-independent
and live in `references/recovery.md`) applies unchanged — the same codes,
delivered in the envelope instead of stderr.

## The 9 mount tools (each maps onto the loop)

- `whoami` — your identity, status, permissions, and credential bindings with the
  APIs each one serves; start here and decide access from it.
- `request_connection` — start a connect session for a registry vendor when
  no binding serves the API you need; relay the returned `approval_url` to
  your operator (they approve — you never poll or approve), then confirm
  with `whoami` and retry.
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

The stdio server serves these nine plus `get_started` (pre-auth setup
diagnosis on a CLI machine).

**Structural facts:** every tool result carries an `instance` stamp
(`backend`/`host`/`instance_id`) — your which-backend check. `get_started`
and all `jentic` CLI verbs (`setup`, `context`, `env`,
`doctor`, `api`, `history`, `events`) do **not** exist on the HTTP mount —
do not invent them.
