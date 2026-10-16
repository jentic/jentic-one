# Broker perf suite

Local, on-demand performance tooling for the broker: how much latency it adds
to a call, what one call costs the databases, and how it behaves under load,
saturation and upstream faults. **Not part of CI** and not a pytest suite —
nothing here is collected by `pytest` (no `test_*.py` files).

Everything runs in one self-contained compose stack
([`docker-compose.perf.yaml`](docker-compose.perf.yaml)): Postgres (with
`pg_stat_statements`), migrations, the app (registry/admin/control/auth), the
broker as its own surface, and the [smoke-upstream harness](../harness/smoke_upstream)
as the upstream. Load generators run **inside** the compose network so numbers
exclude Docker Desktop's host port-forwarding.

## Quick start

```sh
make perf-build        # build jentic-one/app:perf from this checkout
make perf-up           # start the stack
make perf-provision    # vendors, credentials, callers -> results/.perf-env.json
make perf-overhead ARGS=--quick   # ~3 min: broker vs direct at 10 rps
make perf-dbcost       # per-call DB cost, warm vs cold caches
make perf-report       # results/report.html
make perf-down         # stop + wipe volumes
```

## What each tool answers

| Tool | Question | Method |
| --- | --- | --- |
| `bench_overhead.py` (`make perf-overhead`) | How much latency does the broker add? | [oha](https://github.com/hatoo/oha), **open loop** (fixed `-q` rate, `--latency-correction`), same request direct vs via broker, per case. |
| `dbcost.py` (`make perf-dbcost`) | What does one call cost each database? | `pg_stat_statements` grouped by role (each plane has its own role), idle baseline subtracted, ÷ N. |
| `locustfile.py` (`make perf-ui` / `perf-load`) | Where's the knee? What happens under faults, imports, mixed admin load? | [Locust](https://locust.io), **closed loop**, `constant_throughput` pacing, optional step/spike/soak shapes. |
| Grafana (`make perf-obs`) | What's happening server-side while it runs? | Prometheus scrape of the surfaces + postgres-exporter. |
| `report.py` (`make perf-report`) | How did this run compare to the last ones? | Static HTML (Plotly.js) over every `results/*.json`. |

### Reading the overhead numbers

* **Overhead = broker − direct** for the same request at the same rate.
  The **mean** delta is exact; **percentile deltas are indicative** — percentiles
  don't subtract (`p99(a) − p99(b)` is not `p99(a − b)`).
* Docker Desktop numbers are for **relative** comparison (variant vs variant,
  run vs run), not absolute SLOs.
* Low rates (1–10 rps) measure per-call cost; higher rates start to include
  queueing. Saturation is Locust's job, not oha's.

### Overhead cases

`uv run python -m tests.perf.bench_overhead --list` prints them. They cover
payload size (small/1 KB/100 KB/1 MB), method, credential type (no-auth,
bearer, API key in header/query/cookie, sigv4), caller auth (`jak_` API key,
opaque `at_` token, HS256 JWT), revision pinning, streaming vs buffered
persistence, idempotency replay, cold caches (all TTLs 0) and
`Prefer: respond-async` (time-to-202 only — completion is `AsyncJobUser`).

## Stack variants

The broker reads `PERF_*` env vars (defaults = product defaults / the perf
config). `bench_overhead.py` and `dbcost.py` recreate the broker per variant;
for Locust set them before `make perf-up` (or `docker compose up -d broker`).

| Variable | Default | Maps to |
| --- | --- | --- |
| `PERF_STREAMING` | `true` | `broker.resilience.upstream.stream_passthrough_enabled` |
| `PERF_MAX_PER_HOST` | `50` | `broker.resilience.upstream.max_per_host` |
| `PERF_RETRY` | `false` | `broker.resilience.retry.enabled` |
| `PERF_BREAKER` | `true` | `broker.resilience.circuit_breaker.enabled` |
| `PERF_RATE_LIMIT` | `false` | `broker.resilience.rate_limit.enabled` |
| `PERF_MAX_IN_FLIGHT` | `200` | `broker.resilience.max_in_flight` |
| `PERF_RESOLVE_TTL` / `PERF_TOOLKIT_TTL` / `PERF_RULE_TTL` | `3.0` | broker cache TTLs |
| `PERF_ADMIN_POOL` / `PERF_CONTROL_POOL` / `PERF_REGISTRY_POOL` | `10` | `databases.<plane>.pool_max` |
| `PERF_WORKERS` | `1` | uvicorn workers (>1 uses the `--factory` path) |
| `PERF_BROKER_CPUS` / `PERF_APP_CPUS` / `PERF_PG_CPUS` / `PERF_LOADGEN_CPUS` | `2` | container CPU caps |

Rate limiting is **off** by default: one agent driving load would otherwise be
429'd after the 100-request burst. The rate-limit scenario turns it back on and
spreads load over `PoolAgentUser`'s extra agents.

Retry scenarios need `PERF_RETRY=true PERF_BREAKER=false` — the circuit
breaker sits inside the retry runner and would trip first.

## Locust

```sh
make perf-ui                                   # http://127.0.0.1:8089, pick classes
make perf-load PERF_CLASSES="BrokerEchoUser" PERF_USERS=100 PERF_SPAWN=20 PERF_TIME=3m
PERF_SHAPE=step PERF_STEP_USERS=20 make perf-load    # ramp until the knee
```

| User class | Scenario |
| --- | --- |
| `BrokerEchoUser` | Steady small POSTs to one vendor (`PERF_VENDOR`, `PERF_CALLER`). |
| `MultiHostUser` | Same, spread over 5 upstream hosts (global vs per-host pool). |
| `PoolAgentUser` | One agent per user (rate-limit fairness, per-actor caches). |
| `RetryUser` | `X-Mock-Status-Sequence` (`PERF_STATUS_SEQUENCE`, default `503,503,200`). |
| `FailingUpstreamUser` | Always 503 — breaker open / fail-fast. |
| `DisconnectUser` | Upstream drops mid-body. |
| `IdempotentFirstCallUser` | Unique `Idempotency-Key` per call (first-call cost). |
| `AsyncJobUser` | `Prefer: respond-async` → 202 → poll `/jobs/{id}`; reports E2E time. |
| `ImportUser` | Inline spec import + job completion (`PERF_IMPORT_SPEC` for a big spec). |
| `ControlReadUser` | `/executions`, `/monitoring/executions`, `/search` reads. |

Slow upstream: `PERF_UPSTREAM_DELAY=0.5` adds `X-Mock-Delay` to every broker
call. Shapes: `PERF_SHAPE=step|spike|soak` (`PERF_STEP_USERS`, `PERF_STEP_S`,
`PERF_MAX_USERS`, `PERF_SOAK_S`).

## Provisioning notes

`provision.py` is idempotent and talks to the published host ports
(`127.0.0.1:18000` app, `18100` broker, `18084` harness). For each perf vendor
it imports the harness live spec **inline** with `servers[0].url` rewritten to
that vendor's own network alias, **promotes** the draft revision (the broker
only matches current revisions), replaces the vendor's credential (exactly one
active per API), direct-binds it to the perf agent with an allow-all rule, then
verifies every vendor × caller through the broker — including that the
credential actually reached the upstream.

Every harness alias lives under `.perf.internal`: the egress guard exempts a
private DNS name only when it matches **both** an allowed subnet
(`172.28.0.0/24`, pinned in the compose file) **and** an allowed internal
domain suffix.

`results/` is git-ignored; it holds result JSON, Locust CSV/HTML, the report,
and `.perf-env.json` (live tokens for the local stack).

## Known gaps

* The standalone broker's `/metrics/` is shadowed by its catch-all route (401),
  so broker panels in Grafana stay empty; app and Postgres panels work.
* No per-stage breakdown inside the broker yet (auth / discovery / credential /
  upstream / persistence) — overhead is measured end to end.
