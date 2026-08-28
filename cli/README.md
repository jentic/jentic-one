# Jentic CLI

The Jentic CLI ships as **two Go binaries** built from one module, sharing the
same `internal/` packages. Their state is split: `jenticctl` (the installer)
keeps its install/lifecycle state under `~/.jentic`, while the `jentic` agent
CLI stores its config/state in the **XDG layout** (`~/.config/jentic`,
`~/.local/state/jentic` — see the XDG section below).

- **`jenticctl`** — the **installer / lifecycle** CLI. It **installs and operates
  jentic-one locally**: stand up a deployment (source venv **or** Docker) and
  manage the running app (health, start/stop, logs, updates, teardown).
- **`jentic`** — the **agent** CLI. It manages **agent identities**,
  **discovers and imports APIs** from the public catalog, inspects operations,
  and executes against them.

## What it does

Run `jenticctl` or `jentic` (no args) for the grouped command list, or
`<binary> <command> --help` for any command.

| Binary | Area | Commands | What you get |
| ------ | ---- | -------- | ------------ |
| `jenticctl` | **Setup & lifecycle** | `install` · `wizard` · `setup` · `doctor` · `status` · `start` · `stop` · `logs` · `update` · `reset-password` · `uninstall` | Stand up jentic-one locally (source venv **or** Docker) through an interactive wizard, then manage the running app: health checks, start/stop, log tailing, updates, password reset, and teardown. |
| `jentic` | **Identity & access** | `register` · `setup` · `logout` · `context` · `env` · `identity` · `migrate` | Each identity is an agent keypair scoped to an environment; a **context** binds environment + identity + mode and is what commands act through. Register an agent (Ed25519 + RFC 7523), switch/inspect contexts, and `migrate` a legacy `~/.jentic` profile store into the XDG layout. (The old `profile` command was removed — use `context`; run `jentic migrate` to bring a legacy `~/.jentic` profile store across.) |
| `jentic` | **APIs** | `catalog` · `apis` · `endpoints` · `credentials` | Browse, search, and import APIs from the public catalog, then manage the ones in your local registry — revisions, operations, promote/archive, spec download — with interactive TUI browsers. `endpoints` prints the platform's own endpoint + scope reference; `credentials` lists the credentials the control plane holds. |
| `jentic` | **Find and run operations** | `search` · `inspect` · `execute` · `access` · `history` · `events` · `api` | The agent loop: find imported operations, inspect their method/params/schemas, and call them through the broker. `access` files/tracks access requests (`whoami` · `request` · `list` · `status` · `withdraw` · `refresh`); `history export` audits a trace; `events watch` streams live events; `api` is a `gh api`-style authenticated passthrough to any control-plane route (self-describing via `api ops` / `api describe`). |
| `jentic` | **Local agent client** | `skill` · `run` · `reset` · `doctor` | `skill` installs the "how to use Jentic" skill into agent runtimes (Claude Code, Cursor, Codex, …); `run` launches a coding agent in an isolated local account; `reset` wipes local state; `doctor` is the agent-side read-only self-check. Flow + examples: [`docs/local-agent.md`](../docs/local-agent.md). |
| `jentic` | **Administration** | `admin` · `theme` | Manage OAuth provider config and the persisted color theme. |

The table mirrors the CLI's own command groups (what `jentic` with no args
prints). The **complete command + flag reference** is generated from these
cobra definitions (`make cli-reference`) and rendered in the platform docs —
open `/app/docs` on your deployment (Reference → CLI). This README covers
building, onboarding, and operating; it does not duplicate per-flag docs.

**New here?** Two paths, depending on where the Jentic server lives:

**Local install** (stand up jentic-one on your own machine, then connect to it):

```bash
# 1. Install the CLI + stand up the local stack. The one-liner installs the
#    binaries and flows straight into the `jenticctl install` wizard, which
#    brings the server up on http://127.0.0.1:8000:
curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh \
  | env JENTIC_INSTALL_BINARIES=both sh

# 2. Connect this machine — that's it. `register` defaults to the local install
#    (http://127.0.0.1:8000) and seeds the local broker for you, so just confirm:
jentic register
```

