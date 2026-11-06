# Jentic CLI

The Jentic CLI ships as **two Go binaries** built from one module:

- **`jenticctl`** — the **installer / lifecycle** CLI. It **installs and operates
  Jentic One locally**: stand up a deployment (source venv or Docker) and
  manage the running app (health, start/stop, logs, updates, teardown). Its
  state lives under `~/.jentic`.
- **`jentic`** — the **agent** CLI. It manages **agent identities**,
  **discovers and imports APIs** from the public catalog, inspects operations,
  and executes against them. Its state lives in the [XDG layout](#xdg-layout).
  It is self-contained: talking to a remote instance needs no `jenticctl` and
  no local install.

## Commands

Run `jenticctl` or `jentic` (no args) for the grouped command list, or
`<binary> <command> --help` for any command. Every command, flag, and example
is in the generated **[CLI reference](../docs/reference/cli.md)** (also served
in the app at `/app/docs`).

| Binary | Area | Commands | What you get |
| ------ | ---- | -------- | ------------ |
| `jenticctl` | **Setup & lifecycle** | `install` · `wizard` · `setup` · `doctor` · `status` · `start` · `stop` · `logs` · `update` · `reset-password` · `uninstall` | Stand up Jentic One locally (source venv or Docker) through an interactive wizard, then manage the running app: health checks, start/stop, log tailing, updates, password reset, and teardown. |
| `jentic` | **Identity & access** | `register` · `setup` · `logout` · `whoami` · `context` · `env` · `identity` · `migrate` | Each identity is an agent keypair scoped to an environment; a **context** binds environment + identity + mode and is what commands act through. Register an agent (Ed25519 + RFC 7523), switch/inspect contexts, and `whoami` to see your identity/permissions/bindings as the control plane does. |
| `jentic` | **APIs** | `catalog` · `apis` · `endpoints` · `credentials` · `connect` · `access` | Browse, search, and import APIs from the public catalog, then manage the ones in your local registry. `endpoints` prints the platform's endpoint + permission reference; `credentials` lists the credentials the control plane holds; `connect <vendor>` starts a connect session a human approves; `access` shows and changes what your credential bindings let you do. |
| `jentic` | **Find and run operations** | `search` · `inspect` · `execute` · `executions` · `history` · `events` · `api` | The agent loop: find imported operations, inspect their contract, and call them through the broker. `executions` follows held calls awaiting human approval; `history export` audits a trace; `events watch` streams live events; `api` is a `gh api`-style authenticated passthrough to any control-plane route. |
| `jentic` | **Local agent client** | `skill` · `reset` · `doctor` · `mcp` | `skill` installs the "how to use Jentic" skill into agent runtimes (Claude Code, Cursor, Codex, …); `reset` wipes local state (and removes any agent account an older release left behind); `doctor` is the agent-side read-only self-check; `mcp` serves Jentic tools to a local MCP client. Flow + examples: [`docs/guides/connecting-agents.md`](../docs/guides/connecting-agents.md). |
| `jentic` | **Administration** | `admin` · `theme` | `admin config providers` manages the platform's credential-provider configuration; `theme` sets the persisted color theme. |

## Install

```bash
brew install --cask jentic/tap/jentic   # macOS / Linux — both binaries
winget install Jentic.Jentic            # Windows — jentic only (or Scoop)
curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh | sh
```

The one-liner installs both binaries (verified download, or a from-source
build when no release asset matches) and flows straight into the
`jenticctl install` wizard. Manual download, signature verification, the
installer's environment knobs, and air-gapped installs are in
**[Installing the CLIs](../docs/installation/cli.md)**. `jenticctl` is
not supported on native Windows — use WSL
([platform support](../docs/installation/platform-support.md)).

## Quick start

**Local install** — stand up Jentic One on this machine, then connect to it:

```bash
curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh | sh
jentic register        # defaults to http://127.0.0.1:8000 and seeds the local broker
```

**Remote server** — connect to an instance someone else runs (ask your
operator for both URLs):

```bash
jentic register --url https://jentic.example.com --broker-url https://broker.jentic.example.com
```

Setting up a local **coding agent**? Run `jentic setup` instead of
`jentic register` — it registers the agent *plus* installs the agent skills and
MCP entry ([connect an agent](../docs/guides/connecting-agents.md)).

`register` creates the environment, identity, and context, registers the key,
prints the console approval link, waits for an operator to approve, and mints a
token. Re-running is idempotent (`--force` re-registers from scratch; exit
code 3 means "still pending — approve, then re-run"). Then:

```bash
jentic doctor             # identity + reachability + clock-skew report
jentic catalog            # browse APIs
jentic whoami             # a fresh agent is bound to no APIs
jentic connect <vendor>   # start connecting a credential — a human approves the printed URL
jentic execute GET:https://api.example.com/v1/pets
```

The worked end-to-end example is [First brokered call](../docs/guides/first-call.md).

## Local vs remote installs

The control plane (`--url`) and the broker (`--broker-url`, the data plane
`jentic execute` goes through) are separate; the CLI never derives one from the
other.

- **Local** (the stack `jenticctl install` stands up): bare `jentic register`
  gets both right. If you pass `--url` yourself, two rules apply:
  1. **Use `127.0.0.1`, not `localhost`.** The token-exchange assertion's
     audience is compared byte-for-byte against the backend's
     `auth.canonical_base_url` (`http://127.0.0.1:8000` locally); `localhost`
     signs the wrong audience and every token exchange fails with
     `invalid_grant`, even after approval.
  2. **The local broker is plain HTTP on port 8100.** `jentic execute`
     defaults to `https://127.0.0.1:8100`, which against a local broker fails
     with `server gave HTTP response to HTTPS client`. `register` seeds
     `broker_url=http://127.0.0.1:8100` for a loopback URL.
- **Remote**: the broker usually lives on another host, so `register` does
  **not** guess it — pass `--broker-url`. Without one, `jentic execute`
  refuses with `RESOLVE_FAILED` rather than dialing the local default.

An environment missing its broker URL? Re-run `jentic register --broker-url <URL>`,
or set it without re-registering:

```bash
jentic env add local --url http://127.0.0.1:8000 --broker-url http://127.0.0.1:8100 --force
```

or override per call: `jentic execute <op> --broker-scheme http --broker-host 127.0.0.1:8100`.

**File-less mode (orchestrators).** An orchestrator that injects credentials can
skip the config file entirely — nothing is read from or written to disk:

```bash
export JENTIC_BASE_URL=https://jentic.example.com             # control plane
export JENTIC_BEARER_TOKEN=<token minted for this agent>     # never persisted
export JENTIC_BROKER_URL=https://broker.jentic.example.com   # required for execute on a remote install
```

## Environments, identities, and contexts

An **environment** is a Jentic install (control-plane URL, optional broker
URL); an **identity** is an actor (this agent), with an Ed25519 keypair scoped
per environment or a `jak_*` API key; a **context** binds one environment +
one identity + an interaction mode. The active context is what every command
acts through (`--context` > `$JENTIC_CONTEXT` > the persisted active context).
To work against several installs:

```bash
jentic env add staging --url https://staging.jentic.example.com --broker-url https://broker.staging.jentic.example.com
jentic context create staging --env staging --identity crawler
jentic context use staging
jentic register          # register the identity's key with the new env
jentic context view      # what am I pointing at?
```

## Local install (`jenticctl install`)

`jenticctl install` is an interactive wizard that generates
`~/.jentic/jentic-one.yaml` (`--out` writes elsewhere) and stands up the app as
a local virtualenv (**Run locally**) or a docker-compose stack (**Run in
Docker**, which pulls the published, signed image by default). For unattended
installs pass `--defaults` (Docker + Postgres, loopback) or `--answers <file>`
(a YAML file overlaid on those defaults); both are held to the wizard's
validators. There is no default admin credential: `jenticctl setup` (or
`jenticctl wizard`) creates the first administrator account.

```
~/.jentic/
├── jentic-one.yaml      # generated app config
├── docker-compose.yaml  # generated stack (Run in Docker only)
├── data/                # local databases (SQLite files)
└── logs/                # app.log (console) and the optional JSON-lines sink
```

`jenticctl logs` tails `~/.jentic/logs/app.log` (`-f` follow, `-n` lines,
`--json` for the structured sink). The generated files embed secrets in plain
text (`0600`) — fine for a local stack, never for a deployed one (use the
[Helm values](../deploy/helm/values/) path). Secrets are generated on first
install and **reused** on reinstall — from `jentic-one.yaml` or its
`jentic-one-old.yaml` backup — so preserved data stays decryptable; don't
delete the backup if you plan to reinstall over existing data, and pass
`--fresh-secrets` only to rotate deliberately. What each install path does step
by step is in [Installing the CLIs](../docs/installation/cli.md#what-jenticctl-install-does).

## XDG layout

The `jentic` agent CLI stores its state per the XDG Base Directory spec:

```
~/.config/jentic/config.yaml         # environments, identities, contexts, active_context, theme
~/.config/jentic/agent-account.yaml  # record of an agent account an earlier release created (read by `jentic reset`)
~/.local/state/jentic/               # per-identity key material and cached tokens (0600)
~/.cache/jentic/                     # disposable caches
```

A local environment in `config.yaml` looks like:

```yaml
environments:
  local:
    base_url: http://127.0.0.1:8000    # control plane (use 127.0.0.1, not localhost)
    broker_url: http://127.0.0.1:8100  # data plane; http, seeded by register for loopback
```

Edit it with `jentic context` / `env` / `identity` rather than by hand.
`XDG_CONFIG_HOME` and `XDG_STATE_HOME` relocate the config and state
directories (the layout is enforced on every OS, including Windows); the cache
follows the platform's native cache dir.

## Uninstall

- `jenticctl uninstall` wipes `~/.jentic` (venv, source, data, logs) but keeps
  each config file as a `-old` backup; `--dry-run` shows what would go, `--yes`
  skips the prompt. A Docker install's database volume is preserved by
  default so a reinstall reattaches it (`--purge` deletes it; interactively,
  uninstall asks).
- Agent skills (`~/.claude/skills/jentic/`, project `./AGENTS.md` blocks, …)
  are listed, not removed, unless you pass `--remove-skills` (user scope; implied by `--purge`) or
  `--remove-project-skills` (the current directory); edited skills are always
  kept. `jentic skill remove --all` cleans them up at any time.
  - A `SKILL.md` at a Jentic skill path with no Jentic provenance is labelled
    `(not written by Jentic)` and never removed.
  - A symlinked `AGENTS.md` is edited at the file it points to. At project
    scope a link that resolves outside the current directory or into its git
    metadata is refused and left for you to edit by hand.
  - Skills are removed only after the `~/.jentic` teardown succeeds; if one
    cannot be removed, uninstall still finishes, reports it, and exits
    non-zero.
- The agent identity (the XDG directories) survives `uninstall`; `jentic reset`
  removes it. A local agent account an older release created (Unix user, ACLs,
  sudoers entry) is also left by `uninstall` and removed by `jentic reset`.

## Legacy `~/.jentic` profiles

A machine with a profile store from an older `jentic` CLI must run
`jentic migrate` once: it copies the profiles into XDG contexts and leaves the
legacy tree in place. See `jentic migrate --help`.

## Contributing

Building from a checkout, the MCP Inspector debug loop, the package map, and the
install wizard's internals are in [Developing the CLI](../docs/development/cli.md).
