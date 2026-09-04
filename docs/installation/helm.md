# Installing with Helm

The Helm chart at
[`deploy/helm/jentic-one/`](../../deploy/helm/jentic-one/) is an umbrella
chart with one subchart per service (`app`, `broker`, `registry`, `admin`,
`control`) plus an optional bundled PostgreSQL. The chart can be zero-touch:
with generated secrets enabled, every secret (credential-encryption keyset,
JWT signing secret, database passwords) is created on first install and
reused on every upgrade, and migrations run as a post-install hook.

Two constraints to know up front (the chart is smoke-tested in CI — kind,
all modes, post-merge and as a release gate — see the
[chart docs](../../deploy/helm/README.md#known-gaps) for the full gap list):

- The chart is **not published** to any registry. Vendor
  `deploy/helm/jentic-one/` from a checkout of this repository **at the
  release tag** you are deploying.
- Only the combined `app` image is published to GHCR; the `broker` subchart
  needs an image you build and put where your cluster can pull it
  (`make build-all`).

If those constraints don't fit, the [Docker](docker.md) or
[systemd](systemd.md) guide gives the same two-service topology today.
Deploying on Amazon EKS? The [AWS Marketplace](aws-marketplace.md) listing is
the packaged version of this chart with published images.

## Prerequisites

- A Kubernetes cluster — [kind](https://kind.sigs.k8s.io/), minikube, or
  Docker Desktop locally; any 1.29+ cluster otherwise — plus `kubectl` and
  `helm` (≥ 3.8).
- A checkout of this repository at the release tag (chart + image builds).
- For the bundled PostgreSQL: a default StorageClass. Local clusters ship
  one; on a bare cluster the symptom of missing storage is the `postgresql`
  pod `Pending` with "pod has unbound immediate PersistentVolumeClaims".

## 1. Build the images

From the checkout at the release tag:

```bash
make build-all    # builds jentic-one/{app,broker,registry,admin,control}
make save-all     # writes build/jentic-<svc>-<version>.tar for offline transfer
```

The combined topology needs only `app` and `broker`. For a remote cluster,
`docker load -i` the tarballs, retag, and push to your internal registry. The
published `ghcr.io/jentic/jentic-one-app` image can substitute for the `app`
subchart only, via `--set app.image.repository=ghcr.io/jentic/jentic-one-app
--set app.image.tag=X.Y.Z`.

## 2. Install

### Local dev cluster (kind) — one flow

The repo's own tooling creates the cluster, loads the images, and installs
the chart with the dev values
([`deploy/helm/values/local-combined.yaml`](../../deploy/helm/values/) —
bundled Postgres, dev secrets, app published on `localhost:8000`):

```bash
make build-all
uv run python -m tools.deploy cluster up
uv run python -m tools.deploy up --mode combined
```

### Your own cluster — generated secrets, bundled Postgres

The zero-touch shape: no passwords or further configuration at install time.
Secrets are generated on first install and reused verbatim on every upgrade;
the bundled database's init script creates the schemas and roles; migrations
run as a post-install/post-upgrade hook.

```bash
helm install jentic ./deploy/helm/jentic-one \
  --namespace jentic-one --create-namespace \
  --set global.appSecrets.generate=true \
  --set postgresql.enabled=true \
  --set global.postgresql.enabled=true \
  --set global.image.registry=registry.internal/jentic-one \
  --set global.image.tag=X.Y.Z
```

(`global.image.registry` + `global.image.tag` pin every subchart's image in
one place; per-service `<svc>.image.repository`/`.tag` overrides win where
set.)

## 3. Set the canonical base URL

Required before agents can connect. Set it to the URL your agents will reach
the app at (your ingress or load-balancer URL). Agent token exchange compares
this value byte-for-byte against the `--url` agents register with — a
mismatch (including `localhost` vs `127.0.0.1`) fails with `invalid_grant`
*after* the agent is approved:

```bash
helm upgrade jentic ./deploy/helm/jentic-one --reuse-values \
  --set app.extraEnv.JENTIC__AUTH__CANONICAL_BASE_URL=https://jentic.example.com
```

(The kind dev values already set it to `http://localhost:8000`.)

## 4. Verify

```bash
kubectl -n jentic-one get pods
# expect: app, broker (+ postgresql on the bundled path) — all Running

kubectl -n jentic-one port-forward svc/jentic-app 8000:8000
curl -s http://localhost:8000/health   # {"status":"ok","version":"<version>"}
```

Open the app URL and create the first admin account (the one-time `/setup`
page), then connect an agent:

```bash
jentic register --url <app URL> --broker-url <broker URL>
```

Both services speak plain HTTP on port 8000 in-cluster — terminate TLS at
your ingress, routing UI/control traffic to the `app` Service and execution
traffic to the `broker` Service. Agents need both URLs. Then walk through
the [first brokered call](../guides/first-call.md).

## External database (production)

For a managed PostgreSQL, disable the bundled instance and point each surface
at your endpoint:

```yaml
# values-prod.yaml
postgresql:
  enabled: false
global:
  postgresql:
    enabled: false        # also disables the chart's migrate hook (see below)
  databases:
    registry: { host: db.prod.internal, name: jentic, user: registry_user, schema: registry }
    control:  { host: db.prod.internal, name: jentic, user: control_user,  schema: control }
    admin:    { host: db.prod.internal, name: jentic, user: admin_user,    schema: admin }
```

- **Schemas and roles:** create them on the instance first — the SQL is in the
  [Docker guide, step 3](docker.md#3-prepare-the-database).
- **Migrations:** the chart's migrate hook renders only on the bundled-DB
  path. Against an external Postgres, run
  `python -m jentic_one.migrations.run` yourself before (and on every upgrade
  of) the release — the one-shot container from the
  [Docker guide, step 4](docker.md#4-run-migrations) works from any machine
  that reaches the database, or run it in-cluster as a one-off Job on the
  same image with the `JENTIC__DATABASES__*` env sourced from your Secret.
- **First admin:** same shape — the
  `python -m jentic_one create-admin --email …` one-shot from the
  [Docker guide, step 5](docker.md#5-create-the-first-admin). Re-running is
  safe (`setup already complete`).
- **Secrets:** database passwords must match the roles you created — explicit
  `global.databases.*.password` values always win over generated ones. Do not
  put them in a values file: source them from a Kubernetes Secret via
  `secretKeyRef`, or mount your own Secret with
  `global.appSecrets.existingSecret` — see [Secrets](#secrets) below. The
  mandatory set is the same as the
  [Docker guide, step 2](docker.md#2-write-the-config).

## Secrets

Four config values have no safe default: the credential-encryption keyset
(`credentials.encryption` — a *list*, so it cannot ride the flat `JENTIC__*`
env convention; credential writes fail without it), the admin JWT secret, the
invite pepper, and the connect state secret (all three ship a placeholder
that `JENTIC_ENV=production` refuses to boot with). On the bundled-DB path
the same Secret also carries the database passwords. The chart offers three
sources, in order of preference:

1. **`global.appSecrets.generate: true`** — the chart mints random values
   into a release-scoped Secret (`<release>-app-secrets`) on first install
   and **reuses each key verbatim on every upgrade** (regenerating would
   orphan everything already encrypted, revoke every live session, and break
   DB logins). The Secret carries `helm.sh/resource-policy: keep`, so
   `helm uninstall` leaves it behind and a same-name reinstall re-adopts it.
   Caveat: piping `helm template` to `kubectl apply` bypasses the lookup and
   **will** rotate the secrets — use `helm install`/`upgrade`.
2. **`global.appSecrets.existingSecret: <name>`** — mount your own Secret
   (SealedSecrets, External Secrets Operator, …). It must hold a
   `config.yaml` key shaped like the keyset block in the
   [worked config](docker.md#2-write-the-config), plus
   `admin.auth.jwt_secret`, `admin.invite.pepper`, and
   `credentials.connect.state_secret` — and, if the bundled Postgres is
   enabled, the four `db-password-*` keys.
3. **Per-service `configFile.contents`** (dev overlays only) — inlines
   secrets into a plain ConfigMap; never for real data. Mutually exclusive
   with the two modes above (both claim `JENTIC_CONFIG_FILE`; the chart
   fails the render rather than silently preferring one).

For external-database passwords, keep them out of values files entirely —
reference a Secret you manage:

```yaml
- name: JENTIC__DATABASES__REGISTRY__PASSWORD
  valueFrom:
    secretKeyRef:
      name: jentic-db-credentials
      key: registry-password
```

Host/port/name/schema are not secrets — plain values are fine for those.
Encryption-key **rotation** is a config-level operation in every mode: add a
new keyset entry, flip `active_id`, keep the old entry until all rows are
re-encrypted ([upgrades.md](../operations/upgrades.md#what-an-upgrade-never-does)).

## Observability

Each pod can run an OpenTelemetry Collector sidecar:

```bash
helm upgrade jentic ./deploy/helm/jentic-one --reuse-values \
  --set global.observability.otel.enabled=true \
  --set global.observability.otel.endpoint=http://otel-collector:4317
```

Metrics exporter selection (`otlp` / `prometheus` / `none`) and the scrape
annotations are covered in the
[chart docs](../../deploy/helm/README.md#metrics-exporter).

## Upgrading

1. Build/push the new release's images (step 1) and re-vendor the chart at
   the **new** release tag.
2. `helm upgrade jentic ./deploy/helm/jentic-one …` with the new
   `global.image.tag`. On the bundled-DB path the migrate hook re-runs
   automatically; against an external database, re-run migrations first
   (see above).

Generated secrets are never rotated by an upgrade, and `helm uninstall`
intentionally keeps the `jentic-app-secrets` Secret (and the Postgres PVC) so
stored credentials survive a reinstall — the why is under [Secrets](#secrets);
delete the namespace to remove everything. Migrations apply forward; prefer
rolling forward to a fixed release — the full contract:
[docs/operations/upgrades.md](../operations/upgrades.md).

## Troubleshooting

| Symptom | Likely cause |
| ------- | ------------ |
| `postgresql` pod `Pending`, "unbound immediate PersistentVolumeClaims" | No default StorageClass — see Prerequisites |
| `broker` pod `ImagePullBackOff` | The broker image is not published — build it and push/load it where the cluster can pull (step 1) |
| Agent approved but token exchange fails `invalid_grant` | `JENTIC__AUTH__CANONICAL_BASE_URL` unset or differs from the registered `--url` (step 3) |
| Fresh install against an external Postgres has no tables | The migrate hook renders only on the bundled-DB path — run migrations yourself (External database) |
