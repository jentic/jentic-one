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
> The local login page stores one CSRF nonce per page load in `login-csrf`
> (16 MiB, about 18,600 nonces of about 900 bytes for their 5-minute TTL).
> Each client, an IPv4 address or an IPv6 /64, may hold 2% of it, about 370
> nonces: twice what the default authorize rate limit lets one IP request in
> that time. A client flooding the page gets 503 with `Retry-After` while
> other clients still sign in; a flood spread over about 50 or more networks
> at once (one IPv6 /56 alone holds 256 /64s) can still fill it, which the
> redis backend or a larger budget absorbs. Behind a reverse proxy, list it in
> `auth.oauth_rate_limit.trusted_proxies`, or every client shares the
> proxy's address and its share.
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
