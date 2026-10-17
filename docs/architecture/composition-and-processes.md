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
                      └─ uvicorn.run(app)
```

- **`__main__.py`** owns process concerns: config loading, observability
  setup, the `create-admin` first-run subcommand, and the guard that disables
  `server.reload` on a SQLite admin DB (a single-file DB cannot take multiple
  writer processes).
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

## The memory shared-state backend is bounded

> **Warning: the default `memory` shared-state backend is bounded and fails
> closed.**
> `broker.resilience.backend.backend: memory` keeps `Idempotency-Key`
> records, rate-limit buckets, circuit-breaker counters and the auth
> surface's single-use markers in the process, with a fixed budget per key
> namespace. It never evicts a live entry. When a namespace (or one caller's
> share of it) is full, the request that needed the write gets a **503**
> (`idempotency_unavailable` for an `Idempotency-Key` request) with a
> `Retry-After` of when its earliest entry expires.
>
> With the defaults (`broker.idempotency.ttl_s` unset, which is a 1 h replay
> window on memory and 24 h on redis; the `idem` budget of 128 MiB; about
> 1.2 KiB budgeted per record with typical response headers), one broker
> process sustains about **30 idempotent requests per second**, and about
> **3 per second per caller**, because each caller may hold at most 10% of
> the budget. Above that, `Idempotency-Key` requests get 503. Stored
> response bodies have their own 32 MiB `idem-body` budget. Past about
> 5 req/s of 1 KiB bodies, replays omit the body
> (`Jentic-Idempotency-Body-Omitted: true`) but requests are not refused.
> The ceiling is `min(max_entries, max_bytes / record_bytes) / ttl_s`, where
> `record_bytes` is about 1000 bytes plus the response headers. A record keeps
> at most 512 bytes of headers, `Content-Type`, `Location`, `ETag`,
> `Content-Location` and `Retry-After` first, so a replay may lack the
> others.
>
> Every other namespace defaults to 16 MiB, and all namespaces of one
> backend together are capped at
> `broker.resilience.backend.memory.max_total_bytes` (192 MiB), refused the
> same way. The budgets track real memory to within about 15% (measured
> with `tracemalloc`), so on the defaults the broker's state stays under
> about 192 MiB on top of a ~130 MiB process baseline, which the broker
> chart's 512Mi memory limit holds. The combined app runs up to three such
> backends (auth, broker, control) in one process.
>
> To raise it: raise `broker.resilience.backend.memory.namespaces.idem`
> (and its `max_owner_share`) together with `max_total_bytes`, or lower
> `broker.idempotency.ttl_s`. Raise the container memory limit by at least
> as much as you raise the budgets.

## Lifespan ordering

`create_surface_app` wires one lifespan per process, and teardown order is
load-bearing:

1. **Startup:** `ctx.startup()` → DB instrumentation → telemetry → the
   surface's own `extra_lifespan` (the broker opens its shared outbound
   `httpx.AsyncClient` here) → the invalidation bus starts (below) → the
   provider-config refresher starts → container-injected lifespans (the `/mcp` session manager) → background
   workers.
2. **Shutdown**, with a drain step first: the broker's admission gate
   reports unready (so the load balancer deregisters the instance) →
   scanners stop → the job worker drains → telemetry flushes → the
   provider-config refresher stops → `ctx.shutdown()` (closes the providers) → container-injected lifespans exit in reverse → the
   invalidation bus stops → the surface lifespan closes the shared HTTP
   client. Draining the worker
   *before* closing the client matters: an async execution mid-flight must
   finish over a live connection pool.

## Cross-process cache invalidation

The broker caches authorization state in-process — opaque-token and API-key
verdicts, permission-rule lists, credential-binding derivations — under short
TTLs. With several processes (a broker fleet, a separate control plane), a
change committed by one process must reach the others' caches. The
`InvalidationBus` seam
([`shared/invalidation/`](../../src/jentic_one/shared/invalidation/protocol.py))
carries that signal:

```mermaid
flowchart LR
    CP["control plane: revoke token"] -->|"UPDATE access_tokens (commit)"| PG[("PostgreSQL")]
    PG -->|"trigger: pg_notify on commit"| PG
    PG -->|"LISTEN jentic_one_invalidation"| B1["broker A: drop entry"]
    PG -->|"LISTEN jentic_one_invalidation"| B2["broker B: drop entry"]