(Setting up a local **coding agent**? Run `jentic setup` instead of `jentic
register` — it does the registration above, installs the agent skills, and
offers to configure an isolated account.)

**Remote server** (connect to a Jentic server someone else is running): you
don't need `jenticctl` or a local install at all — the `jentic` binary is
self-contained (it links none of the installer/lifecycle code; enforced by an
arch test) and one command onboards it:

```bash
jentic register --url https://jentic.example.com --broker-url https://broker.jentic.example.com
```

Ask your operator for both URLs — the control plane and the broker often live on
different hosts, and the CLI never derives one from the other.

## Install

Fastest first. `jentic` (agent) and `jenticctl` (installer/server) are **separate
downloads** — most users only need `jentic`, unless you're standing up the stack
locally.

### 1. Homebrew (macOS / Linux) — both binaries

```bash
brew install jentic/tap/jentic
```

The cask installs both `jentic` and `jenticctl`.

### 2. One-line download (verified binary, no compiler)

```bash
# jentic only (default) — for talking to a remote jentic server:
curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh \
  | env JENTIC_INSTALL_METHOD=binary sh

# both binaries (also install jenticctl to run the stack locally):
curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh \
  | env JENTIC_INSTALL_METHOD=binary JENTIC_INSTALL_BINARIES=both sh
```

This downloads the released archive for your OS/arch from the GitHub Releases
page, **verifies its `sha256`** against `checksums.txt` (fail-closed — a
mismatch aborts and installs nothing), and — when `cosign` is on your `PATH` —
**verifies the release signature** too (absent `cosign` is a loud warning, never
a silent skip). No Go toolchain, no clone, no compile.

The default method is `auto`: it prefers this verified download when a matching
release asset exists and **falls back to building from source** otherwise (forks,
dev refs, or a ref with no published assets).

Environment knobs:

- `JENTIC_INSTALL_METHOD` — `auto` (default) · `binary` (force download; errors
  if no asset) · `source` (force the from-source build).
- `JENTIC_INSTALL_BINARIES` — `jentic` (default for downloads) · `both`.
- `JENTIC_NO_INSTALL=1` — install the binaries only; **skip** the
  `jenticctl install` stack wizard (useful in CI or when you only want the CLI).
- `GITHUB_TOKEN=ghp_xxx` — download/clone from a **private** fork (token needs
  `repo` read scope); also used to build the server image from source.
- `JENTIC_REF=v0.31.0` — pin the release tag to install (download mode) or the
  ref (tag / branch / commit) to build (source mode).

On a machine with **no interactive terminal** (piped `curl … | sh` in CI), the
installer installs the binaries and then prints the exact non-interactive
follow-up (`jenticctl install --defaults`) instead of blocking on a wizard.

### 3. Manual download + verify

