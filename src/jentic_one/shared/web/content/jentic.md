---
name: jentic
description: Use this skill whenever the user wants to work with a third-party or external API/tool through the Jentic platform — e.g. asks to "find the vessel-tracking API and add it", "get rows from this Google Sheet", connect Slack, import/search/discover an API, integrate or automate a SaaS, pull data from a service, or call an external endpoint. Prefer launching this before ToolSearch or hand-rolled HTTP: it drives the audited Jentic loop (identity → discover → request access → execute) over whichever Jentic surface the session has — `jentic` MCP tools or the `jentic` CLI — even inside a code repo. Do NOT use it for local-only work (editing code, finding files, adding a package/dependency, or questions with no external API call).
version: 3
---

# Using Jentic

Jentic is an API broker: you discover operations across many APIs, then execute
them through a single authenticated gateway without managing each API's
credentials yourself. The same audited loop — identity → discover → request
access → execute — is exposed over two surfaces. This document is the
surface-neutral guide to the loop; the per-surface mechanics live in the
`references/` files next to it. **Work out which session you are in first**,
then load ONLY your lane's reference:

- **CLI session** — the `jentic` CLI is on PATH. Read `references/cli.md`
  for every command, flag, exit code, and failure diagnosis.
- **MCP session** — your session has `jentic` MCP **tools** (`whoami`,
  `search_apis`, `execute`, …). Read `references/mcp.md` for the tool
  surface, call shapes, envelopes, and recovery. (It also explains the
  stdio-vs-HTTP-mount tell — `get_started` present vs absent — and what each
  flavor can and cannot do.)
- For pitfalls, the command/tool cheatsheet, and verification checklists in
  both lanes, read `references/recovery.md`.

Both lanes drive the same loop against the same backend. The `instance`
stamp (`backend`/`host`/`instance_id`) on every MCP tool result — and the
unauthenticated `GET /instance` endpoint — is the which-backend-am-I-on
check; in a CLI session `jentic context view` reads the same facts.

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

- A Jentic identity your human operator set up and a human approved — you
  never mint your own. How the credential is attached differs by lane (the
  CLI attaches a token; an MCP client presents an OAuth grant or bearer key
  per request); you never see or handle the raw credential in either lane.
- A reachable Jentic deployment (control plane + broker). Reachability and
  base-URL configuration are lane concerns — see your lane's reference file.
- **CLI session:** read `references/cli.md` before running commands — it is
  the command, flag, and environment contract for this lane.
- **MCP session:** read `references/mcp.md` before calling tools — it is the
  tool-surface and envelope contract for this lane.

## Procedure

Each step below is shared doctrine — what to do and in what order. The exact
commands (CLI) and tool calls (MCP) for every step are in `references/cli.md`
and `references/mcp.md`; follow your session's lane through every step and
never execute the other lane's verbs.

### 1. Confirm you have a valid identity

You normally don't set up your own identity — your human operator connects
this agent to a Jentic install out-of-band (via `jentic register`/`jentic
setup` for a CLI machine, or by authorizing the MCP connection) and a human
approves it. First, check your setup: run your lane's identity check (CLI
`jentic doctor`; MCP `whoami`). If it reports a registered, approved
identity with a usable credential, move on to step 2.

If it does not (no context, not registered, pending approval, or an auth
error), **stop and relay to your operator** — registration and approval block
on a human and cannot be completed by an autonomous agent. Your lane's
reference names the exact recovery for each state.

### 2. Check what you can do, and request access if needed

Your identity view (CLI `jentic access whoami`; MCP `whoami`) lists your
status, scopes, and toolkit bindings; each binding lists the APIs it
**serves** (`serves: [{api_vendor, api_name, api_version}]`). This tells you
exactly what you can already call. Combined with the catalog (what's
available to add — step 3), it's your map of the workspace.

**Decide access from that view first — do NOT execute an operation just to
see whether you have access.** A denied execute is a wasted round-trip; you
can tell in advance:

- If a binding already **serves** the API you need → you have access. Skip
  straight to inspect/execute; file no request.
- If **nothing** you're bound to serves it → you do **not** have access yet.
  Provision it **before** your first execute — do not "try execute and
  branch on the denial".

