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
jentic access whoami                   # 1. what your bindings already SERVE
jentic catalog search "<capability>"   # 2. find an importable API (public catalog)
jentic catalog import <vendor/name>    #    import it into the local registry
jentic search "<what you want to do>"  # 3. find the operation — each hit gives its METHOD and URL
jentic inspect 'GET https://api.example.com/v1/things/{id}'   # 4. params, schemas, auth
jentic execute GET:https://api.example.com/v1/things/{id} --path id=abc   # 5. call it through the broker
```

Key behaviours (details and full flag syntax in the skill):

- **Decide access from `whoami`, don't probe with `execute`.** If nothing you
  are bound to serves the API, file **one composite**
  `jentic access request --provision <vendor/name> --auth … --rules-json …
  --reason … --wait` covering the whole job. A human approves; you never
  approve yourself, and you never see the credential secret.
- **Import before search.** A fresh registry is empty; `search` returning
  `{"data": []}` means nothing is imported, not that you lack access.
- **Denials teach you.** A denied `execute` exits 2 and prints an
  `agent_directive` on stderr with the exact recovery
  (`no_toolkit_binding`, `credential_not_provisioned`, …). Follow its
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
2. **A freshly imported API has no toolkit.** Your **first** access request
   for it must be `--provision <vendor/name>` (which describes the whole path:
   toolkit, credential, rules, binding). A bare `--toolkit <vendor/name>`
   request will be denied — nothing serves the API yet.
3. **Withdraw mistakes before re-filing.** A new access request for the same
   target can be merged into your still-pending earlier request — so a
   `--provision` filed after a doomed `--toolkit` can inherit its denial. If
   you filed a bad request, run `jentic access withdraw <request_id>` first,
   then file the correct one fresh.
4. **One composite request per job**, always with `--reason` — never thrash
   with per-operation or duplicate requests.

## How to do an action (worked example)

Task: *"get the current Bitcoin price"* on a fresh instance — nothing
imported, no access yet.

```bash
# 1. What can I already call? (nothing yet, on a fresh install)
jentic access whoami

# 2. Find and import the API from the public catalog
jentic catalog search "crypto prices"
jentic catalog import coincap-io/coincap-io

# 3. First access request for a just-imported API: --provision, never --toolkit
jentic access request --provision coincap-io/coincap-io \
  --auth api_key \
  --rules-json '[{"effect":"allow","methods":["GET"],"path":".*"}]' \
  --reason "read current crypto prices for the user" \
  --wait
# → a human fulfils and approves this in the dashboard; --wait blocks until they do.
#   Filed something wrong first? `jentic access withdraw <request_id>`, then re-file.

# 4. Find the operation — the hit gives you its METHOD and URL
jentic search "get current asset price"

# 5. Inspect, then execute with that exact METHOD + URL
jentic inspect 'GET https://rest.coincap.io/v3/assets/{id}'
jentic execute GET:https://rest.coincap.io/v3/assets/{id} --path id=bitcoin
```

If step 5 is denied (exit 2), the `agent_directive` on stderr names the exact
recovery — follow its `suggested_command` instead of retrying the same call.

## Machine-friendly behaviour

- Add `--json` for machine-readable output (`search`, `execute`, `inspect`,
  `apis`, `access`, `doctor`); non-TTY output is JSON automatically for most
  commands.
- Exit codes are a contract: **0** ok, **1** transport/unexpected, **2**
  "cannot succeed as asked" (denial, resolve failure, missing context — do not
  blind-retry), **3** timed out still pending (retry later),
  **4** partially approved.
- Export `JENTIC_SESSION_ID=<id>` so operators can correlate your calls in the
  audit log; pass `--idempotency-key <uuid>` when retrying mutating calls.
- `jentic api <METHOD> <path>` is an authenticated passthrough to any
  control-plane endpoint (`jentic api ops` lists them); full route/scope
  reference: [endpoints.md](../reference/endpoints.md).

## What stays human

| Action | Where the human does it |
| ------ | ----------------------- |
| Approve a new agent | `/app/agents` in the console |
| Approve/fulfil access requests, enter credential secrets | `/app` dashboard (the `approve_url` / `provisioning_url` you hand them) |
| Create/manage users | `/app` admin UI |
| Re-import an updated API spec (`jentic catalog outdated`) | Their call — suggest it, never run it silently |

## Going deeper

- [First brokered call](../guides/first-call.md) — worked end-to-end example
- [Credentials and toolkits](../guides/credentials-and-toolkits.md) — how a
  stored credential maps onto APIs
- [Overlays](../guides/overlays.md) — fixing an imported spec without editing it
- A running instance serves its own agent map at `/llms.txt` and interactive
  references at `/app/docs` — prefer those for anything runtime-specific.
