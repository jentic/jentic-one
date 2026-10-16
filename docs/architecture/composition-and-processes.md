# Composition and processes

How one Python package becomes the processes you deploy: what happens between
`python -m jentic_one` and a serving FastAPI app, which surfaces share a
process, and which background work rides along.

## Boot sequence

```
python -m jentic_one
  └─ __main__.py        load_config() → configure logging/tracing/metrics
       └─ Context(config, allowed_dbs=…)      # DB gate computed from JENTIC__APPS
            └─ wiring.build_default_container(ctx)   # cross-surface seams
                 └─ app factory                       # per-surface or combined
                      └─ uvicorn.run(app)             # server.workers: 1 (default)
```

- **`__main__.py`** owns process concerns: config loading, observability
  setup, the `create-admin` first-run subcommand, the guard that disables
  `server.reload` on a SQLite admin DB (a single-file DB cannot take multiple
  writer processes), and the multi-worker launch (see
  [Multiple worker processes](#multiple-worker-processes-serverworkers)).
- **`Context`** ([`shared/context.py`](../../src/jentic_one/shared/context.py)) carries config plus lazily-opened
  database handles, gated by `allowed_dbs` (below).
- **`wiring.py`** is the composition root (see
  [surfaces and layering](surfaces-and-layering.md)); it builds the
  `AppContainer` — the DI seam carrying an optional injected `Broker`, extra
  routers, extra installers, and extra lifespans. The enterprise overlay and
  other downstream packages extend the system here, not inside surfaces.
- **The app factories** ([`shared/web/app_factory.py`](../../src/jentic_one/shared/web/app_factory.py)) assemble either one
  standalone surface app (`SURFACE_MODULES[surface].create_app`) or a
  combined app that includes every selected surface's routers in one shared
  root namespace — only the per-surface health routers carry a prefix
  (`/registry/health`, `/control/health`, …); business routes such as
  `/apis` and `/credentials` are served unprefixed.

## `JENTIC__APPS` selects the surfaces

One image, one package, any shape: the comma-separated `JENTIC__APPS`
environment variable decides which surfaces a process serves. Two rules are
enforced at boot:

- **The broker runs alone.** `_build_app` raises if `broker` appears with any
  other surface. The data plane holds decrypted secrets in memory during
  injection; keeping it in its own process keeps that memory out of the
  control plane and lets the two scale independently.
- **Databases are gated per shape.** `Context` only opens the databases the
  surface set needs. Each surface implies its own DB, widened by
  `SURFACE_DB_DEPS` in `__main__.py`: `broker` needs all three (resolve
  against registry, credentials from control, audit into admin); `auth`
  needs admin + control; standalone `control`/`registry` need admin (their
  local auth verifier resolves API keys and permissions there); and a
  control shape with MCP enabled needs registry (the `/mcp` search tools
  call registry services in-process). Touching a DB outside the gate raises
  immediately, so a mis-deployed process fails at boot.

## Deployment topologies

```mermaid
flowchart TB
    subgraph combined [Combined — the default]
        A1["app: JENTIC__APPS=registry,admin,control,auth"]
        B1["broker: JENTIC__APPS=broker"]
        D1[("registry / control / admin")]
        A1 --- D1
        B1 --- D1
    end

    subgraph parts [Parts — one process per surface]
        R2["registry"]
        C2["control (+ /mcp)"]
        M2["admin (+ SPA)"]
        U2["auth"]
        B2["broker"]
        D2[("registry / control / admin")]
        R2 --- D2
        C2 --- D2
        M2 --- D2
        U2 --- D2
        B2 --- D2
    end

    subgraph edge [Broker-scaled — hot data plane]
        A3["app (all four control-plane surfaces)"]
        B3a["broker ×N (stateless, scale horizontally)"]
        D3[("shared databases + Redis state backend")]
        A3 --- D3
        B3a --- D3
    end
```

- **Combined** — two containers: everything except the broker in one
  process, the broker in the other. What
  [`deploy/`](../../deploy/README.md)'s compose files and the Helm chart's
  default values produce.
- **Parts** — each surface standalone, fronted by a gateway that routes by
  path prefix. Standalone apps serve at root (`/health`, not
  `/registry/health`); the combined app keeps the per-surface health
  prefixes so the probes don't collide. Agent-discovery documents
  (`/llms.txt`, `/skills/*`) are mounted on every standalone surface so a
  split deployment still serves them.
- **Broker-scaled** — brokers are stateless; run several behind a load
  balancer. Set `broker.resilience.backend.backend` to `redis` (and give
  every replica the same `broker.resilience.backend.redis_url`) so rate
  limits, circuit breakers, and idempotency records are shared across
  replicas. The redis client is an optional extra
  (`pip install jentic-one[redis]`), not part of the published container
  image — scaling out on that image means building your own with the extra.

## Multiple worker processes (`server.workers`)

By default `python -m jentic_one` serves from one process, so a broker (or
app) container uses at most one CPU core. Set `server.workers`
(`JENTIC__SERVER__WORKERS`) above 1 to have uvicorn run that many worker
processes behind one listening socket:

```
python -m jentic_one          # supervisor: load_config() + validation only
  └─ uvicorn.run("jentic_one.__main__:create_app", factory=True, workers=N)
       ├─ worker 1: create_app() → load_config() → Context → app → lifespan
       ├─ worker 2: …
       └─ worker N: …
```

The supervisor builds no app. Each worker is a spawned process that re-imports
`jentic_one.__main__` and builds its own app through `create_app`, re-loading
config from the config file (`JENTIC_CONFIG_FILE`) and `JENTIC__*` environment
it inherits. Config therefore has to be re-readable: nothing set in the
supervisor's memory reaches a worker. The bind (`server.host` /
`server.port`) is the same in both modes; no other uvicorn option is set.

What `server.workers > 1` refuses, and why:

| Refused | Where | Why |
| ------- | ----- | --- |
| Any database on the `sqlite` backend | config validation | One SQLite file serialises writers behind a single lock; N processes of request handlers and job pollers contend into `database is locked`, and an in-memory SQLite DB is not shared at all. Use PostgreSQL. |
| `server.reload: true` | config validation | uvicorn's reloader supervises exactly one worker. |
| An unset `admin.auth.jwt_secret`, `admin.invite.pepper` or `credentials.connect.state_secret` that a surface of the process reads | config validation | Outside production an unset secret is generated per process, so each worker would mint its own: a session JWT, invite token or connect `state` signed by one worker would fail on the next. A broker reads none of the three, so it is not affected. |
| A one-shot config source (`JENTIC_CONFIG_FILE` on a pipe or `/dev/fd`) | `_serve`, before workers start | The supervisor drained it; the workers cannot re-read it. |

Each worker runs the full lifespan, so everything a process owns is
multiplied by N. That is the same model as running N replicas with one
worker each, and these are its consequences:

- **Background loops run in every worker.** Each worker's `WorkerLoop`
  polls for jobs. Claiming is safe under concurrent pollers on PostgreSQL:
  the claim is a single `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP
  LOCKED LIMIT 1) RETURNING`, so two pollers never claim the same job
  and a job left `RUNNING` by a dead worker is reclaimed after its
  visibility deadline. The scanners are already replica-safe: the
  credential-expiry scanner dedupes on persistent markers, the connect-poll
  scanner leases rows with `SKIP LOCKED`, and the catalog update scanner
  de-phases its sweeps (a rare duplicate `catalog.update_available` event is
  tolerated, as it is across replicas). With telemetry on, every worker
  emits its own `instance_booted` event, so one start reports N boots.
- **Metrics are per process.** There is no multiprocess aggregation. With
  `observability.metrics.exporter: prometheus`, a `/metrics/` scrape is
  answered by whichever worker accepts the connection and shows only that
  worker's series, so counters appear to jump between scrapes (the
  supervisor logs `prometheus_metrics_per_worker` at start). Use the `otlp`
  exporter (the OpenTelemetry SDK gives each process its own
  `service.instance.id`, so worker series stay distinct at the collector),
  or run one worker per container and scale replicas.
