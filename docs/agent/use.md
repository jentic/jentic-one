# Use Jentic One — agent patterns

You have a running install ([install.md](install.md)) and an approved
identity. This file orients you; the **authoritative, detailed playbook is the
Jentic skill** — [`skills/jentic/SKILL.md`](../../skills/jentic/SKILL.md) —
which `jentic skill init` installs into your runtime's native layout
(Claude/Cursor/Codex/…). Read that skill before driving real calls; this page
is the map, not the territory.

## The loop

Every task against an external API follows the same audited loop:

```bash
jentic catalog search "<capability>"   # 1. find an importable API (public catalog)
jentic catalog import <vendor/name>    #    import it into the local registry
jentic search "<what you want to do>"  # 2. find the operation — each hit gives its METHOD and URL
jentic inspect GET:https://api.example.com/v1/things/{id}     # 3. params, schemas, auth
jentic execute GET:https://api.example.com/v1/things/{id} --path id=abc   # 4. call it through the broker
```

Key behaviours (details and full flag syntax in the skill):

- **Get credentials through the connect flow, never by asking for the
  secret.** When no bound credential serves the API, a denied `execute`
  names the recovery: `jentic connect <vendor>` starts an agent-driven
  OAuth flow (`POST /integrations:connect`) against the deployment's
  vendor registry — a human consents inside the flow, and the credential,
  agent binding, and permissions land together. You never approve
  yourself, and you never see the credential secret. For APIs the vendor
  registry doesn't cover, hand off to a human: the operator stores the
  credential and binds your agent in the console.
- **Import before search.** A fresh registry is empty; `search` returning
  `{"data": []}` means nothing is imported, not that you lack access.
- **Denials teach you.** A denied `execute` exits 2 and prints an
  `agent_directive` on stderr with the exact recovery
  (`no_credential_binding`, `credential_not_provisioned`, …). Follow its
  `suggested_command`; never re-send the same call.
- The broker is a **forward proxy**: an execute target is always the
  operation's method plus its **full upstream URL** (`METHOD:https://…` —
  scheme, host, and path), never a host-relative path. Take the `METHOD URL`
  pair straight from the `search` hit; `search` only sees operations already
  imported into this instance's registry.

## Rules for acting

1. **Never guess a command or flag.** This CLI is not apt/npm/gh — commands
   like `catalog --update` or `import` do not exist. Before the first use of
   any command, run `jentic <command> --help`; every failure also prints the
   exact next command on stderr, so read the error before trying anything else.
2. **A freshly imported API has no credential.** Importing puts the API in
   the registry; it does not connect an account. The connect flow is the only
   agent-side way to get a credential — and for vendors the registry covers,
   `jentic connect <vendor>` imports the API for you as well.
3. **Hand off when told to.** A `prompt_human` directive (missing secret,
   vendor not in the registry, lapsed account link) means a human must act
   in the console — report it to the operator and wait; never re-send the
   same call hoping for a different answer.

## How to do an action (worked example)

Task: *"get the current Bitcoin price"* on a fresh instance — nothing
imported, no credential bound yet.

```bash
# 1. Find and import the API from the public catalog
jentic catalog search "crypto prices"
jentic catalog import coincap-io/coincap-io

# 2. Find the operation — the hit gives you its METHOD and URL
jentic search "get current asset price"

# 3. Inspect, then execute with that exact METHOD + URL
jentic inspect GET:https://rest.coincap.io/v3/assets/{id}
jentic execute GET:https://rest.coincap.io/v3/assets/{id} --path id=bitcoin
```

If step 3 is denied (exit 2), the `agent_directive` on stderr names the exact
recovery: `jentic connect <vendor>` when the deployment's vendor registry can
mint the credential (an OAuth flow a human consents to), or a hand-off to the
operator — they store the credential and bind your agent in the console —
when it cannot. Follow the directive instead of retrying the same call.

## Machine-friendly behaviour

- Add `--json` for machine-readable output. It exists on the **leaf**
  commands (`search`, `execute`, `inspect`, `doctor`, `apis list`, …) —
  the bare group commands (e.g. `jentic apis`) reject it. Do not rely on
  "non-TTY output is JSON
  automatically": `jentic register` persists `mode: human` in the context it
  creates, and an explicit mode short-circuits the TTY check, so piped
  output is prose on most installs. For a fully machine posture set
  `JENTIC_MODE=agent` — note it also deadlines most commands at 60 s
  (pass `--timeout` on long waits such as `register`).
- Exit codes are a coarse contract: **0** ok, **1** transport/unexpected,
  **2** "cannot succeed as asked" (denial, resolve failure, missing context —
  do not blind-retry), **3** timed out still pending (retry later). Two
  caveats: `execute` exits **0 for any
  non-denial broker response**, including 429 rate-limits, 503 shed/circuit
  responses and 504 timeouts — always check the HTTP status in the JSON
  envelope, never the exit code alone. Exit 1 is also broader than
  "transport": several deterministic, named-fix errors (e.g.
  `MIGRATION_REQUIRED`, `NOT_AUTHENTICATED`, `PENDING_APPROVAL`) also exit 1
  — read the envelope's `error.code` and `actionable_step` before treating 1
  as retryable.
- Export `JENTIC_SESSION_ID=<id>` so operators can correlate your
  **control-plane** calls in the audit log (brokered `execute` calls land in
  Executions, which carries no session column); pass `--idempotency-key
  <uuid>` when retrying mutating calls.
- `jentic api <METHOD> <path>` is an authenticated passthrough to any
  control-plane endpoint (`jentic api ops` lists them); full route/scope
  reference: [endpoints.md](../reference/endpoints.md).

## What stays human

| Action | Where the human does it |
| ------ | ----------------------- |
| Approve a new agent | `/app/agents` in the console |
| Consent to a vendor connect flow | inside the OAuth flow itself — the denial directive (or `jentic connect <vendor>`) surfaces the link to hand them |
| Store credentials the connect flow can't mint, bind agents | the console (`/app`) — the agent detail page's **Access** tab |
| Create/manage users | `/app` admin UI |
| Re-import an updated API spec (`jentic catalog outdated`) | Their call — suggest it, never run it silently |

## Going deeper

- [First brokered call](../guides/first-call.md) — worked end-to-end example
- [How credential resolution works](../guides/credentials-and-toolkits.md) —
  how a stored credential maps onto APIs
- [Overlays](../guides/overlays.md) — fixing an imported spec without editing it
- A running instance serves its own agent map at `/llms.txt` and interactive
  references at `/app/docs` — prefer those for anything runtime-specific.