```

- **Publishing is transactional and lives in the database.** `AFTER` row
  triggers on the admin tables (`access_tokens`, `agent_credentials`,
  `agents`, `users`, `actor_permission_grants`, `oauth_clients`,
  `oauth_client_grants`, `agent_credential_bindings`) and control tables
  (`agent_permission_rules`, `permission_rule_set_rules`, `credentials`) call
  `pg_notify` with a small JSON payload naming the table and the changed key
  values. `NOTIFY` is delivered only on commit, so a rolled-back change never
  invalidates anything, and every writer — any surface, any process, a manual
  `psql` session — publishes without application code. A payload that would
  exceed the `NOTIFY` size limit, and every `TRUNCATE`, sends a "flush this
  table" message instead; an `UPDATE` that changes only the `updated_at` /
  `last_used_at` bookkeeping timestamps sends nothing.
- **Listening is one dedicated connection per database server per process**,
  opened directly with asyncpg outside the SQLAlchemy pool (a `LISTEN` is
  session state). The bus opens it only when something subscribed — the broker
  and any process that resolves credential providers (below) do; a process
  serving only registry, admin or auth opens nothing. It is visible in
  `pg_stat_activity` as `application_name = 'jentic-one-invalidation'`.
- **A lost connection degrades to TTLs, never to stale forever.** The bus
  probes the connection, reconnects with capped exponential backoff
  (`invalidation.reconnect_backoff_*`), and after reconnecting flushes every
  subscribed cache, because notifications sent while it was down are gone.
  Startup waits a bounded time for the first `LISTEN`; a database that is
  slower than that does not hold up boot, and when its `LISTEN` lands later
  the bus flushes too (requests may have been cached in between).
- **SQLite runs a no-op bus**: there is no notification channel, and a
  single-file database has no second writer process. The TTLs apply. Set
  `invalidation.enabled: false` where `LISTEN` cannot work (a transaction-mode
  connection pooler) to get the same TTL-only behaviour on PostgreSQL.
- **Credential provider configs ride the same bus.** An admin write to a
  provider config (`PUT /admin/config/providers/{name}`) rebuilds the
  provider registry of the process that served it, and the trigger on
  `provider_configs` notifies every other process that resolves providers
  (control, the broker, a combined app) on the `admin.provider_configs`
  topic. Each one rebuilds its registry from the stored configs
  ([`shared/provider_refresh.py`](../../src/jentic_one/shared/provider_refresh.py)),
  so a rotated or removed client secret stops being used without a restart.
  The payload names the provider only — never the config or its ciphertext.
  Rebuilds are debounced and single-flighted (a burst of writes is one
  rebuild; a write landing mid-rebuild causes one more), a reconnect flush
  triggers one, and a failed rebuild is retried with backoff. Resolving a
  provider stays an in-memory lookup: the database is read only on a
  rebuild. A rebuild reuses every provider whose config is unchanged; a
  replaced provider stays usable by the requests that already resolved it
  and is closed (if it holds resources) after a grace period, or at shutdown.
  With a bus that never delivers (SQLite's single process, or
  `invalidation.enabled: false`) only the writing process rebuilds, and the
  others merge the stored configs at their next boot.
- **Pluggable.** `AppContainer.invalidation_bus` replaces the default
  (`build_invalidation_bus(ctx)`); the app factory stashes the chosen bus on
  `app.state.invalidation_bus` before installers run, so caches subscribe at
  build time, and starts and stops it in the lifespan. An alternative
  transport proves itself with `jentic_one.testing.BaseInvalidationBusContract`.

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
