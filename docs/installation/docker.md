# Installing with Docker

Two containers from one image against an external PostgreSQL. This is the
smallest production-shaped deployment, and the [systemd](systemd.md) and
[Helm](helm.md) guides build on the same image, config, and bootstrap steps.

The container image is the **only supported backend distribution** — there is
no `pip install jentic-one`. The full reference for everything on this page
(worked config, compose file, connection-pool sizing) is
[deploy/README.md](../../deploy/README.md#self-hosted-containers--external-postgres);
this page is the install path in order.

## Platform notes

Any Docker host works; what differs is how Docker gets there and where the
commands run ([full matrix](platform-support.md)):

| Host | Docker | Where to run this page |
| ---- | ------ | ---------------------- |
| Linux | Native | Any shell, as shown |
| macOS | Docker Desktop (or colima) | Any shell, as shown |
| Windows | Docker Desktop, WSL2 backend (Linux containers) | **Inside WSL2** — the snippets are POSIX shell, and `jenticctl` is not shipped for native Windows |

The `127.0.0.1` port binds behave the same everywhere — Docker Desktop
forwards them to the host's loopback on macOS and Windows. The native Windows
`jentic.exe` can call a broker published this way; only the *operating* of the
containers lives in WSL2.

## 1. Pull and verify the image

Tags — including version tags — can be re-pushed; only a `@sha256:` digest is
a true pin. Take the digest from the release's `publish-image` job output (or
the GHCR package page):

```bash
IMAGE="ghcr.io/jentic/jentic-one-app@sha256:<digest-from-the-release>"
docker pull "$IMAGE"

cosign verify \
  --certificate-identity-regexp '^https://github\.com/jentic/jentic-one/\.github/workflows/release\.yml@refs/tags/v.*$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "$IMAGE"
```

Air-gapped? Pull and verify on a connected machine, `docker save`/`docker load`
the tarball across (see the [quickstart](quickstart.md#air-gapped-transfer)).

## 2. Write the config

Save the non-secret config as `/etc/jentic/production.yaml` on the host. Start
from [`config/production.yaml.example`](../../config/production.yaml.example);
the worked, commented version is in the
[deploy README](../../deploy/README.md#a-worked-production-configyaml). It must
contain:

- the three database sections (`registry`, `control`, `admin`) pointing at
  your Postgres — three schemas in one database is the shipped shape;
- `auth.canonical_base_url` set to the app's public URL;
- the credential-encryption keyset (`credentials.encryption`) — it is a list,
  so it lives in this file, not an env var. Treat the file as a secret.

Secrets go in an env file, `/etc/jentic/prod.env` (`chmod 600`, values
unquoted — `docker --env-file` takes them literally):

```bash
JENTIC_ENV=production
JENTIC_CONFIG_FILE=/etc/jentic/production.yaml

JENTIC__DATABASES__REGISTRY__PASSWORD=…
JENTIC__DATABASES__CONTROL__PASSWORD=…
JENTIC__DATABASES__ADMIN__PASSWORD=…

JENTIC__ADMIN__AUTH__JWT_SECRET=…
JENTIC__ADMIN__INVITE__PEPPER=…
JENTIC__CREDENTIALS__CONNECT__STATE_SECRET=…
```

In production the app refuses to boot with placeholder values for the last
three. Generate fresh material (also for the encryption keyset) with:

```bash
python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
```

## 3. Prepare the database

Once, on your Postgres (the database itself must already exist). The migration
runner never creates roles, and it only creates a missing schema when its
database user holds `CREATE` on the database — with the least-privilege roles
below it does not, so pre-create the schemas and grants:

```sql
CREATE SCHEMA IF NOT EXISTS registry;
CREATE SCHEMA IF NOT EXISTS control;
CREATE SCHEMA IF NOT EXISTS admin;

CREATE ROLE registry_user LOGIN PASSWORD '…';
CREATE ROLE control_user LOGIN PASSWORD '…';
CREATE ROLE admin_user LOGIN PASSWORD '…';

GRANT USAGE, CREATE ON SCHEMA registry TO registry_user;
GRANT USAGE, CREATE ON SCHEMA control TO control_user;
GRANT USAGE, CREATE ON SCHEMA admin TO admin_user;
```

(Passwords must match the `JENTIC__DATABASES__*__PASSWORD` values in
`prod.env`. Simpler variant: one owning role for all three schemas.)

No Postgres yet, or want it in Docker too?
[`docker/local-setup/docker-compose.yaml`](../../docker/local-setup/docker-compose.yaml)
runs `postgres:16` with
[`init-schemas.sql`](../../docker/local-setup/init-schemas.sql) — the worked
version of the SQL above, run automatically on first boot (dev passwords;
change them for anything real).

## 4. Run migrations

```bash
docker run --rm --env-file /etc/jentic/prod.env \
  -v /etc/jentic:/etc/jentic:ro \
  "$IMAGE" \
  python -m jentic_one.migrations.run
```

## 5. Create the first admin

```bash
read -rs ADMIN_PASSWORD   # or fetch from your secrets manager
printf '%s\n' "$ADMIN_PASSWORD" | docker run --rm -i --env-file /etc/jentic/prod.env \
  -v /etc/jentic:/etc/jentic:ro \
  "$IMAGE" \
  python -m jentic_one create-admin --email admin@example.com
```

Re-running is safe: it exits non-zero with `setup already complete` if an
admin exists.

## 6. Start app and broker

Same image; only `JENTIC__APPS` and the host port differ. The broker must run
as the **sole surface** in its container.

```bash
# app: control plane (registry,admin,control,auth — the image default). UI + APIs.
docker run -d --name jentic-app --env-file /etc/jentic/prod.env \
  -v /etc/jentic:/etc/jentic:ro -p 127.0.0.1:8000:8000 \
  "$IMAGE"

# broker: data plane — the execution edge agents call.
docker run -d --name jentic-broker --env-file /etc/jentic/prod.env \
  -e JENTIC__APPS=broker \
  -v /etc/jentic:/etc/jentic:ro -p 127.0.0.1:8100:8000 \
  "$IMAGE"
```

```bash
curl -fsS http://localhost:8000/health   # app
curl -fsS http://localhost:8100/health   # broker
```

Both surfaces speak plain HTTP — the loopback binds keep them off the network
until a TLS-terminating reverse proxy fronts them. Route UI/control traffic to
the app and execution traffic to the broker; agents need both URLs.

Prefer one file over two `docker run`s? Two worked compose examples:

- [app + broker against an external Postgres](../../deploy/README.md#docker-compose-example)
  — migrate → app + broker with health checks; the production shape of this page.
- [the whole stack including Postgres](../agent/install.md#4-write-the-compose-file--jenticdocker-composeyaml)
  — the agent runbook's self-contained compose file (app, broker, and a
  `postgres:16` service with the schema init baked in).

## 7. Connect the CLIs

- **Admin host:** install `jenticctl` from the release archive
  ([download + verify](../../cli/README.md#3-manual-download--verify)).
- **Every host using the instance:** install `jentic` the same way, then
  register — an operator approves each agent in the UI:

```bash
jentic register --url https://jentic.example.com --broker-url https://broker.jentic.example.com
```

Then walk through the [first brokered call](../guides/first-call.md).

## Upgrading

1. Take the new release's digest, re-run the `cosign verify` above against it.
2. Re-run step 4 (migrations) with the new image, then recreate the `app` and
   `broker` containers from it.

Migrations are applied forward — prefer rolling forward to a fixed release
over rolling the image back onto a newer schema. Details:
[Upgrading (and rolling back)](../../deploy/README.md#upgrading-and-rolling-back).
