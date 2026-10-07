# Installing with Helm

The Helm chart at
[`deploy/helm/jentic-one/`](../../deploy/helm/jentic-one/) is an umbrella
chart with one subchart per service (`app`, `broker`, `registry`, `admin`,
`control`) plus an optional bundled PostgreSQL. The chart can be zero-touch:
with generated secrets enabled, every secret (credential-encryption keyset,
JWT signing secret, database passwords) is created on first install and
reused on every upgrade, and migrations run as a Helm hook.

The chart is smoke-tested in CI on kind, in all modes, post-merge and as a
release gate. Two constraints to know up front (the
[chart docs](../../deploy/helm/README.md#known-gaps) carry the full gap
list):

- The chart is **not published** to any registry. Vendor
  [`deploy/helm/jentic-one/`](../../deploy/helm/jentic-one/) from a checkout of this repository **at the
  release tag** you are deploying.
- Only one image is published to GHCR: `ghcr.io/jentic/jentic-one-app`. It
  runs the broker too — point `broker.image.repository` at it and set
  `broker.extraEnv.JENTIC__APPS=broker` (the broker subchart does not set
  that itself; the AWS Marketplace overlay uses exactly this shape). The
  alternative is building your own images (`make build-all`) and pushing
  them where your cluster can pull.

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
  The data PVC defaults to **8Gi** (`postgresql.persistence.size`) and is
  created through the StatefulSet's `volumeClaimTemplates`, so the size is
  **immutable after first install** — resizing means expanding the PVC by
  hand (if the StorageClass allows it) or a dump/restore into a new
  release. Size it before first install.

## 1. Get the images

**Published image (no build):** `ghcr.io/jentic/jentic-one-app` serves both
the `app` and `broker` subcharts — the install command in step 2 points both
at it. [Verify the signature](docker.md#1-pull-and-verify-the-image) before
mirroring it into an internal registry.

**Build your own** from the checkout at the release tag:

```bash
make build-all    # builds jentic-one/{app,broker,registry,admin,control}
make save-all     # writes build/jentic-<svc>-<version>.tar for offline transfer
```

The combined topology needs only `app` and `broker`. For a remote cluster,
`docker load -i` the tarballs, retag, and push to your internal registry,
then set each enabled service's `<svc>.image.repository` to the pushed name
(the subchart defaults, `jentic-one/<svc>`, resolve only for locally-built
images on a local cluster).

## 2. Install

### Local dev cluster (kind) — one flow

The repo's own tooling creates the cluster, loads the images, and installs
the chart with the dev values
([`deploy/helm/values/local-combined.yaml`](../../deploy/helm/values/local-combined.yaml) —
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
run as a post-install hook on first install and a **pre-upgrade** hook
thereafter.

```bash
helm install jentic ./deploy/helm/jentic-one \
  --namespace jentic-one --create-namespace \
  --timeout 30m \
  --set global.appSecrets.generate=true \
  --set postgresql.enabled=true \
  --set global.postgresql.enabled=true \
  --set broker.enabled=true \
  --set app.image.repository=ghcr.io/jentic/jentic-one-app \
  --set broker.image.repository=ghcr.io/jentic/jentic-one-app \
  --set broker.extraEnv.JENTIC__APPS=broker \
  --set global.image.tag=X.Y.Z
```

(`broker.enabled=true` is required — the umbrella chart ships the broker off.
Both services run the one published image; `JENTIC__APPS=broker` makes the
second one the broker. Every service pod gets `JENTIC_ENV=production` from
`global.jenticEnv`, which is load-bearing: in development mode the config layer
silently generates missing secrets per-process instead of refusing to boot, so
sessions die on every pod restart and the surfaces disagree with no error. Only
set `global.jenticEnv=development` for a throwaway cluster that supplies none of
the secrets. `global.image.tag` pins every subchart's tag in one place; omit it
and each subchart falls back to its own `appVersion`, which matches the chart
version you vendored. `--timeout 30m` matters because the
migrate hook runs *inside* Helm's timeout — the default is 5 minutes, after
which Helm marks the release failed while the Job keeps running; the next
`helm upgrade` retry then deletes the still-running hook Job mid-migration
(`before-hook-creation`), leaving a half-applied schema (see
[upgrades.md](../operations/upgrades.md)). Set `<svc>.image.repository`
explicitly for **every enabled service**:
each subchart ships a non-empty local-build default (`jentic-one/<svc>`), and
`global.image.registry` applies only to services whose `image.repository` is
empty — so on its own it is a no-op and the pods go `ImagePullBackOff` on the
unqualified default. Self-built images: point the two repositories at your
internal registry's `…/app` and `…/broker` instead and drop the
`JENTIC__APPS` override.)

## 3. Set the canonical base URL

Required before agents can connect. Set it to the URL your agents will reach
the app at (your ingress or load-balancer URL). Agent token exchange compares
this value byte-for-byte against the `--url` agents register with — a
mismatch (including `localhost` vs `127.0.0.1`) fails with `invalid_grant`
*after* the agent is approved:

```bash
helm upgrade jentic ./deploy/helm/jentic-one --reuse-values --timeout 30m \
  --set app.extraEnv.JENTIC__AUTH__CANONICAL_BASE_URL=https://jentic.example.com
```

(The kind dev values already set it to `http://localhost:8000`.)

## 4. Create the first admin, then verify

Create the admin **before** you expose the app through an ingress:
`POST /users:create-admin` is unauthenticated by design and self-closes only
once the first user exists, so on the Helm path — where pods serve before
any admin exists — racing it against public exposure is an avoidable risk.
The app pod already carries the database env, so run the one-shot inside it
(re-running is safe: `setup already complete`):

```bash
read -rs ADMIN_PASSWORD   # run this line by itself; it waits for input
printf '%s\n' "$ADMIN_PASSWORD" | kubectl -n jentic-one exec -i deploy/jentic-app -- \
  python -m jentic_one create-admin --email admin@example.com
```

Alternatively, open the one-time `/app/setup` page while the app is still
port-forward-only.

Then verify — note `/health` is dependency-free (it stays green with the
database down or empty, [monitoring.md](../operations/monitoring.md#health)),
so check the pieces that can actually be wrong:

```bash
kubectl -n jentic-one get pods
# expect: app, broker (+ postgresql on the bundled path) — all Running

kubectl -n jentic-one port-forward svc/jentic-app 8000:8000 &
curl -s http://localhost:8000/health         # process liveness only
curl -s http://localhost:8000/admin/health   # expect setup_required: false — proves the admin exists

# Migrations actually applied? (the one check /health cannot make) — the app
# pod already carries the database env, so run the check inside it:
kubectl -n jentic-one exec deploy/jentic-app -- \
  python -m jentic_one.migrations.run --check   # expect OVERALL current

kubectl -n jentic-one port-forward svc/jentic-broker 8100:8000 &
curl -s http://localhost:8100/health         # the broker answers too
```

Then connect an agent — note `register` needs the URLs agents will actually
use, so bring your ingress up first (below) rather than registering against
a port-forward `localhost` URL:

```bash
jentic register --url <app URL> --broker-url <broker URL>
```

Both services speak plain HTTP on port 8000 in-cluster — terminate TLS at your
ingress, routing UI/control traffic to the `app` Service and execution traffic
to the `broker` Service. Agents need both URLs.

The chart can render the Ingress for you (`ingress.enabled=true`, off by
default). It routes to the release's HTTP entry point — the `app` Service in
this combined shape — so the broker needs a second host naming its own service:

```bash
helm upgrade jentic ./deploy/helm/jentic-one … \
  --set ingress.enabled=true \
  --set ingress.className=nginx \
  --set ingress.hosts[0].host=jentic.example.com \
  --set ingress.hosts[1].host=broker.example.com \
  --set ingress.hosts[1].paths[0].path=/ \
  --set ingress.hosts[1].paths[0].pathType=Prefix \
  --set ingress.hosts[1].paths[0].service=broker \
  --set app.extraEnv.JENTIC__AUTH__OAUTH_RATE_LIMIT__TRUSTED_PROXIES=10.0.0.0/8 \
  --set ingress.tls[0].secretName=jentic-tls \
  --set ingress.tls[0].hosts[0]=jentic.example.com
```

That last `extraEnv` is **required**, and the install fails without it. The
pre-auth OAuth rate limiter keys on the client IP, so behind an ingress every
request arrives from the controller's address and all clients share one bucket —
a single noisy caller then rate-limits your whole fleet out of `/authorize`. Set
it to the ingress controller's pod CIDR or IPs (or
`ingress.skipTrustedProxiesCheck=true` if you enforce rate limits upstream of
the cluster). Bringing your own Ingress manifest instead works the same way:
point it at the `<release>-app` and `<release>-broker` Services on port 8000,
and set the same key. Then walk through
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
  `python -m jentic_one.migrations.run` yourself before first start **and on
  every upgrade** — the one-shot container from the
  [Docker guide, step 4](docker.md#4-run-migrations) works from any machine
  that reaches the database, or run it in-cluster as a one-off Job. Minimal
  manifest (same image, command, and env-var names as the chart's own
  migrate hook; the passwords ride `envFrom` a Secret you manage whose keys
  are the `JENTIC__DATABASES__*__PASSWORD` variables — the same set as the
  [Docker guide, step 2](docker.md#2-write-the-config)):

  ```yaml
  apiVersion: batch/v1
  kind: Job
  metadata:
    name: jentic-migrate-X.Y.Z   # version-suffixed: Jobs are immutable, and this re-runs every upgrade
    namespace: jentic-one
  spec:
    backoffLimit: 3
    template:
      spec:
        restartPolicy: Never
        containers:
          - name: migrate
            image: ghcr.io/jentic/jentic-one-app:X.Y.Z
            command: ["python", "-m", "jentic_one.migrations.run"]
            envFrom:
              - secretRef:
                  name: jentic-db-credentials   # JENTIC__DATABASES__{REGISTRY,CONTROL,ADMIN}__PASSWORD
            env:
              - { name: JENTIC__DATABASES__REGISTRY__HOST, value: db.prod.internal }
              - { name: JENTIC__DATABASES__REGISTRY__NAME, value: jentic }
              - { name: JENTIC__DATABASES__REGISTRY__USER, value: registry_user }
              - { name: JENTIC__DATABASES__REGISTRY__SCHEMA_NAME, value: registry }
              # …repeat HOST/NAME/USER/SCHEMA_NAME for CONTROL and ADMIN
  ```
- **First admin:** same as [step 4](#4-create-the-first-admin-then-verify)
  — run the `create-admin` one-shot inside the app pod. Re-running is
  safe (`setup already complete`).
- **Secrets:** database passwords must match the roles you created. The
  chart supports exactly two password sources on this path: explicit
  `global.databases.*.password` values (always win, but live in a values
  file — dev-grade), or a Secret mounted via
  `global.appSecrets.existingSecret` that carries the three
  `db-password-{registry,control,admin}` keys — the pods consume those via
  `secretKeyRef` whenever the explicit value is unset. If you do put a
  password in a values file, **quote it**: an all-digit password unquoted
  is a YAML *number* (`0123456789` renders as `1.23456789e+08`) and
  authentication fails inscrutably. See
  [Secrets](#secrets) below. The mandatory set is the same as the
  [Docker guide, step 2](docker.md#2-write-the-config).

## Secrets

Four config values have no safe default: the credential-encryption keyset
(`credentials.encryption` — a *list*, so it cannot ride the flat `JENTIC__*`
env convention; credential writes fail without it), the admin JWT secret, the
invite pepper, and the connect state secret. The latter three ship a
placeholder that `JENTIC_ENV=production` refuses to boot with on every surface
that reads them; the keyset ships nothing at all. On the bundled-DB path
the same Secret also carries the database passwords. (A fifth value,
`auth.id_signing`, is needed only for `openid`-scope flows — nothing
generates it, not even the chart's generated Secret; carry it in the
`existingSecret`'s `config.yaml` (single layout) when you use OIDC/MCP
interactive sign-in — see the [Docker guide's worked
config](docker.md#2-write-the-config).)

### Which surface gets which secret

By default each surface is handed only the secrets its code reads
(`global.appSecrets.layout: split`), one Secret key per concern:

| Secret key | Config field | Reaches the pod as | app | admin | control | registry | broker |
| ---------- | ------------ | ------------------ | :-: | :---: | :-----: | :------: | :----: |
| `credentials-encryption.yaml` | `credentials.encryption` | file, `JENTIC_CONFIG_FILE` | yes | yes | yes | — | yes |
| `admin-jwt-secret` | `admin.auth.jwt_secret` | env (`secretKeyRef`) | yes | yes | yes | yes | — |
| `admin-invite-pepper` | `admin.invite.pepper` | env (`secretKeyRef`) | yes | yes | — | — | — |
| `connect-state-secret` | `credentials.connect.state_secret` | env (`secretKeyRef`) | yes | — | yes | — | — |

Admin (which also hosts the auth surface) signs admin/session JWTs; control
and registry verify them, so they need the same HS256 secret. Admin reads the
keyset to encrypt provider-config client secrets. The broker authenticates
with its own `broker.jwt_secret` / trusted issuers, never the admin JWT
secret. The config loader's production guard follows the same table: a
standalone surface only fails its boot when a secret *it reads* is missing.

The columns are the app surfaces each subchart's image runs (its baked
`JENTIC__APPS`). A `<svc>.extraEnv.JENTIC__APPS` override changes that, and
the chart follows it: the pod gets the union of what the listed surfaces
read, and every secret if the list names a surface the chart does not know.
So the published app image re-roled with `broker.extraEnv.JENTIC__APPS=broker`
gets only the keyset.

The secret env vars the chart sets on each pod:

| Env var | Secret key |
| ------- | ---------- |
| `JENTIC_CONFIG_FILE` | `/etc/jentic/app-secrets/credentials-encryption.yaml` (the mounted `credentials-encryption.yaml` key) |
| `JENTIC__ADMIN__AUTH__JWT_SECRET` | `admin-jwt-secret` |
| `JENTIC__ADMIN__INVITE__PEPPER` | `admin-invite-pepper` |
| `JENTIC__CREDENTIALS__CONNECT__STATE_SECRET` | `connect-state-secret` |

In the `single` layout only `JENTIC_CONFIG_FILE` is set, pointing at
`/etc/jentic/app-secrets/config.yaml`.

The legacy layout (`global.appSecrets.layout: single`) mounts one
`config.yaml` key holding everything on every surface — the only layout
chart versions before this one supported, and still the default for an
`existingSecret` (see below).

### Sources

The chart offers three sources, in order of preference:

1. **`global.appSecrets.generate: true`** — the chart mints random values
   into a release-scoped Secret (`<release>-app-secrets`) on first install
   and **reuses each key verbatim on every upgrade** (regenerating would
   orphan everything already encrypted, revoke every live session, and break
   DB logins). The Secret carries `helm.sh/resource-policy: keep`, so
   `helm uninstall` leaves it behind and a same-name reinstall re-adopts it.
   It holds the per-concern keys above **and** a legacy `config.yaml` with
   the same values (read only by `layout: single` and by older chart
   versions after a rollback). Caveat: piping `helm template` to
   `kubectl apply` bypasses the lookup and **will** rotate the secrets — use
   `helm install`/`upgrade`.
2. **`global.appSecrets.existingSecret: <name>`** — mount your own Secret
   (SealedSecrets, External Secrets Operator, …). Two shapes:
   - **Single layout** (the default for `existingSecret`): a `config.yaml`
     key shaped like the keyset block in the
     [worked config](docker.md#2-write-the-config), plus
     `admin.auth.jwt_secret`, `admin.invite.pepper`, and
     `credentials.connect.state_secret` — mounted on every surface.
   - **Split layout** (`global.appSecrets.layout: split`): the four
     per-concern keys from the table above. `credentials-encryption.yaml` is
     a config document holding only `credentials.encryption` (same shape as
     in the worked config); the other three hold the bare secret value.

   Either way it must **also** hold the three
   `db-password-{registry,control,admin}` keys unless you set every
   `global.databases.*.password` explicitly — the pods reference those keys
   whenever app-secrets is active and the explicit value is unset,
   *regardless* of whether the bundled Postgres is enabled, and a missing
   key is `CreateContainerConfigError` on every app/broker pod. (A fourth
   key, `db-password-postgres`, is consumed only by the bundled Postgres.)
3. **Per-service `configFile.contents`** (dev overlays only) — inlines
   secrets into a plain ConfigMap; never for real data. Mutually exclusive
   with the two modes above on any surface that mounts the secrets file
   (both claim `JENTIC_CONFIG_FILE`; the chart fails the render rather than
   silently preferring one). In the split layout the registry mounts no
   file, so it may still use `configFile`.

**`extraEnv` wins over the chart's own env.** `<svc>.extraEnv` renders after
every env var the chart sets, and Kubernetes resolves a duplicate name to the
last entry, so an operator value always takes effect. For the secret env vars
above (and `JENTIC_ENV`) the chart goes further and drops its own entry when
`extraEnv` sets the same name, so the pod spec holds a single, unambiguous
entry: `--set registry.extraEnv.JENTIC__ADMIN__AUTH__JWT_SECRET=…` replaces
the `secretKeyRef` on that surface. An `extraEnv` value is a plain string in
your values, though, so use it for secrets only in dev.

### Upgrading to the per-surface layout

- **`generate: true`** — nothing to do. The first `helm upgrade` onto this
  chart version reads the existing `config.yaml` and writes the per-concern
  keys from it (same values — nothing rotates, stored credentials stay
  decryptable, sessions stay valid), then each pod rolls onto only its own
  keys. If that `config.yaml` was hand-edited to carry settings beyond the
  four generated secrets, the upgrade stops with an error naming them. Either
  set `global.appSecrets.layout: single` to keep the old mount, or move those
  settings to `extraEnv` and delete them from the Secret's `config.yaml`. The
  check runs on every split render, so a release kept on `single` for that
  reason cannot later fall back to `split` and silently lose them; keep
  `layout: single` in the values you upgrade with (`--reuse-values` does).
  Rolling back to an older chart keeps working: the legacy `config.yaml` key
  stays in the Secret with the values the pods use. In `split` the chart
  rewrites it from the per-concern keys on every upgrade; in `single` it is
  left verbatim and the per-concern keys follow it.
- **`existingSecret`** — nothing changes until you opt in. To switch, add the
  four per-concern keys to your Secret (copy the values out of its
  `config.yaml` — **the same values**, or stored credentials become
  undecryptable), then upgrade with `global.appSecrets.layout: split`. Keep
  `config.yaml` in the Secret until you no longer need to roll back.
- The chart and the application image must come from the same release: the
  config loader's surface-aware production guard is what lets a standalone
  broker or registry boot without the secrets it no longer receives.

For external-database passwords there is no per-variable `secretKeyRef`
passthrough: `extraEnv` renders name/value scalars only (a nested
`valueFrom` map renders as a stringified value). A plain `extraEnv` value for
`JENTIC__DATABASES__<DB>__PASSWORD` does override the chart's (extraEnv wins,
see above), but it puts the password in your values. Keep passwords out of values files by carrying them as
the `db-password-*` keys of the `existingSecret` above; anything fancier
(ExternalSecrets per variable, CSI volumes) means patching the subchart
templates. Host/port/name/schema are not secrets — plain values are fine
for those.

Encryption-key **rotation** is a config-level operation in every mode: add a
new keyset entry and flip `active_id`. Stored secrets re-encrypt under the
new key only when they are rewritten — there is no bulk re-encrypt and no
completion check — so keep retired keys in the keyset (in the split layout
the keyset lives in the `credentials-encryption.yaml` key: edit it there,
then run a `helm upgrade`, which copies it into the legacy `config.yaml` so a
later rollback still decrypts what was written under the new key)
([upgrades.md](../operations/upgrades.md#what-an-upgrade-never-does)).

## Scaling and HA

Every subchart exposes `replicas` and `resources` in its values
(`--set broker.replicas=3`). The defaults are sized for evaluation, not
load: broker/registry/control request 100m/128Mi with a 500m/256Mi limit,
and app/admin request 256Mi with a 1Gi limit. The app's floor is real, not
a hint: it boots at roughly 150Mi RSS, and every concurrent password or
OAuth client-secret verification adds 64Mi (argon2id with
`memory_cost=65536`) — so a handful of simultaneous logins on a 256Mi limit
is an OOM-kill, not a slowdown. Do not set the app's memory limit below
512Mi.

- **Broker** — stateless; run several replicas behind the Service. First set
  the shared-state backend to Redis so rate limits, circuit breakers, and
  idempotency records are shared across replicas: with the default `memory`
  backend that state is per-process
  ([config/production.yaml.example](../../config/production.yaml.example),
  [composition-and-processes.md](../architecture/composition-and-processes.md)).
  The keys are `broker.resilience.backend.backend: redis` and
  `broker.resilience.backend.redis_url` (via `extraEnv`:
  `JENTIC__BROKER__RESILIENCE__BACKEND__BACKEND` /
  `…__REDIS_URL`, identical on every replica). The chart does not bundle
  Redis — bring your own. **One hard caveat:** the Redis client is an
  optional extra (`pip install jentic-one[redis]`) that the published
  `ghcr.io/jentic/jentic-one-app` image does **not** include — configuring
  the redis backend on that image crash-loops every replica at startup. On
  the published image today, keep `broker.replicas: 1`; scaling out means
  building your own image with the `[redis]` extra.
- **App** — the same shared-state backend also carries the auth surface's
  OAuth rate limiters and consent-nonce anti-replay, so the same rule
  applies: keep `app.replicas: 1` on the `memory` backend; configure Redis
  before scaling out. Multi-replica app surfaces are not part of the CI
  smoke matrix.
- **Bundled PostgreSQL** — a single-instance StatefulSet, dev/eval-grade by
  design; HA means an external managed database (below). Its connection ceiling
  is already raised to 200 (`postgresql.maxConnections`) because three pools per
  process exhausts the image's default of 100 well before you run out of CPU;
  raising replicas means raising this and the subchart's memory limit together.
- **PodDisruptionBudgets** — `podDisruptionBudget.enabled=true` renders one per
  enabled surface at `minAvailable: 1`, so node drains and cluster upgrades
  cannot take a surface fully offline. Turn it on only *after* raising
  `replicas` above 1: with a single replica there is no spare pod to evict and
  the budget blocks every drain instead.
- **NetworkPolicy** — `networkPolicy.enabled=true` limits ingress to
  same-release pods plus the namespaces in `networkPolicy.allowFromNamespaces`.
  Put your ingress controller's namespace in that list unless it runs in this
  release's namespace: the policy denies it otherwise and every route answers
  502. The bundled database gets a policy of its own that admits **only** this
  release's pods — `allowFromNamespaces` does not widen it, so a backup or DBA
  workload elsewhere needs its own policy (Kubernetes unions policies selecting
  the same pod, so yours adds to the chart's). Egress is a separate opt-in
  (`restrictEgress`) and never applies to the broker, whose whole function is
  calling third-party APIs. All of it is a no-op on a cluster whose CNI does not
  enforce policies — check yours before relying on it.

## Observability

Each pod can run an OpenTelemetry Collector sidecar:

```bash
helm upgrade jentic ./deploy/helm/jentic-one --reuse-values --timeout 30m \
  --set global.observability.otel.enabled=true \
  --set global.observability.otel.endpoint=http://otel-collector:4317
```

Metrics exporter selection (`otlp` / `prometheus` / `none`) and the scrape
annotations are covered in the
[chart docs](../../deploy/helm/README.md#metrics-exporter).

## Upgrading

0. Take a [backup](../operations/backup-restore.md) — it is the rollback.
1. Get (or build and push) the new release's images (step 1) and re-vendor
   the chart at the **new** release tag.
2. `helm upgrade jentic ./deploy/helm/jentic-one --timeout 30m …` with the
   new `global.image.tag` — and **every `--set` from your install**: a plain
   `helm upgrade` (no `--reuse-values`) resets everything else to chart
   defaults, which flips `broker.enabled` back off and, on the external-DB
   shape, re-enables the bundled Postgres. `helm get values jentic` prints
   what the release currently runs with; re-pass all of it (or use
   `--reuse-values` when you're changing nothing but the tag). On the
   bundled-DB path the migrate hook re-runs automatically — inside
   `--timeout`, hence the explicit value; the Helm default of 5 minutes can
   `SIGTERM` a long migration mid-run on a populated database. The hook is
   `pre-upgrade`, so the migration completes **before** the Deployments roll:
   new pods never serve against the old schema, and a failed migration aborts
   the upgrade with the old pods still running the schema they were built for.
   Budget for that: on a populated database the release is unavailable for the
   length of the migration, so upgrade in a window. Against an external
   database, re-run migrations first (see above).

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
| `broker` pod `ImagePullBackOff` | `broker.image.repository` still the local-build default (`jentic-one/broker`) — point it at the published image + `JENTIC__APPS=broker`, or at an image you built and pushed (steps 1–2) |
| Agent approved but token exchange fails `invalid_grant` | `JENTIC__AUTH__CANONICAL_BASE_URL` unset or differs from the registered `--url` (step 3) |
| Fresh install against an external Postgres has no tables | The migrate hook renders only on the bundled-DB path — run migrations yourself (External database) |
| `FATAL: sorry, too many clients already` (Postgres log), apps intermittently failing to connect | Each app/broker process opens a pool into all three databases (~30 connections). The chart sets `postgresql.maxConnections: 200`, which covers every bundled topology with headroom — raise it (and `postgresql.resources.limits.memory` with it) if you scale replicas up, or front an external database with a connection pooler |