**File once, richly — never thrash.** Work out the full access end-state up
front — from your identity view, the catalog, and the task — and file it as
**one composite request** (CLI `jentic access request`; MCP
`request_access`) covering every API the job needs, so the human
decides in one sitting. Always include a reason: a human reviews it before
approving and your reason is shown to them — a clear one-liner ("fetch the
user's open PRs to summarise them") is what gets you approved faster. Never
file duplicate or per-operation requests, and don't withdraw-and-refile to
tweak a proposal. Granting is always a human action — you file and wait, you
never approve yourself. Be honest about the terminal states: **denied** →
read the items' `decision_reason` to learn *why* before giving up;
**partially approved** → proceed only with what was actually granted.

#### Proposing permission rules from the spec

A provisioning plan is your chance to propose the credential's auth type and
its permission rules as a **first pass** — a human reviews and edits them
before approving. Do the work up front: inspect the operations you intend to
call (step 4's lane verb) to read the methods, paths, and declared security
schemes; pick the auth type from what the spec declares (`bearer`,
`api_key`, `basic`, `oauth2`, or `none`); then translate the user's
plain-English intent into concrete `allow`/`deny` rules with
`methods`/`path`, e.g.
`[{"effect":"allow","methods":["GET"],"path":".*"}, {"effect":"allow","methods":["POST","PUT"],"path":"/boards/prod/.*"}]`.
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
first-match-wins, so an early broad `allow` shadows every rule after it, and
a rule set that contradicts the intent you stated in your reason will
confuse the human reviewing it. You never enter the credential secret and
you never approve — the human fills the secret in the dashboard and grants
the plan. You propose; they decide.

A plain toolkit-binding request is only the **last mile** — use it when a
toolkit for the API already exists (e.g. an operator created one) and you
just need to be bound to it. When nothing serves the API yet, a provisioning
plan is the right first move; a bare toolkit bind would auto-deny with
`decision_reason: "No toolkit serves API <vendor/name>; provision and bind a
credential for it first, then request the toolkit binding"` — that is the
signal to file the provisioning plan instead.

### 3. Find an operation (import first, then search)

Operation search only sees operations that have been **imported into this
deployment's local registry**. On a fresh install the registry is empty, so
a search returns `{"data": []}` until you import something. **Import before
you search** — if the user already named the API/vendor (e.g. "Google
Sheets"), go straight to the catalog; don't search an empty registry first
and waste a call. The discovery order is: browse the public catalog for an
importable API → import the one you want (auto-promotes to live) → search
the local registry.

Importing an **already-cataloged** API is gated on `catalog:import`, which
an approved agent holds **by default** — no access request needed. Just run
the import. Re-importing an API that is already there is safe — the registry
state **converges** either way — but the surfaces report the duplicate
differently (a success on the HTTP mount, a dead-letter "identical content
already exists" error on the stdio server and the CLI); your lane's
reference names the exact shape. Treat it as "already there" — don't retry,
and don't file an access request for a made-up "catalog read" scope: reading
the registry and importing a cataloged API need no grant.

**Before concluding "the data is gone", confirm which backend you're on.**
If APIs, credentials, or toolkits you *know* existed appear missing — or IDs
look unfamiliar — you may be talking to a **different** backend than you
expect: a hosted (`remote`) install and a `local` self-hosted one have
independent registries and credentials, and each surface in your session can
be bound to a different one. Check the `instance` stamp (MCP) or
`jentic context view` / `GET /instance` (CLI) and repoint the client at the
right base URL rather than importing/searching again.

### 4. Inspect the operation's contract

Resolve an operation to its method, path, parameters, and schemas before
calling it (CLI `jentic inspect`; MCP `inspect_operation`). Pass the
`operation_id` from the search hit, or a `METHOD URL` pair. Always inspect
before you execute — the contract names the parameters and the security
requirements you'll propose rules against.

### 5. Execute through the broker

Send the request through the Jentic broker (CLI `jentic execute`; MCP
`execute` / `execute_read`). The broker is a transparent forward proxy, so
the target is the **full upstream URL** (scheme + host + path), not a
host-relative path. Reference an `operation_id` from the search/inspect step
— the surface fills in the upstream URL for you — or pass `METHOD:URL`
directly. The broker authenticates you and injects the stored upstream
credential server-side; credentials never pass through your session.

A held or async execution does not answer inline: it returns a **job
envelope** (`{job_id, status, …}`). Poll the job until its status is
terminal — and **never re-send the original call while a job is pending**:
approval happens out-of-band, and re-sending duplicates the side effect.

A denied or failed execute carries a coded recovery (the CLI's stderr
`agent_directive` + exit codes; MCP's coded error envelopes). Follow the
directive/envelope rather than assuming which recovery applies — the denial
taxonomy and its per-code recoveries are in your lane's reference. Diagnose
a failure by its symptom, not by assuming access: transport failures
(DNS/TLS/refused) and denials recover differently.

## Quick Reference

- The loop, in either lane: identity check → decide access from your
  bindings (file ONE composite request if short) → catalog import → registry
  search → inspect → execute through the broker.
- CLI session: the full command cheatsheet is in `references/cli.md` (and
  `jentic --help` is always current); MCP session: the 9 mount tools / 10
  stdio tools with one-line whens are in `references/mcp.md`.
- Both cheatsheets, side by side with pitfalls and verification, are in
  `references/recovery.md`.

## Pitfalls

- Executing before the agent is registered/approved fails — there is no
  usable identity. Run your lane's identity check and relay the recovery to
  your operator; nothing you can call completes an approval.
- An empty search result (`{"data": []}`) usually means **nothing is
  imported yet**, not that you lack access. Go through the catalog, then
  search again.
- **Don't execute to test access.** Your identity view already tells you
  what your bindings **serve**; if the API you need isn't there, file the
  provisioning plan and wait. The recovery directive is a fallback for
  surprises, not a discovery step.
- You file and wait for access; you can't approve your own requests.
- **Verify which backend you're talking to before diagnosing "missing" APIs
  or credentials** — compare `instance` stamps (see step 3) and stick to one
  surface for the whole task.
- The lane-specific pitfall lists (broker-URL/DNS diagnosis, stopped
  instances, job-pending rules, envelope caveats) are in
  `references/recovery.md` — read your lane's section before debugging.

## Verification

- Your lane's identity check answers with a registered, approved identity.
- After importing an API from the catalog, a registry search for something
  in that API returns at least one result.
- A known-allowed read executed through the broker returns a 2xx response.
- The lane-specific checklists (exact commands and tool calls) are in
  `references/recovery.md`.
