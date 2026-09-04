# Local development setup

## One command: `make dev`

From a source checkout, the single supported way to bring the stack up locally
— including **after a machine reboot** — is:

```bash
make dev
```

`make dev` is **idempotent**: it is safe to run repeatedly, and each step is a
no-op when it is already satisfied. It performs, in order:

1. **Docker preflight.** Verifies Docker is installed and running. If Docker is
   not running it fails fast with an actionable message (start Docker Desktop on
   macOS / the Docker daemon on Linux) instead of failing deep inside a
   migration.
2. **Database fixtures + migrations.** If Postgres is already reachable it just
   re-applies `alembic upgrade head` for each schema (a no-op when current);
   otherwise it starts the Docker fixtures and runs the full setup
   (`scripts/setup.sh` → compose up, create schemas, migrate).
3. **UI bundle.** If `ui/dist` has no built `index.html` it runs `make ui-build`;
   otherwise it skips. (If Node is unavailable it warns and continues — the
   backend API still runs, only the `/app` SPA is unavailable.)
4. **Start the app.** Runs `make start-app` with `JENTIC_CONFIG_FILE` set
   automatically, serving all surfaces on `http://127.0.0.1:8000`.

When it finishes, the SPA is reachable at `http://127.0.0.1:8000/app` and the
site root redirects there.

**SPA serving from source:** the server falls back to `ui/dist` when the
packaged `jentic_one/static` bundle is absent (it only exists in a built
wheel), so no copy/symlink step is needed. Wheel/production serving is
unchanged — the packaged bundle takes precedence when present.

## First-time setup

Before your first `make dev`, install dependencies and git hooks once:

```bash
make install   # sync Python deps, install UI deps, install git hooks
```

## Related targets

| Target | Description |
| ------ | ----------- |
| `make dev` | Idempotent bring-up + start the app (the one-command flow above) |
| `make start-fixtures` | Start Docker DB fixtures and apply migrations |
| `make stop-fixtures` | Stop the Docker DB fixtures (keeps volumes) |
| `make destroy-fixtures` | Remove fixtures **and volumes** (destructive reset) |
| `make ui-build` | Build the UI bundle into `ui/dist` |
| `make start-app` | Start the combined app (all surfaces) |

`make dev` never runs the destructive `destroy-fixtures`, so it is safe against
an existing local Postgres.
