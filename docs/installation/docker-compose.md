# Installing with docker-compose

One file brings up migrations, the first-admin bootstrap, and the two
long-running services against an **external** PostgreSQL — the compose shape
of the [Docker guide](docker.md), which explains each step (image digest,
config, database prep). Follow that page through step 3 first, then save the
worked config as `./production.yaml` and the secrets env block as `.env`,
both next to the compose file.

Want the database in compose too?
[`docker/local-setup/docker-compose.yaml`](../../docker/local-setup/docker-compose.yaml)
adds a `postgres:16` service with the schema init baked in (dev passwords),
and the [agent runbook](../agent/install.md#4-write-the-compose-file--jenticdocker-composeyaml)
has a fully self-contained variant.

## The compose file

```yaml
# docker-compose.yaml — app + broker against an EXTERNAL Postgres.
# `migrate` is a one-shot init service; `app` and `broker` wait for it to
# complete before starting. The first admin is created afterwards with a
# one-off `docker compose run` (see below).
#
# The digest pin is the reproducibility contract: replace it with the digest
# echoed by the release you are deploying. A floating tag here would let a
# re-pushed tag change your deployment underneath you.
#
# Ports bind to 127.0.0.1: both surfaces speak plain HTTP, so expose them via
# a TLS-terminating reverse proxy (or your ingress) — never directly.
#
# The healthcheck uses python (the image has no curl); it makes
# `docker compose ps` a real health signal, e.g. a crash-looping broker.
x-image: &image ghcr.io/jentic/jentic-one-app@sha256:<digest-from-the-release>
x-healthcheck: &healthcheck
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
  interval: 30s
  timeout: 5s
  retries: 3
  start_period: 15s
x-common: &common
  image: *image
  env_file: [.env]                 # JENTIC_ENV, DB passwords, the three secrets
  volumes:
    - ./production.yaml:/etc/jentic/production.yaml:ro
  environment:
    JENTIC_CONFIG_FILE: /etc/jentic/production.yaml

services:
  migrate:
    <<: *common
    command: ["python", "-m", "jentic_one.migrations.run"]
    restart: "no"

  app:
    <<: *common
    depends_on:
      migrate: { condition: service_completed_successfully }
    ports: ["127.0.0.1:8000:8000"]
    restart: unless-stopped
    healthcheck: *healthcheck

  broker:
    <<: *common
    depends_on:
      migrate: { condition: service_completed_successfully }
    # YAML merge replaces the whole `environment` map, so JENTIC_CONFIG_FILE
    # must be re-declared alongside the broker-specific JENTIC__APPS.
    environment:
      JENTIC_CONFIG_FILE: /etc/jentic/production.yaml
      JENTIC__APPS: broker
    ports: ["127.0.0.1:8100:8000"]
    restart: unless-stopped
    healthcheck: *healthcheck
```

## Bootstrap

Create the roles and schemas on your Postgres first
([Docker guide, step 3](docker.md#3-prepare-the-database)), then:

```bash
docker compose up -d migrate                 # runs migrations, then exits 0
docker compose logs migrate                  # `up -d` detaches — check it succeeded
read -rs ADMIN_PASSWORD
printf '%s\n' "$ADMIN_PASSWORD" | docker compose run --rm -T app \
  python -m jentic_one create-admin --email admin@example.com
docker compose up -d app broker              # start the long-running services
curl -fsS http://localhost:8000/health       # app
curl -fsS http://localhost:8100/health       # broker
```

## Upgrading (and rolling back)

When the next release is cut:

0. Take a [backup](../operations/backup-restore.md) — it is the rollback.
1. Take the new digest from that release's `publish-image` job output (or
   the GHCR package page) and re-run `cosign verify` against it
   ([Docker guide, step 1](docker.md#1-pull-and-verify-the-image)).
2. Edit the `x-image` digest in the compose file.
3. `docker compose up -d` — compose recreates `migrate` first (its config
   changed), re-runs the pending migrations, and only recreates
   `app`/`broker` once it exits 0. **If migrate fails, `up` aborts and the
   old containers keep running the old release** — fix, then re-run.

Migrations apply forward, and restoring the pre-upgrade snapshot (step 0) is
the rollback — a schema downgrade path exists
(`python -m jentic_one.migrations.run --direction down`) but is break-glass,
not a supported rollback; the full contract is
[docs/operations/upgrades.md](../operations/upgrades.md).
