# Upgrade testing

The container upgrade harness (`tests/upgrade/`) verifies that data seeded on a
published release of Jentic One survives a forward migration to the candidate
build and remains usable through the candidate's control-plane and broker APIs.

## Running locally

Run the full lifecycle against an isolated Docker Compose stack (`jentic-upgrade-test`):

```bash
make test-upgrade OLD=0.44.0 BACKEND=postgres
```

When `CANDIDATE` is unset, `tests/upgrade/run.sh` builds `jentic-one/app:candidate`
from the current checkout using `deploy/docker/app.multiarch.Dockerfile`. To reuse
an already-built image across runs:

```bash
make test-upgrade OLD=0.44.0 BACKEND=postgres CANDIDATE=jentic-one/app:candidate
```

The harness uses dedicated loopback ports (`18000` for `app`, `18100` for
`broker`, `18200` for `smoke-upstream`) and an internal `postgres:16` service
with its own named volume, so it never touches local development or integration
test fixtures on port `5432`.

> **macOS Docker bind-mount note:** `smoke-upstream` mounts `../../tests` from
> the repository checkout into `/work/tests`. Run the harness from a checkout
> under a directory shared with your Docker VM (such as `/Users/...`), not from
> `/tmp` when using a runtime like Colima that does not share `/tmp` by default.

## Phases

`tests/upgrade/run.sh` executes six phases in order:

1. **`old-up`** — starts `postgres` (initialized via `tests/upgrade/init-schemas.sql`)
   and `smoke-upstream` (`tests/harness/smoke_upstream`, invoked as
   `python -m tests.harness.smoke_upstream`), runs
   `python -m jentic_one.migrations.run` in the source image
   (`ghcr.io/jentic/jentic-one-app:<OLD>`), and starts the source `app` and
   `broker` containers.
2. **`seed`** — runs the source-version seeder (`tests/upgrade/seed/seed_v0_44.py`)
   over HTTP to create the admin (`admin@local` / `SmokeAdmin12345!`), a member
   user, an unredeemed user invite, an active agent with permissions and a
   `jak_` API key, an imported `smoke-upstream` OpenAPI spec, a bound bearer
   credential with an allow rule, and one pre-upgrade brokered call to
   `/auth/bearer`. Writes `seed-manifest.json`.
3. **`stop`** — stops and removes the old `app` and `broker` containers while
   keeping the PostgreSQL volume intact.
4. **`migrate`** — runs `python -m jentic_one.migrations.run` in the candidate
   image, verifies `python -m jentic_one.migrations.run --check` prints
   `OVERALL current` (exit `0`), and runs `migrations.run` a second time to
   confirm idempotency.
5. **`new-up`** — starts the candidate `app` and `broker` containers against the
   migrated database and waits for `/health`.
6. **`verify`** — runs `tests/upgrade/verify/test_post_upgrade.py` against the
   candidate endpoints and captured logs.

## Old-image bootstrap and container runtime contract

Resolving how published `0.43.0` and `0.44.0` images boot against PostgreSQL
established three invariants baked into `tests/upgrade/compose.yaml` and
`tests/upgrade/init-schemas.sql`:

1. **PostgreSQL schemas and per-surface roles require `init-schemas.sql`:**
   `src/jentic_one/migrations/env.py` probes `pg_namespace` and runs
   `CREATE SCHEMA IF NOT EXISTS "<schema>"` only when the schema is absent,
   which requires database-level `CREATE` privilege if the schema was not
   pre-created, and Alembic never creates PostgreSQL login roles. Because the
   compose stack uses separate least-privilege per-surface roles
   (`registry_user`, `control_user`, `admin_user`) rather than the `postgres`
   superuser, `tests/upgrade/init-schemas.sql` (mounted at
   `/docker-entrypoint-initdb.d/init-schemas.sql`) creates the `registry`,
   `control`, and `admin` schemas, creates the three login roles, and grants
   `USAGE, CREATE` plus default table/sequence/type privileges in each schema.
