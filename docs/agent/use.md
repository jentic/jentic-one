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
jentic whoami                          # 1. what your bindings already SERVE
jentic catalog search "<capability>"   # 2. find an importable API (public catalog)
jentic catalog import <vendor/name>    #    import it into the local registry
jentic search "<what you want to do>"  # 3. find the operation — each hit gives its METHOD and URL
jentic inspect GET:https://api.example.com/v1/things/{id}     # 4. params, schemas, auth
jentic execute GET:https://api.example.com/v1/things/{id} --path id=abc   # 5. call it through the broker
```

Key behaviours (details and full flag syntax in the skill):

- **Decide access from your bindings (`jentic whoami`), don't probe with
  `execute`.** If nothing you are bound to serves the API and its vendor is
  in the deployment's connect registry, start the connection yourself with
  `jentic connect <vendor>` and relay the printed `approval_url` to your
  operator — a human approves it in the browser; you never approve. For
  anything else, report the gap to your operator in **one summary**
  covering the whole job (the API, the auth type, the permission rules you
  read from the spec, and why); they connect the credential and bind you in
  the dashboard. You never grant yourself access, and you never see the
  credential secret.
- **Import before search.** A fresh registry is empty; `search` returning
  `{"data": []}` means nothing is imported, not that you lack access.
- **Denials teach you.** A denied `execute` exits 2 and prints an
  `agent_directive` on stderr with the exact recovery
  (`no_credential_binding`, `credential_not_provisioned`, …). Follow its
  instruction; never re-send the same call.
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
2. **A freshly imported API has no credential.** Your operator must first
   connect/provision a credential for it in the dashboard (auth type +
   permission rules), then bind you to it — importing alone grants nothing.
3. **One report per job**, always with the reason — never thrash your
   operator with per-operation or duplicate asks.

## How to do an action (worked example)

Task: *"get the current Bitcoin price"* on a fresh instance — nothing
imported, no access yet.

```bash
# 1. What can I already call? (nothing yet, on a fresh install)
jentic whoami

# 2. Find and import the API from the public catalog
jentic catalog search "crypto prices"
jentic catalog import coincap-io/coincap-io

# 3. Report the access gap to your operator, once, with everything they need:
#    "I need coincap-io/coincap-io. Auth type: api_key. Rules:
#     [{"effect":"allow","methods":["GET"],"path":".*"}].
#     Reason: read current crypto prices for the user."
#    (For a vendor in the connect registry you would instead run
#     `jentic connect <vendor>` and relay the printed approval_url.)
# → a human connects the credential and binds you in the dashboard; re-check
#   your bindings (jentic whoami) once they confirm.

# 4. Find the operation — the hit gives you its METHOD and URL
jentic search "get current asset price"

# 5. Inspect, then execute with that exact METHOD + URL
jentic inspect GET:https://rest.coincap.io/v3/assets/{id}
jentic execute GET:https://rest.coincap.io/v3/assets/{id} --path id=bitcoin
```

If step 5 is denied (exit 2), the `agent_directive` on stderr names the exact
recovery — follow its instruction instead of retrying the same call.

## Machine-friendly behaviour

- Add `--json` for machine-readable output. It exists on the **leaf**
  commands (`search`, `execute`, `inspect`, `doctor`, `apis list`, …) — the
  bare group commands (`jentic apis`) reject it. Do not rely on "non-TTY
  output is JSON
  automatically": `jentic register` persists `mode: human` in the context it
  creates, and an explicit mode short-circuits the TTY check, so piped
  output is prose on most installs. For a fully machine posture set
  `JENTIC_MODE=agent` — note it also deadlines most commands at 60 s
  (pass `--timeout` on long waits such as `register`).
- Exit codes are a coarse contract: **0** ok, **1** transport/unexpected,
  **2** "cannot succeed as asked" (denial, resolve failure, missing context —
  do not blind-retry), **3** timed out still pending (retry later),
  **4** partially approved. Two caveats: `execute` exits **0 for any
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
| Connect/provision credentials, bind agents, enter credential secrets | `/app` console (dashboard) — relay your access ask to the operator in prose; they act on it there |
| Create/manage users | `/app` admin UI |
| Re-import an updated API spec (`jentic catalog outdated`) | Their call — suggest it, never run it silently |

## Going deeper

- [First brokered call](../guides/first-call.md) — worked end-to-end example
- [How credential resolution works](../guides/credentials-and-toolkits.md) —
  how a stored credential maps onto APIs
- [Overlays](../guides/overlays.md) — fixing an imported spec without editing it
- A running instance serves its own agent map at `/llms.txt` and interactive
  references at `/app/docs` — prefer those for anything runtime-specific.
