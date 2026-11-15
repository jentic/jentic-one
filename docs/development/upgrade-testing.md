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

## Phases

`tests/upgrade/run.sh` executes six phases in order:

1. **`old-up`** — starts `postgres` (initialized via `tests/upgrade/init-schemas.sql`)
   and `smoke-upstream` (`tests.harness.smoke_upstream`), runs
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

## Debugging failures

All logs and the seed manifest are written to `OUT_DIR` (default
`build/upgrade-<OLD>-<BACKEND>/`):

- `seed-manifest.json` — IDs, tokens, and expected state written by the seeder
- `old-migrate.log`, `old-app.log`, `old-broker.log` — source-release logs
- `migrate.log`, `migrate-check.log`, `migrate-idempotent.log` — candidate migration output
- `new-app.log`, `new-broker.log` — candidate startup and request logs
- `compose.log` — full `docker compose logs` dump captured when any phase exits non-zero
