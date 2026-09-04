# Troubleshoot Jentic One — agent runbook

Symptom-keyed fixes for installs created by [install.md](install.md). The
rules there apply here too — especially rule 3: if the symptom is not in this
file, **report to the human and stop**; do not improvise a recovery.

## CLI download fails (no URL resolved, or 404)

A release tag can exist before CI finishes attaching its binaries; the
install snippet already skips asset-less releases by matching on
`browser_download_url`. If it still fails, the assets are genuinely
unavailable right now. **Do not build from source, do not go looking for
installer scripts, and do not construct download URLs by hand.** Report the
error and stop.

## `${VER}`/`${PGPASS}` resolve empty; Postgres exits with "superuser password is not specified"

`docker compose` interpolates those from `~/.jentic/.env` (it sits next to
the compose file); shell variables from earlier steps do not survive between
command blocks. If `.env` is missing or incomplete, recreate it:
`VER=<installed version>` (`jentic --version`) and, for Postgres, the
existing `PGPASS`.

If Postgres already tried to initialise with an empty password, the volume is
half-initialised. On a **fresh** install:

```bash
docker compose -p jentic -f ~/.jentic/docker-compose.yaml down
docker volume rm jentic_db-data
```

then fix `.env` and re-run migrations (install.md Step 6).

## Postgres schemas missing, or someone suggests an init script

Never add `/docker-entrypoint-initdb.d/…` scripts to the `db` service. The
migration run creates the `registry`/`control`/`admin` schemas itself,
idempotently. Postgres init scripts run exactly once, on an empty volume, and
a script that fails mid-init leaves a broken volume that never gets its
schemas — that is why this system deliberately avoids them. If a broken init
already happened on a fresh install, remove the `jentic_db-data` volume (see
above) and re-run migrations.

## Image pull denied

The package may be private on this network: `docker login ghcr.io` or mirror
the image, then retry. Digest pinning and cosign verification:
[installation/README.md](../installation/README.md).

## Migrations fail

- The data volume was created **by this run** (fresh install): discard it so
  the retry starts clean, fix the cause, and re-run —
  `docker compose -p jentic -f ~/.jentic/docker-compose.yaml down`
  then `docker volume rm jentic_jentic-data` (Postgres: `jentic_db-data`).
- The volume **pre-existed** (reinstall): do **not** remove it — it may hold
  real data. Stop and report the migration error to the human.

## App or broker never becomes healthy

A cold start (first boot, empty database) can take tens of seconds — poll,
don't probe once. If it still never answers, read the logs and report:

```bash
docker compose -p jentic -f ~/.jentic/docker-compose.yaml logs
```

## `setup_required` flaps

A single `false` right after startup can be a DB-warmup blip. Trust it only
when two reads ~2 s apart agree.

## `invalid_grant` on register or token exchange

The token audience is an **exact string match** against
`auth.canonical_base_url`. Register with exactly that URL — `127.0.0.1` vs
`localhost` is the classic mismatch on a local install.

## `jentic register` times out (exit 3, `TIMEOUT_PENDING`)

The registration is saved. Have the human approve at `/app/agents`, then
re-run the same command — it resumes idempotently.

## `jentic execute` fail-closes against a remote install

Registering with a remote install requires an explicit
`--broker-url https://<broker-host>` — it is never derived from the
control-plane URL. Re-register with both URLs.

## SQLite "disk I/O error"

The database must live on a named volume, not a host bind mount — Docker
Desktop's file sharing lacks the locking semantics SQLite needs.

## No human available at the first-admin gate (CI, fleet installs)

An operator can create the first admin non-interactively, password piped on
stdin (never argv):

```bash
docker compose -p jentic -f ~/.jentic/docker-compose.yaml \
  run --rm -T app python -m jentic_one create-admin --email <email>
```

Prefer the browser gate (`/app/setup`) whenever a human is present.
