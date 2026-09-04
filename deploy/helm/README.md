# Helm charts & the local cluster workflow

Two charts live here:

- [`jentic-one/`](jentic-one/) — the umbrella application chart: one subchart
  per service (`app`, `broker`, `registry`, `admin`, `control`), an nginx
  `gateway` for the parts topology, a bundled `postgresql` for dev/trial, and
  a `common` library chart of template helpers. Release-scoped resources
  (generated secrets, the migrate hook) live in its own `templates/`.
- [`observability/`](observability/) — a standalone LGTM stack
  (Grafana/Loki/Tempo/Prometheus + OTel collector) for local dev.

[`values/`](values/) holds the per-mode and per-overlay value files the local
workflow composes.

**Installing on a real cluster?** That journey is
[`docs/installation/helm.md`](../../docs/installation/helm.md) — including
the secrets model (generated vs your own). This page is the chart-development
and smoke-test workflow.

## Known gaps

- The chart is **not published** to any registry (the AWS Marketplace ECR
  copy is the exception — see
  [marketplace publishing](../../docs/development/marketplace-publishing.md)).
  Vendor [`deploy/helm/jentic-one/`](jentic-one/) from a checkout at the release tag.
- Subcharts default to locally-built `jentic-one/<svc>` image repos. Only the
  `app` subchart can be pointed at the published GHCR image
  (`--set app.image.repository=… --set app.image.tag=…`); the `broker`
  subchart doesn't set `JENTIC__APPS=broker` itself, so running it on the
  published app image needs `broker.extraEnv.JENTIC__APPS=broker` (the AWS
  Marketplace overlay does exactly that).

## Local cluster workflow

