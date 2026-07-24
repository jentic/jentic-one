# Jentic CLI

The Jentic CLI ships as **two Go binaries** built from one module, sharing the
same `internal/` packages and the same `~/.jentic` state:

- **`jenticctl`** — the **installer / lifecycle** CLI. It **installs and operates
  jentic-one locally**: stand up a deployment (source venv **or** Docker) and
  manage the running app (health, start/stop, logs, updates, teardown).
- **`jentic`** — the **API-spec** CLI. It manages **agent identities**,
  **discovers and imports APIs** from the public catalog, inspects operations,
  and executes against them.

## What it does

Run `jenticctl` or `jentic` (no args) for the grouped command list, or
`<binary> <command> --help` for any command.

| Binary | Area | Commands | What you get |
| ------ | ---- | -------- | ------------ |
| `jenticctl` | **Setup & lifecycle** | `install` · `doctor` · `status` · `start` · `stop` · `logs` · `update` · `uninstall` | Stand up jentic-one locally (source venv **or** Docker) through an interactive wizard, then manage the running app: health checks, start/stop, log tailing, updates, and teardown. |
| `jentic` | **Identity & access** | `register` · `profile` · `logout` | Each profile is an agent. Register it (Ed25519 + Dynamic Client Registration), switch the active profile, and clear cached tokens. |
| `jentic` | **APIs** | `catalog` · `apis` | Browse, search, and import APIs from the public catalog, then manage the ones in your local registry — revisions, operations, promote/archive, spec download — with interactive TUI browsers. |

**New here?** Run `jenticctl install` to set up locally, then `jentic register`
to create an agent.

## Build

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

## Usage

```bash
# Register an agent, browse the catalog, and execute an operation.
jentic register --base-url http://127.0.0.1:8000
jentic apis
jentic execute --profile default <operation>
```

## Onboarding (`jenticctl install`)

`jenticctl install` is an interactive wizard (colored, keyboard-selectable menus
with sensible defaults) that helps you stand up the **jentic-one app**. It asks
how you want to deploy and configure the platform, **generates** a
`jentic-one.yaml`, and then performs the install for you — either a local
virtualenv (the **Run locally** path) or a containerized docker-compose stack
(the **Run in Docker** path).

Everything the CLI owns is rooted under `~/.jentic`:

```
~/.jentic/
├── jentic-one.yaml      # generated app config (this wizard)
├── docker-compose.yaml  # generated stack (Run in Docker path only)
├── config.yaml          # CLI settings (base_url, profiles)
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
state secret) are **freshly generated** on each run and written into the config
with `0600` perms — never prompted for. After writing the file the wizard prints
the next-step commands for your chosen path and the bootstrap-admin reminder
(`admin@local` / `1234`, rotate to a 12+ char password on first login).

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

## Agent identity (`jentic register`)

Each profile is an **agent**. `jentic register` generates an Ed25519 keypair (if
absent), performs Dynamic Client Registration against the control plane, waits
for an operator to approve the agent, then mints and saves an access/refresh
token pair to the profile. `jentic execute` attaches that token as the
`Authorization` bearer when calling the catalog.

```bash
# Register the default profile (waits for approval, then saves tokens)
jentic register --base-url http://127.0.0.1:8000

# Named profile + custom client name
jentic register --profile work --name my-agent

# Inspect identity / clear tokens
jenticctl status --profile work
jentic logout --profile work
```

Approval is a human, out-of-band step (an operator with `agents:write` calls
`POST {base}/agents/{id}:approve`). `register` polls the token endpoint and
continues automatically once the agent is active. Re-running `register` is
idempotent; `--force` re-keys and re-registers.

Profiles are stored under `~/.jentic/profiles/<name>/` with `0600` perms:

```
~/.jentic/profiles/<name>/
├── profile.yaml   # base_url, agent_id, agent_name, kid, registration token
├── agent.key      # Ed25519 private key (PEM, 0600)
└── tokens.json    # access/refresh tokens + expiry (0600)
```

## Config file (`~/.jentic/config.yaml`)

Instead of (or alongside) flags, settings can be persisted in
`~/.jentic/config.yaml`. The file is optional; a missing file is fine. It is
organized into sections so other settings can be added over time.

```yaml
# ~/.jentic/config.yaml
base_url: http://127.0.0.1:8000   # control plane (auth surface) for register/tokens
default_profile: default          # profile used when --profile is omitted
broker:
  scheme: http                    # http or https
  host: localhost:4000            # bare host[:port], no scheme (scheme lives above)
```

The broker target is split into `broker.scheme` and `broker.host`: `host` is a
bare `host[:port]` with no scheme, and the URL is assembled as
`scheme://host`. A leading scheme in `host` is stripped for tolerance, but the
canonical form keeps it bare.

Precedence (lowest to highest): built-in defaults -> `config.yaml` -> explicit
command-line flags.
