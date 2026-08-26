# Installing with Helm

The Helm chart at
[`deploy/helm/jentic-one/`](../../deploy/helm/jentic-one/) is an umbrella
chart with one subchart per service (`app`, `broker`, `registry`, `admin`,
`control`) plus an optional bundled Postgres for dev clusters.

Read this first: **the Helm path is currently the least turnkey of the three
guides.** The chart is exercised in CI on every PR (kind, three modes), but
two gaps matter for production — see
[What's deferred](../../deploy/README.md#whats-deferred):

- The chart is **not published** to any registry. Vendor
  `deploy/helm/jentic-one/` from a checkout of this repository **at the
  release tag** you are deploying.
- Only the combined `app` image is published to GHCR. The subcharts reference
  per-service images (`jentic-one/<svc>`), and the broker subchart cannot run
  the published app image (it has no way to set `JENTIC__APPS=broker` on it).
  So the broker — and the split-surface `parts` topology — need images you put
  into your own registry.

If those constraints don't fit, the [Docker](docker.md) or
[systemd](systemd.md) guide gives you the same two-service topology today.

## 1. Get the images into your registry

On a connected machine, from a checkout at the release tag:

```bash
make build-all    # builds jentic-one/{app,broker,registry,admin,control}
make save-all     # writes build/jentic-<svc>-<version>.tar for offline transfer
```

Transfer the tarballs, `docker load -i` them inside the network, retag, and
push to your internal registry. (The combined topology needs only `app` and
`broker`.) The published `ghcr.io/jentic/jentic-one-app` image can substitute
for the `app` subchart only, via
`--set app.image.repository=ghcr.io/jentic/jentic-one-app --set app.image.tag=X.Y.Z`.

## 2. Write a values file

The shipped [`values/local-*.yaml`](../../deploy/helm/values/) files are for
dev/kind — with a bundled Postgres and plaintext dev passwords. For production,
write your own against an external Postgres:

```yaml
# values-prod.yaml
app:
  enabled: true
  image: { repository: registry.internal/jentic-one/app, tag: "X.Y.Z" }
broker:
  enabled: true
  image: { repository: registry.internal/jentic-one/broker, tag: "X.Y.Z" }

postgresql:
  enabled: false          # external Postgres — no bundled DB

global:
  postgresql:
    enabled: false        # also disables the chart's migrate hook (see step 3)
  databases:
    registry: { host: db.prod.internal, name: jentic, user: registry_user, schema: registry }
    control:  { host: db.prod.internal, name: jentic, user: control_user,  schema: control }
    admin:    { host: db.prod.internal, name: jentic, user: admin_user,    schema: admin }
```

**Secrets:** do not put database passwords (or the JWT secret, invite pepper,
state secret, encryption keyset) in a values file. The chart's default
`common.db-env` helper injects plain env values and is **dev-only** — for
production, source them from a Kubernetes Secret via `secretKeyRef`, following
[Production secrets](../../deploy/README.md#production-secrets). The mandatory
set is listed in
[Mandatory vs optional secrets](../../deploy/README.md#mandatory-vs-optional-secrets).

## 3. Prepare the database and run migrations

Create the schemas and roles on your Postgres first — the SQL is in the
[Docker guide, step 3](docker.md#3-prepare-the-database).

The chart's migration Job is a post-install/post-upgrade hook that renders
only when `global.postgresql.enabled` is true (the bundled-DB dev path).
Against an external Postgres, run migrations yourself before (and on every
upgrade of) the release. The entrypoint is
`python -m jentic_one.migrations.run`, bundled in every image; the simplest
way to run it is the one-shot container from the
[Docker guide, step 4](docker.md#4-run-migrations), from any machine that
reaches the database. In-cluster, run it as a one-off Job on the same image,
with the `JENTIC__DATABASES__*` env sourced from your Secret via `envFrom`.

## 4. Install

```bash
helm install jentic ./deploy/helm/jentic-one -f values-prod.yaml
kubectl get pods                       # app + broker Ready
```

Create the first admin the same way as migrations — the
`python -m jentic_one create-admin --email …` one-shot from the
[Docker guide, step 5](docker.md#5-create-the-first-admin), run against the
same database. It exits with `setup already complete` if an admin exists, so
re-running is safe.

Both services speak plain HTTP on port 8000 — terminate TLS at your ingress,
routing UI/control traffic to the `app` service and execution traffic to the
`broker` service. Agents need both URLs
(`jentic register --url … --broker-url …`).

## Observability

Each pod can run an OpenTelemetry Collector sidecar:

```bash
helm upgrade jentic ./deploy/helm/jentic-one -f values-prod.yaml \
  --set global.observability.otel.enabled=true \
  --set global.observability.otel.endpoint=http://otel-collector:4317
```

Metrics exporter selection (`otlp` / `prometheus` / `none`) and the scrape
annotations are covered in [Metrics](../../deploy/README.md#metrics).

## Upgrading

1. Put the new release's images into your registry (step 1) and bump the tags
   in `values-prod.yaml`.
2. Re-run migrations (step 3) against the new image.
3. `helm upgrade jentic ./deploy/helm/jentic-one -f values-prod.yaml` — from
   the chart vendored at the **new** release tag.

Migrations apply forward; prefer rolling forward to a fixed release —
see [Upgrading (and rolling back)](../../deploy/README.md#upgrading-and-rolling-back).