- **Limits and pools are per process.** `broker.resilience.max_in_flight`,
  the readiness saturation threshold, the outbound HTTP pool and each
  database's `pool_max` apply per worker: a broker with
  `server.workers: 4` admits 4× `max_in_flight` and opens up to
  4× `pool_max` connections to each database. Size PostgreSQL's
  `max_connections` (or a pooler) for it.
- **In-memory state is per process.** On the default `memory` shared-state
  backend, broker rate limits, circuit breakers and `Idempotency-Key`
  replay, and the auth surface's OAuth rate limits and consent anti-replay,
  are held per worker (the supervisor logs
  `shared_state_backend_memory_per_worker`): limits multiply by N and a
  replayed key can reach a worker that has not seen it. Set
  `broker.resilience.backend.backend: redis` to share them. A few limiters
  are memory-only by design and stay per worker even on Redis: the
  connect-flow per-actor limit (route and MCP tool), the MCP registration
  throttle, and the JWT-bearer assertion `jti` replay cache.
- **Caches are per process.** The broker's token/API-key resolve cache,
  its credential-derivation and rule caches, and their single-flight
  coalescing are per worker, so a cold miss costs up to one lookup per
  worker and a revocation is bounded by each worker's cache TTL — the
  same as across replicas. The DB-backed provider registry is refreshed
  only in the worker that served the admin write (see `Context.refresh_providers`);
  other workers pick up a changed provider config on their next restart.