2. **Container entrypoint and surface selection (`0.43.0` and `0.44.0`):**
   Every published `ghcr.io/jentic/jentic-one-app` image uses the default
   command `python -m jentic_one` (listening on container port `8000`) and
   selects active surfaces via `JENTIC__APPS` (`registry,admin,control,auth` for
   `app`, `broker` for `broker`). Database migrations run via the one-shot
   command `python -m jentic_one.migrations.run` (and `--check` for read-only
   head verification).
3. **Environment variables and image selection (`OLD_IMAGE`, `NEW_IMAGE`, `APP_IMAGE`):**
   Both `0.43.0` and `0.44.0` share the same `JENTIC__DATABASES__<SURFACE>__*`
   settings, indexed encryption keyset variables
   (`JENTIC__CREDENTIALS__ENCRYPTION__ACTIVE_ID=v1`,
   `JENTIC__CREDENTIALS__ENCRYPTION__ENTRIES__0__ID=v1`,
   `JENTIC__CREDENTIALS__ENCRYPTION__ENTRIES__0__MATERIAL`), platform secrets
   (`JENTIC__CREDENTIALS__CONNECT__STATE_SECRET`,
   `JENTIC__ADMIN__AUTH__JWT_SECRET`, `JENTIC__ADMIN__INVITE__PEPPER`), and
   egress allowlists (`JENTIC__INGEST__EGRESS__*`, `JENTIC__BROKER__EGRESS__*`).
   `tests/upgrade/run.sh` exports `OLD_IMAGE` (`ghcr.io/jentic/jentic-one-app:<OLD>`)
   and `NEW_IMAGE` (the candidate image) and passes `APP_IMAGE="${OLD_IMAGE}"`
   during phases 1–2 and `APP_IMAGE="${NEW_IMAGE}"` during phases 4–6;
   `compose.yaml` resolves `${APP_IMAGE:-${NEW_IMAGE:-${OLD_IMAGE:-jentic-one/app:candidate}}}`.

## Checking that the harness catches a break

To verify that the upgrade harness fails loudly on a broken forward migration
and captures actionable diagnostic logs:

1. In a throwaway checkout, inject a failure at the top of `upgrade()` in a
   post-`0.44.0` migration under `src/jentic_one/migrations/control/versions/`
   (for example, `raise RuntimeError("deliberately broken migration")` in
   `e9f0a1b2c3d4_track_connect_flow_completion.py`). Never commit the edit.
2. Build (or let `run.sh` build) the candidate image and run the harness:
   ```bash
   make test-upgrade OLD=0.44.0 BACKEND=postgres
   ```
3. Confirm that phase `[5/6] Running candidate migrations` exits `1`, prints the
   failing migration traceback to stderr, tears down the compose stack and named
   volume, and leaves the diagnostic artifacts in
   `build/upgrade-0.44.0-postgres/` (or `OUT_DIR`):
   - `migrate.log` — candidate Alembic output and the unhandled exception traceback
   - `old-migrate.log` — source-release migration log from phase 2
   - `compose.log` — combined `docker compose logs` across all services
   - `app-failure.log`, `broker-failure.log`, `postgres-failure.log`, `smoke-upstream-failure.log` — per-service failure logs captured by the `EXIT` trap
4. Revert the temporary migration edit (`git checkout -- src/jentic_one/migrations/`)
   and re-run `make test-upgrade OLD=0.44.0 BACKEND=postgres` to confirm the
   green path passes.

## Debugging failures

All logs and the seed manifest are written to `OUT_DIR` (default
`build/upgrade-<OLD>-<BACKEND>/`):

- `seed-manifest.json` — IDs, tokens, and expected state written by the seeder
- `old-migrate.log`, `old-app.log`, `old-broker.log` — source-release logs
- `migrate.log`, `migrate-check.log`, `migrate-idempotent.log` — candidate migration output
- `new-app.log`, `new-broker.log` — candidate startup and request logs
- `compose.log`, `app-failure.log`, `broker-failure.log`, `postgres-failure.log`, `smoke-upstream-failure.log` — `docker compose logs` dumps captured when any phase exits non-zero