Grab the archive for your platform from the
[Releases page](https://github.com/jentic/jentic-one/releases), then verify and
install it yourself:

```bash
VER=0.31.0; OS=linux; ARCH=amd64            # adjust for your platform (darwin/arm64, …)
BASE="https://github.com/jentic/jentic-one/releases/download/v${VER}"
curl -fsSLO "${BASE}/jentic_${VER}_${OS}_${ARCH}.tar.gz"
curl -fsSLO "${BASE}/checksums.txt"
curl -fsSLO "${BASE}/checksums.txt.sig"
curl -fsSLO "${BASE}/checksums.txt.pem"

# Verify the signature over the checksum file (keyless / Fulcio identity):
cosign verify-blob \
  --certificate checksums.txt.pem \
  --signature checksums.txt.sig \
  --certificate-identity-regexp 'https://github.com/jentic/jentic-one/.*' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  checksums.txt

# Verify the archive's checksum, then install:
sha256sum --check --ignore-missing checksums.txt   # macOS: shasum -a 256 -c …
tar xzf "jentic_${VER}_${OS}_${ARCH}.tar.gz"
sudo install jentic /usr/local/bin/
```

`jenticctl` ships as its own archive (`jenticctl_${VER}_${OS}_${ARCH}.tar.gz`) —
download and install it the same way if you need the local stack.

**Windows.** `jentic` ships a Windows build as
`jentic_${VER}_windows_amd64.zip` (and `_arm64`). Download it from the
[Releases page](https://github.com/jentic/jentic-one/releases), unzip it, put
`jentic.exe` on your `PATH`, and run `jentic doctor`. Note: `jenticctl` is **not**
shipped natively for Windows, and `jentic run` (the local-agent sandbox) is
**unsupported on native Windows** — use **WSL** for agent-sandbox and local-stack
workflows.

### 4. From source (contributors / advanced)

Build from a checkout with `make build` (below), or force the installer's
from-source path with `JENTIC_INSTALL_METHOD=source`.

## Build from a checkout (developers)

```bash
cd cli
make build   # builds both binaries (jenticctl + jentic), stamping version/commit/date
make help    # list all targets (test, lint, check, cov, ...)
```

`make build-ctl` / `make build-api` build a single binary; `make check` runs
lint + vet + race tests.

The examples below assume both binaries are on your `PATH`:

```bash
make build && sudo install -m 0755 jenticctl jentic /usr/local/bin/
# or, without installing, run them straight from the build directory:
export PATH="$PWD:$PATH"
```

## Supported platforms

The `jentic` and `jenticctl` binaries are pure-Go and build/run on **Linux**,
**macOS** (Intel + Apple Silicon), and **Windows** (x86-64). Each release is
smoke-tested end-to-end on all three (`ubuntu-24.04`, `macos-latest`,
`windows-latest`) via the CI `e2e-install` matrix, which runs the built binaries'
`--version` / `jentic doctor` / `jentic access whoami --json` / `jenticctl doctor`
contract on every OS.

| Platform | `install.sh` | `jenticctl install` (server) | `jentic` agent CLI | `jentic run` isolation |
| --- | --- | --- | --- | --- |
| **Linux** (Ubuntu 24.04+) | ✅ | ✅ Docker + source | ✅ | ✅ (`bwrap` + userns/ACLs) |
| **macOS** (latest) | ✅ | ✅ source; Docker via Colima/Docker Desktop | ✅ | ✅ (Seatbelt `sandbox-exec`) |
| **Windows** 10/11 | ⚠️ **via WSL only** | via WSL | ✅ native | ❌ not supported |

Notes:

- **`install.sh` is bash-only** (macOS + Linux). On **native Windows** it exits
  with a "run this inside WSL" message — there is no PowerShell installer. Windows
  users run the prebuilt binaries directly (or build with `make build`); the agent
  surface (`jentic ...`) works natively.
- **`jentic run` (local-agent isolation) is Unix-only.** It uses Seatbelt on
  macOS and `bwrap`+ACLs on Linux; there is no Windows sandbox analogue, so the
  command refuses cleanly on Windows rather than running unconfined.

## Usage

```bash
# Connect this machine to a Jentic install, browse the catalog, execute an operation.
jentic register                                       # local install (defaults to 127.0.0.1, seeds the broker)
# …or, for a remote server:
jentic register --url https://jentic.example.com --broker-url https://broker.jentic.example.com
jentic catalog
jentic access whoami                                  # a fresh agent is bound to no APIs
jentic access request --toolkit <vendor/name> --wait  # ask a human to grant access
jentic execute <operation>
```

> **Remote install:** a remote control plane's broker usually lives on a
> different host, so `jentic register` does **not** guess a `broker_url` for it
> — pass `--broker-url` as above (ask your operator for it). Without a broker,
> `jentic execute` fail-closes with `RESOLVE_FAILED` rather than dialing your
> local default. Forgot it? Re-running register fills it in on the existing
> environment:
>
> ```bash
> jentic register --url https://jentic.example.com --broker-url https://broker.jentic.example.com
> ```
>
> (`jentic env add <env> --url <URL> --broker-url <URL> --force` does the same
> without re-registering.) See [Local setup](#local-setup) for the loopback
> case, where `register` seeds the broker automatically.

## Local setup

Running against a **local** install (the stack `jenticctl install` stands up on
`127.0.0.1`) has two rules that trip people up. Bare `jentic register` handles
both for you (it defaults to `http://127.0.0.1:8000` and seeds the broker); the
rules below matter only if you pass `--url` explicitly.

1. **Use `127.0.0.1`, not `localhost`.** The token-exchange assertion's audience
   is compared byte-for-byte against the backend's `auth.canonical_base_url`,
   which is `http://127.0.0.1:8000` for a local install. Registering with
   `--url http://localhost:8000` signs the wrong audience and every token
   exchange fails with `invalid_grant` — even after you approve the agent. (The
   prefilled default and bare `jentic register` already use `127.0.0.1`, so this
   only bites if you type `localhost` yourself.)

2. **The local broker is plain HTTP on port 8100.** `jentic execute` targets
   the broker at `https://127.0.0.1:8100` by default; against a local http
   broker that yields `server gave HTTP response to HTTPS client`. The
   environment's `broker_url` overrides that default — and `jentic register`
   seeds it automatically when the control-plane URL is loopback.

```bash
# One command connects this machine. `register` defaults to the local install
# URL (http://127.0.0.1:8000) and seeds broker_url for you — just press Enter to
# confirm the prefilled URL:
jentic register

# (Non-interactive / scripts: pass it explicitly.)
jentic register --url http://127.0.0.1:8000

# Approve the agent in the console, then:
jentic doctor                 # identity + reachability + clock-skew report
jentic catalog                # browse APIs
jentic access whoami          # a fresh agent starts bound to no APIs
jentic access request --toolkit <vendor/name> --wait  # ask a human to grant access
jentic execute listPets       # routed through http://127.0.0.1:8100 automatically
```

If an environment already exists without a broker URL (e.g. created before this
behavior, or pointing at a remote broker), set it explicitly:

```bash
jentic env add local --url http://127.0.0.1:8000 --broker-url http://127.0.0.1:8100 --force
```

or override the broker per call: `jentic execute <op> --broker-scheme http --broker-host 127.0.0.1:8100`.

## Onboarding (`jenticctl install`)

`jenticctl install` is an interactive wizard (colored, keyboard-selectable menus
with sensible defaults) that helps you stand up the **jentic-one app**. It asks
how you want to deploy and configure the platform, **generates** a
`jentic-one.yaml`, and then performs the install for you — either a local
virtualenv (the **Run locally** path) or a containerized docker-compose stack
(the **Run in Docker** path).

**Unattended (CI / scripted):** the wizard is a TTY-only TUI, so for
non-interactive installs pass `--defaults` (take the wizard's defaults — Docker +
Postgres, loopback — as-is) or `--answers <file>` (overlay a YAML answers file on
top of those defaults; unlisted keys keep the default). Both skip the wizard and
are held to the exact same field validators, so a headless install can't produce
a config the wizard would have rejected. Example answers file:

```yaml
# answers.yaml — only the fields you want to override; the rest keep defaults.
bind_host: 127.0.0.1
app_port: "18000"
broker_port: "18100"
database: postgres
```

```bash
jenticctl install --defaults --no-wizard          # zero-prompt default install
jenticctl install --answers answers.yaml --no-wizard
```

Everything `jenticctl` owns (plus the legacy `jentic` store read only by
`migrate`) is rooted under `~/.jentic`:

```
~/.jentic/
├── jentic-one.yaml      # generated app config (this wizard)
├── docker-compose.yaml  # generated stack (Run in Docker path only)
├── config.yaml          # legacy CLI settings (base_url, default_profile) — read only by `migrate`
├── data/                # local databases (SQLite files)
└── logs/                # log output
```

```bash
# Run the wizard (writes ~/.jentic/jentic-one.yaml by default)
jenticctl install

# Write the config somewhere else
jenticctl install --out ./config/my-install.yaml
```

The wizard collects, with defaults:

- **Deployment** — run from source (uv) or in Docker (docker compose); combined
  vs parts topology.
- **Database** — SQLite (no extra services) or PostgreSQL; connection
  details for Postgres, or the data directory for SQLite.
- **Surfaces** — which `apps` to enable (registry / admin / control / auth /
  broker).
- **Server** — bind host + port (drives `auth.canonical_base_url`).
- **Runtime** — debug toggle + log level.
- **Observability** — metrics + tracing exporters.

For the **Run locally** path the wizard performs a real install once the config
is written:

0. **Preflight** — checks the required tools are on `PATH` before doing any work
   (`uv` always; `git` when the source must be cloned). It prints a checklist
   with versions and aborts with install hints if anything required is missing.
   Requirements are defined per path in `requirementsFor`
   ([`internal/install/preflight.go`](internal/install/preflight.go)) so it's
   easy to add more.
1. **Build** a virtualenv at `~/.jentic/venv` and install `jentic-one` (editable)
   into it. The source is repo-aware:
   - **Inside a jentic-one checkout** (detected by walking up for `pyproject.toml`
     + `src/jentic_one`): installs from your local source.
   - **Outside the repo**: clones
     [`github.com/jentic/jentic-one`](https://github.com/jentic/jentic-one) into
     `~/.jentic/src` first, then installs from there.
2. **Migrate** — runs `jentic_one.migrations.run` with the venv interpreter
   against your generated config, creating the database schema (the SQLite files
   live under `~/.jentic/data/`). For Postgres, if the database isn't reachable
   yet the wizard warns and leaves the migrate command in the next steps instead
   of failing the whole install.
3. **Start the app (background)** — once migrations are applied, the wizard
   launches `~/.jentic/venv/bin/python -m jentic_one` detached, capturing its
   console output to `~/.jentic/logs/app.log` with the PID in `~/.jentic/app.pid`.
   It watches the first couple of seconds for an early crash; if the app stays up
   the summary shows the PID plus how to view logs (`jenticctl logs -f`) and stop it
   (`jenticctl stop`). A failure to start is non-fatal — the install is complete and
   the manual start command is left in the next steps. Pass `--no-start` to skip
   this.

### Logs (`jenticctl logs`)

`jenticctl logs` tails the app's captured console output at `~/.jentic/logs/app.log`.

```bash
jenticctl logs              # last 200 lines
jenticctl logs -f           # follow (stream new lines)
jenticctl logs -n 50        # last 50 lines
jenticctl logs --json       # the structured JSON-lines sink (if enabled)
jenticctl logs --path       # print the resolved log file path and exit
```

The **Logging** section of the install wizard controls the app's file sink
(`logging.file_enabled`): when enabled it writes one JSON object per line to
`~/.jentic/logs/<file_name>` (default `app.jsonl`) in addition to stdout.
`jenticctl logs --json` reads that structured file, resolving its exact path from
the generated config.

For the **Run in Docker** path the wizard performs an equivalent install:

0. **Preflight** — checks `docker` is on `PATH` (plus `git` when the source must
   be cloned to build the image).
1. **Build** the combined app image (`jentic-one/app:jentic-cli`) from your local
   checkout, or from a fresh clone of
   [`github.com/jentic/jentic-one`](https://github.com/jentic/jentic-one) into
   `~/.jentic/src`. The shared `python-base` stages are built first.
2. **Write** `~/.jentic/docker-compose.yaml` (app + a managed Postgres
   when you choose Postgres) with your generated config mounted read-only at
   `/etc/jentic/jentic-one.yaml`. The config is rendered with container-aware
   values (bind `0.0.0.0`, Postgres host `db`, SQLite under `/data`, logs under
   `/logs`); `JENTIC__APPS` is set from your selected surfaces. SQLite databases
   live in a Docker **named volume** (`jentic-data`), not a host bind mount —
   Docker Desktop's file sharing doesn't support the locking SQLite needs, which
   otherwise surfaces as `disk I/O error`.
3. **Migrate** in a one-shot container
   (`docker compose run --rm app python -m jentic_one.migrations.run`). For
   Postgres, compose starts and health-waits the db first.
4. **Start the stack** — `docker compose up -d` (unless `--no-start`). Manage it
   afterwards with `jenticctl start` / `jenticctl stop`, which detect the generated
   compose file and drive `docker compose up -d` / `down`.

Use `--skip-build` to only generate config (no image build, compose file,
migrate, or start) and print the next-step commands.

Secrets (the credential-encryption key, admin JWT secret, invite pepper, connect
state secret, and — for a managed Docker Postgres — a random database password)
are generated on first install and written into the config with `0600` perms —
never prompted for. On reinstall they are **reused** from your existing config
by default so previously-encrypted data stays readable; pass `--fresh-secrets`
to deliberately rotate them (see "Reinstalling over existing data" below).
There is **no default admin credential**: after install, `jenticctl setup` (or
the guided wizard) creates the first administrator account with a password you
choose — nothing pre-seeded, nothing to rotate.

> **Local development only.** Both the generated `jentic-one.yaml` and the
> `docker-compose.yaml` embed credentials (including the Postgres password) in
> plain text — standard for a self-contained local stack. For any deployed
> environment, do **not** ship these files: source secrets from Docker secrets,
> Kubernetes secrets, or an external secret manager (the production path is
> configured via Helm values under `deploy/helm/values/`, not this wizard).

The generated file mirrors
[`jentic_one.shared.config.AppConfig`](../src/jentic_one/shared/config.py). Since
it lives under `~/.jentic` (not the cwd), point the app at it with
`JENTIC_CONFIG_FILE=<path>` — the printed next-step commands already do this for
you. SQLite databases default to `~/.jentic/data/`.

### Uninstall (`jenticctl uninstall`)

`jenticctl uninstall` wipes everything under `~/.jentic` (venv, source, data, logs,
profiles) but **preserves your config**: each config file is renamed to a
`-old` backup so you can restore it later. The `~/.jentic` directory itself is
kept.

```bash
jenticctl uninstall          # prompts for confirmation
jenticctl uninstall --yes    # skip the prompt
```

After running, `~/.jentic` contains only the backups:

```
~/.jentic/
├── jentic-one-old.yaml   # was jentic-one.yaml
└── config-old.yaml       # was config.yaml
```

### Reinstalling over existing data

On a Docker install `uninstall` **preserves** the database volume by default
(the "reinstall reattaches your data" contract in `--keep-data`'s help), and
a plain re-`install` never touches it. The next `jenticctl install` reuses
secrets from an existing `jentic-one.yaml` at `--out` (or the
`jentic-one-old.yaml` backup beside it) so the fresh config's encryption key
matches the one that encrypted the credentials, invite pepper, OAuth tokens
etc. in the preserved data:

- Reused: `credentials.encryption` (whole keyset — a hand-rotated
  `active_id: v2` + `v1`/`v2` layout survives verbatim), `admin.auth.jwt_secret`,
  `admin.invite.pepper`, `credentials.connect.state_secret`,
  `auth.id_signing`, `telemetry.instance_id`.
- Not reused: wizard-owned settings (ports, backend, apps list, etc.) — the
  operator may be legitimately changing them.
- **Don't delete `jentic-one-old.yaml` by hand** if you plan to reinstall
  and keep the data — that file holds the encryption key that makes the
  preserved credentials decryptable.
- Pass `--fresh-secrets` to `jenticctl install` for deliberate rotation
  (invalidates existing sessions, invites, and stored ciphertexts).

### Wizard structure

The wizard is a **hub-and-spoke TUI** ([`internal/install/wizard.go`](internal/install/wizard.go),
built on [bubbletea](https://github.com/charmbracelet/bubbletea) +
[huh](https://github.com/charmbracelet/huh)): a deployment page, then a menu of
configuration **sections** with a live detail pane, each drilling into a small
form, ending in **Continue**.

To onboard a new `AppConfig` option:

1. Add a field to `Draft` in
   [`internal/install/draft.go`](internal/install/draft.go) (with a default in
   `NewDraft`).
2. Add it to a `Section` in
   [`internal/install/sections.go`](internal/install/sections.go): a huh field in
   the section's `Groups` (use `WithHideFunc` for conditional fields) and a line
   in its `Summary` for the detail pane. Add a whole new `Section` to the
   `Sections` slice to create a new hub row.
3. Map the field into the generated YAML in
   [`internal/install/render.go`](internal/install/render.go).

No changes to the command wiring are needed — the hub is built from the
`Sections` registry automatically.

## Agent identity & contexts (`jentic register`)

The CLI's identity model has three small pieces, glued by one binding:

- **Environment** — a Jentic install: its control-plane URL (and optionally a
  broker URL). You'll usually have one; add more to talk to dev/staging/prod.
- **Identity** — an actor (this agent). Its credential is an Ed25519 keypair
  scoped **per environment** (or a `jak_*` API key).
- **Context** — binds one environment + one identity (+ an interaction mode).
  The **active context** is what every command acts through — switch context
  and the whole CLI points somewhere else, atomically.

You do **not** assemble that by hand. `jentic register` is the one onboarding
command:

```bash
# Fresh machine → working setup, one command. Creates the environment,
# identity and context, activates them, registers the key, waits for the
# operator to approve, and mints a token. --broker-url points `jentic execute`
# at the remote data plane (loopback installs seed it automatically).
jentic register --url https://jentic.example.com --broker-url https://broker.jentic.example.com

# Name things explicitly (defaults: identity = hostname, env = URL's first label)
jentic register --url https://jentic.example.com --name crawler --env prod

# With a context already active, re-register / resume that identity:
jentic register

# Fill in a missing broker on the active environment while re-registering:
jentic register --broker-url https://broker.jentic.example.com

# Interactive: bare `jentic register` on a fresh machine prompts for the
# install URL (prefilled with the local default — just confirm after a local
# install) + agent name (and the broker URL, for a remote install) and does
# the rest.
```

Approval is a human, out-of-band step: `register` prints the console link
(`{base}/app/agents/{id}`), polls, and continues automatically once an operator
approves. Re-running is idempotent and resumable (`--force` re-registers from
scratch; exit code 3 means "still pending — approve, then re-run").

After onboarding:

```bash
jentic context view      # what am I pointing at?
jentic doctor            # paths / identity / token / reachability self-check
jentic catalog           # you're in business
```

To work against several installs, add more environments and switch contexts:

```bash
jentic env add staging --url https://staging.jentic.example.com --broker-url https://broker.staging.jentic.example.com
jentic context create staging --env staging --identity crawler
jentic context use staging
jentic register          # register the identity's key with the new env
```

State lives in the XDG layout (see below): the config in
`~/.config/jentic/config.yaml`, keys/tokens per (identity, environment) in
`~/.local/state/jentic/` (0600).

### Legacy `~/.jentic` profile stores (migrate to use them)

Machines with an existing `~/.jentic` profile store from a pre-activation CLI
are **read-only** to this release until you run `jentic migrate`, which copies
the profile(s) into the XDG context model (leaving `~/.jentic` intact apart from
a `MIGRATED` marker). It also adopts a legacy local-agent `agent_account:`
record from `~/.jentic/config.yaml` into `~/.config/jentic/agent-account.yaml`
(as does the first `jentic run`/`jentic reset` write, so localagent keeps no
state in `~/.jentic`). Until you migrate, non-exempt commands stop and point you
at `jentic migrate` (see the migrate gate). The V1 `register --profile` /
`--base-url` onboarding flow has been **removed** in the activation release —
there is no in-place V1 register/logout/status; migrate first, then use the V2
`context` / `env` / `identity` commands.

```bash
jentic migrate                       # copy legacy profiles → XDG contexts (named after each profile)
jentic context list                  # see the migrated contexts
jentic context use <name>            # activate one
jentic doctor                        # verify identity + reachability
```

Legacy profiles are stored under `~/.jentic/profiles/<name>/` with `0600` perms:

```
~/.jentic/profiles/<name>/
├── profile.yaml   # base_url, agent_id, agent_name, kid, registration token
├── agent.key      # Ed25519 private key (PEM, 0600)
└── tokens.json    # access/refresh tokens + expiry (0600)
```

## Config file (`~/.jentic/config.yaml`) — legacy / POC only

> **Legacy.** This file belongs to the pre-activation `~/.jentic` layer. The V2
> CLI resolves everything from the XDG store (see "XDG layout" below); in
> particular `jentic execute` reads the broker from the active environment's
> `broker_url`, **not** from `broker.scheme`/`broker.host` here. This section is
> retained only to describe what a migrated legacy file may contain.

Pre-activation settings were persisted in `~/.jentic/config.yaml`, organized
into sections:

```yaml
# ~/.jentic/config.yaml  (legacy)
base_url: http://127.0.0.1:8000   # control plane (auth surface) for register/tokens
default_profile: default          # profile used when --profile is omitted (V1)
broker:
  scheme: http                    # http or https  (POC forward target; logged only)
  host: 127.0.0.1:8100            # bare host[:port], no scheme (scheme lives above)
```

The legacy `broker` block was a POC-only forward target (recorded, not used by
V2 execute). For V2, set the broker on the environment instead:
`jentic env add <env> --url http://127.0.0.1:8000 --broker-url http://127.0.0.1:8100 --force`.

## XDG layout (`~/.config/jentic`) — the V2 model

The sections above describe the **legacy** `~/.jentic` layout, which keeps
working until you migrate. The V2 CLI stores its state per the XDG Base
Directory spec, and `jentic migrate` copies a legacy profile store into it
(leaving `~/.jentic` untouched apart from a `MIGRATED` marker):

```
~/.config/jentic/config.yaml         # environments, identities, contexts, active_context, theme
~/.config/jentic/agent-account.yaml  # local-agent account record + directory grants (see docs/local-agent.md)
~/.local/state/jentic/               # per-identity key material and cached tokens
~/.cache/jentic/                     # disposable caches
```

`config.yaml` here is a different document from the legacy one: it declares
**environments** (control-plane `base_url` and an optional `broker_url` — the
latter is never derived from `base_url`, but `jentic register` seeds it for a
loopback install), **identities** (agent keypairs), and **contexts** binding an
environment + identity + mode, plus `active_context`. A local environment looks
like:

```yaml
environments:
  local:
    base_url: http://127.0.0.1:8000    # control plane (use 127.0.0.1, not localhost)
    broker_url: http://127.0.0.1:8100  # data plane; http, seeded by register for loopback
```

Inspect and edit it with `jentic context/env/identity` rather than by hand;
`jentic context view` shows the resolved state. The standard `XDG_CONFIG_HOME`
and `XDG_STATE_HOME` variables relocate the config and state directories (the
XDG layout is enforced on every OS, including Windows, so paths stay
predictable); the cache follows the platform's native cache dir.

### File-less mode (orchestrators)

An orchestrator that injects credentials can skip the config file entirely by
setting environment variables — nothing is read from or written to disk:

```bash
export JENTIC_BASE_URL=https://jentic.example.com                # control plane
export JENTIC_BEARER_TOKEN=<token minted for this agent>        # never persisted
export JENTIC_BROKER_URL=https://broker.jentic.example.com      # required for execute on a remote install
```

`JENTIC_BROKER_URL` is the file-less counterpart of the environment's
`broker_url`: without it, `jentic execute` against a remote `JENTIC_BASE_URL`
fail-closes with `RESOLVE_FAILED` (it never falls back to the local default
for a remote control plane). All three are required for a working remote
data-plane loop; the control-plane commands need only the first two.
