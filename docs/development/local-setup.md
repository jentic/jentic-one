# Local development setup

The contributor on-ramp: from a fresh clone to a running stack. Running the tests is [Testing](testing.md); commit,
sign-off, and PR rules are in [`CONTRIBUTING.md`](../../CONTRIBUTING.md).

## Prerequisites

| Tool | Needed for | Source of truth |
| ---- | ---------- | --------------- |
| Python | the backend | `requires-python` in [`pyproject.toml`](../../pyproject.toml), [`mise.toml`](../../mise.toml) |
| [uv](https://docs.astral.sh/uv/) | Python deps, every `make` target that runs Python | `uv.lock` |
| Docker with Compose v2 | the Postgres fixtures (`make dev`, `make start-fixtures`, integration tests) | [`docker/local-setup/docker-compose.yaml`](../../docker/local-setup/docker-compose.yaml) |
| Node.js + npm | the UI bundle, UI tests, lint hooks on `ui/` files | `node-version` in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) |
| Go | the `jentic` / `jenticctl` CLIs, `make cli-reference` | `go` directive in [`cli/go.mod`](../../cli/go.mod) |

Docker is optional if you only use the SQLite stack
([`make start-app-sqlite`](#no-docker-sqlite)); Node is optional for
backend-only work (`make install` skips the UI deps when `node` is missing);
Go is optional unless you touch `cli/` or the CLI reference.

## First-time setup

```bash
git clone https://github.com/jentic/jentic-one.git
cd jentic-one
make install
```

`make install` runs three targets:

| Target | Does |
| ------ | ---- |
| `make sync` | `uv sync --dev --all-extras` |
| `make ui-setup` | `npm ci` in `ui/` (skipped with a notice when `node` is not on `PATH`) |
| `make hooks` | `uv run lefthook install` — pre-commit (lint, UI eslint/prettier, detect-secrets) and commit-msg (conventional commits) |

Confirm the environment with `make check` (lint + mypy, the secrets audit,
the architecture tests, then the OpenAPI scorecard, which skips itself when
`JENTIC_SCORE_API_KEY` is unset).

## Run the stack

Pick one:

| Command | What runs | Needs |
| ------- | --------- | ----- |
| `make dev` | Fixtures + migrations + UI bundle if missing, then the combined app on `:8000` | Docker |
| `make start-local` | Combined app on `:8000` **and** the broker on `:8100`, in one terminal (Ctrl-C stops both) | Fixtures already running (`make dev` once, or `make start-fixtures`) |
| `make start-app` + `make start-broker` | The same two processes, in two terminals | Fixtures already running |
| `make start-app-sqlite` | Combined app on `:8000` over SQLite files in `.data/` | Nothing (no Docker) |

The combined app serves the registry, admin, control, and auth surfaces; the
broker is a separate process. Anything that executes a call (`jentic execute`,
the MCP `execute` tool) needs the broker, so use `make start-local` for
end-to-end work.

All targets use `config/local.yaml` (`JENTIC_CONFIG_FILE`) unless you set
`JENTIC_CONFIG_FILE` yourself. Once the app is up, open
`http://127.0.0.1:8000/app/setup` and create the first admin.

### `make dev`

Idempotent — safe to re-run, including after a reboot. Each step is a no-op
when already satisfied ([`scripts/dev-up.sh`](../../scripts/dev-up.sh)):

1. **Docker preflight.** Fails fast if `docker` is missing or the daemon is
   not running.
2. **Fixtures + migrations.** If Postgres already answers, it only re-runs
   the migration runner (`python -m jentic_one.migrations.run`); otherwise it
   runs [`scripts/setup.sh`](../../scripts/setup.sh) (compose up, create
   schemas, migrate).
3. **UI bundle.** Runs `make ui-build` only if `ui/dist/index.html` is
   missing (warns and continues when Node is unavailable — the API still
   runs, only `/app` is missing).
4. **Start.** `make start-app` on `http://127.0.0.1:8000`; `/` redirects to
   `/app`.

`make dev` never removes volumes. From a source checkout the server serves
the SPA from `ui/dist` (the packaged bundle only exists in a built wheel).

### No Docker: SQLite

`make start-app-sqlite` applies migrations to `.data/{registry,control,admin}.db`
(`make migrate-sqlite`) and starts the combined app with
`config/local-sqlite.yaml`, which uses SQLite FTS5 lexical search. It starts
the combined app only.

### Fixture targets

| Target | Does |
| ------ | ---- |
| `make start-fixtures` | Start the Postgres container and apply migrations |
| `make stop-fixtures` | Stop the container (keeps the volume) |
| `make destroy-fixtures` | Remove the container **and its volume** (full reset) |

## UI development

See [`ui/README.md`](../../ui/README.md) for the Vite dev server, MSW mocks, and
UI tests. `make dev` builds `ui/dist` only when it is missing; run
`make ui-build` after pulling UI changes.

## Build the CLIs

See [`cli/README.md`](../../cli/README.md); `make help` in `cli/` lists the build
and check targets.

## Common failures

| Symptom | Fix |
| ------- | --- |
| `ERROR: Docker is installed but not running.` | Start Docker Desktop (macOS) or the daemon (Linux), re-run `make dev`. Or use `make start-app-sqlite`. |
| Postgres port 5432 already taken | `JENTIC_PG_PORT=55432 make start-fixtures`; `setup.sh` then prints the `JENTIC__DATABASES__*__PORT` overrides to pass to `make start-app`. Use the same `JENTIC_PG_PORT` for integration tests. |
| `start-local: port 8000 is in use by …` | Stop the named process; `make start-local` refuses to start over a busy port. |
| `/app` shows an old UI | `make ui-build`. |
| `/app` returns 404 from source | `ui/dist` was never built because Node is missing: install Node, then `make ui-build`. |
| `make check` fails at `score` | The scorecard runs only with `JENTIC_SCORE_API_KEY` set and then needs `npx` and network; unset the key to skip it. |
| Migrations fail on a throwaway local DB | `make destroy-fixtures && make start-fixtures` (deletes all local data). |
| UI tests fail to launch a browser | `cd ui && npx playwright install chromium` (see [`ui/README.md`](../../ui/README.md)). |
| Commit rejected by the `commit-msg` hook | The message needs `type(scope): subject` — see [`CONTRIBUTING.md`](../../CONTRIBUTING.md#commits). |