A [kind](https://kind.sigs.k8s.io/)-based local cluster validates that chart
installs work, pods become Ready, and health endpoints respond — without a
cloud environment.

### Prerequisites

- kind (v0.20+), Helm (v3.12+), `kubectl` (kind manages kubeconfig)
- Docker images built locally (`make build-all`)

### Mode matrix

| Mode       | Values file                                    | What runs                                        |
| ---------- | ---------------------------------------------- | ------------------------------------------------ |
| `combined` | [`values/local-combined.yaml`](values/local-combined.yaml) | app (all surfaces) + broker + PostgreSQL |
| `parts`    | [`values/local-parts.yaml`](values/local-parts.yaml)       | registry + admin + control + broker + gateway + PostgreSQL |
| `broker`   | [`values/local-broker.yaml`](values/local-broker.yaml)     | broker + PostgreSQL                      |

### Quick start

```bash
make build-all                                          # Build all Docker images
uv run python -m tools.deploy cluster up                # Create local kind cluster
uv run python -m tools.deploy up --mode combined        # Deploy with combined mode
curl http://localhost:8000/health                       # Verify app health
uv run python -m tools.deploy smoke                     # Run smoke test suite
uv run python -m tools.deploy down                      # Remove the release
uv run python -m tools.deploy cluster down              # Destroy the cluster
```

Add `--otel` to `up` to wire app OTel sidecars to the observability stack
(requires `obs up` first). To switch modes, `down` then `up --mode parts`.

Running a hosted Jentic install alongside this one? A client is bound to one
backend at a time — confirm which backend a base URL serves with
`curl http://localhost:8000/instance`
([local/remote coexistence](../../docs/development/context-and-config.md#local--remote-coexistence-which-backend-am-i-talking-to)).

### Image loading

`tools.deploy up` loads only the images the chosen `--mode` needs into the
kind cluster:

| `--mode`   | Images loaded                            |
| ---------- | ---------------------------------------- |
| `combined` | `app`, `broker`                          |
| `parts`    | `registry`, `admin`, `control`, `broker` |
| `broker`   | `broker`                                 |

The bundled PostgreSQL uses the official `postgres` image from Docker Hub —
nothing to build. To pre-warm every service image once (useful when switching
modes), run `uv run python -m tools.deploy load --all` after `cluster up`.

### Logs

```bash
uv run python -m tools.deploy logs app       # Tail app pod logs
uv run python -m tools.deploy logs broker    # Tail broker pod logs
```

### Port mappings (kind → localhost)

From [`deploy/k8s/kind-config.yaml`](../k8s/kind-config.yaml), kept in sync
with the `service.nodePort` values in the local overlays:

| Host port | Service (mode)              | Kind nodePort |
| --------- | --------------------------- | ------------- |
| 8000      | app (combined) / gateway (parts) | 30080    |
| 8080      | broker (all modes)          | 30081         |
| 8001      | admin (parts)               | 30082         |
| 8002      | control (parts)             | 30083         |
| 8003      | registry (parts)            | 30086         |

### Smoke tests in CI

The [`smoke-helm`](../../.github/workflows/smoke-helm.yml) workflow runs the
mode matrix via the same `tools.deploy ci-smoke` entrypoint on an ephemeral
kind cluster: post-merge on every push to `main` (path-filtered off doc-only
commits), as a **blocking release gate** (`release.yml` calls it on every
`vX.Y.Z` tag and refuses to publish until the matrix is green), and on
manual dispatch for a single mode.

## Cluster UI / observability

Two layers, deployed independently.

### Lightweight: k9s

```bash
brew install k9s
uv run python -m tools.deploy ui                # k9s scoped to the jentic namespace
```

A terminal UI — pods, services, logs, exec, port-forward. Fastest way to see
what's happening during `tools.deploy up`.

### Full LGTM stack: Grafana + Loki + Tempo + Prometheus

[`observability/`](observability/) deploys the full set into a `monitoring`
namespace, with two independent pipelines:

- **Traces + metrics** — apps emit OTLP from a sidecar to the central OTel
  Collector, which fans out to Tempo and Prometheus.
- **Logs** — a Grafana Alloy DaemonSet on each node tails
  `/var/log/pods/...` and pushes JSON-parsed lines (with `level`,
  `trace_id`, `span_id` extracted) directly to Loki. No app-side change; logs
  keep going to stdout.

```
app pod ─┐
broker  ─┼─ otel sidecar ──► obs-otelcollector ─┬─► Tempo  (traces)
…        ┘                                      └─► Prometheus (metrics)
                                                            ▼
node /var/log/pods ──► Alloy (DaemonSet) ─────► Loki ─► Grafana (UI)
```

The lanes meet only in Grafana, which correlates them by `trace_id` (the
Loki datasource's derived fields). Subchart deps come from the upstream
community charts (`grafana/loki`, `grafana/tempo`, `grafana-community/grafana`,
`prometheus-community/prometheus`, `open-telemetry/opentelemetry-collector`,
`grafana/alloy`).

```bash
uv run python -m tools.deploy obs up                    # Install into monitoring/
uv run python -m tools.deploy up --mode combined --otel # App sidecars point at obs-otelcollector
uv run python -m tools.deploy grafana                   # Port-forward Grafana to localhost:3000

uv run python -m tools.deploy obs down                  # Tear it down
```

Logs flow into Loki regardless of `--otel` (they come from Alloy, not the
sidecars). Default Grafana credentials are `admin` / `admin`; datasources for
Prometheus, Loki, and Tempo are pre-wired, with `Trace → logs` /
`Trace → metrics` links.

Knobs worth knowing:

- **Resource sizing** — defaults in
  [`values/local-observability.yaml`](values/local-observability.yaml) keep
  the stack under ~2 GiB on a single kind node; persistence is off.
- **No `--otel`** — apps run with no sidecar, no overhead; logs still flow.
- **Adding a component** — add a subchart to [`observability/Chart.yaml`](observability/Chart.yaml);
  it's a normal Helm umbrella.

This stack is for **local dev / smoke**. For production, run the same charts
against persistent storage (e.g. S3 for Tempo/Loki), behind real auth (OIDC
for Grafana), and consider kube-prometheus-stack for alertmanager +
node-exporter + service monitors.

## Observability hooks (application chart)

Subcharts include built-in support for structured logging and an OTel
sidecar; see [`_logging.tpl`](jentic-one/charts/common/templates/_logging.tpl)
and [`_otel-sidecar.tpl`](jentic-one/charts/common/templates/_otel-sidecar.tpl).

- `LOG_FORMAT` (default `json`), `LOG_LEVEL` (default `info`), and
  `OTEL_SERVICE_NAME` (`<release>-<chart>`, e.g. `jentic-broker`) are
  injected into every service pod.
- With `global.observability.otel.enabled=true`, each pod gets an OTel
  Collector sidecar receiving OTLP gRPC on `localhost:4317` and exporting to
  the configured endpoint:

```bash
helm install jentic deploy/helm/jentic-one \
  --set global.observability.otel.enabled=true \
  --set global.observability.otel.endpoint=http://otel-collector:4317
```

## Metrics exporter

The application emits metrics via the OpenTelemetry metrics SDK. The
exporter is chosen at deploy time
(`JENTIC__OBSERVABILITY__METRICS__EXPORTER` /
`global.observability.metrics.exporter`):

| Exporter     | How it works                                         | When to use                              |
| ------------ | ---------------------------------------------------- | ---------------------------------------- |
| `otlp`       | Pushes to OTel Collector on localhost:4317 (sidecar)  | Production / OTel-based stacks (default) |
| `prometheus` | Exposes `/metrics` endpoint on the service port       | Environments with Prometheus scraping    |
| `none`       | No-op — SDK inactive                                  | Tests, CI, local dev without obs stack   |

For Prometheus scraping, layer the annotation overlay — the observability
chart's Prometheus auto-discovers annotated pods:

```bash
uv run python -m tools.deploy up --mode combined
helm upgrade jentic deploy/helm/jentic-one \
  -f deploy/helm/values/local-combined.yaml \
  -f deploy/helm/values/local-prom-app.yaml
```

Adding custom instruments in app code? Go through `get_meter()` in
[`src/jentic_one/shared/metrics.py`](../../src/jentic_one/shared/metrics.py)
(enforced by [`tests/arch/test_metrics_facade.py`](../../tests/arch/test_metrics_facade.py)) and keep label cardinality
bounded — route templates and enums, never raw paths or user-supplied IDs.