- **What is shared.** Anything in the database: job claims, login lockout
  (`failed_login_count` / `locked_until`), tokens, sessions and audit.
  MCP's `/mcp` endpoint is stateless, so it needs no session affinity.

## Lifespan ordering

`create_surface_app` wires one lifespan per process, and teardown order is
load-bearing:

1. **Startup:** `ctx.startup()` → DB instrumentation → telemetry → the
   surface's own `extra_lifespan` (the broker opens its shared outbound
   `httpx.AsyncClient` here) → container-injected lifespans (the `/mcp`
   session manager) → background workers.
2. **Shutdown**, with a drain step first: the broker's admission gate
   reports unready (so the load balancer deregisters the instance) →
   scanners stop → the job worker drains → telemetry flushes →
   `ctx.shutdown()` → container-injected lifespans exit in reverse → the
   surface lifespan closes the shared HTTP client. Draining the worker
   *before* closing the client matters: an async execution mid-flight must
   finish over a live connection pool.

## Background work

Four loops start inside the lifespan:

| Loop | Runs when | Job |
| ---- | --------- | --- |
| `WorkerLoop` ([`shared/jobs/worker.py`](../../src/jentic_one/shared/jobs/worker.py)) | the admin DB is reachable and a handler registered | Claims queued jobs from the admin DB's jobs table. The `IMPORT` handler registers only on registry shapes; the `EXECUTION` handler only where the broker's upstream executor exists. A broker process therefore never claims import jobs, and an app process never claims executions. |
| `CredentialExpiryScanner` | control shapes | Emits expiry warnings for credentials nearing their end date. |
| `CatalogUpdateScanner` | registry shapes | Watches imported APIs' sources for upstream changes and raises update-available notifications. |
| `TelemetryFlushLoop` ([`shared/telemetry/loop.py`](../../src/jentic_one/shared/telemetry/loop.py)) | `telemetry.enabled` (off by default in code, on in the shipped install examples) and the admin DB is reachable | Drains the telemetry queue and POSTs event batches (the catalog scanner also egresses, to the tracked spec sources); drains once more on shutdown. |

The gate is the surface set (`enabled_apps`), not which DBs happen to be
reachable: the broker is granted the registry DB for its synchronous resolve
path, but it is not the registry surface, so it must not race the control
plane for import jobs.

## Related

- [Broker execution](broker-execution.md) — what the broker process does
  with the resources this lifespan hands it.
- [Data model](data-model.md) — the three databases the gate above controls.
- [`docs/installation/helm.md`](../installation/helm.md) and
  [`deploy/README.md`](../../deploy/README.md) — how these shapes are
  expressed in Helm values and compose files.
