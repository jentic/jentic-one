# Broker perf suite

Local, on-demand performance tooling for the broker: how much latency it adds
to a call, what one call costs the databases, and how it behaves under load,
saturation and upstream faults. **Not part of CI** and not a pytest suite —
nothing here is collected by `pytest` (no `test_*.py` files). The pure logic
that decides whether a result is written (success gating, token freshness,
interleaving, restore-on-exit, the run lock) is unit-tested in
[`tests/unit/perf_suite/`](../unit/perf_suite), which does run in CI.

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
make perf-capacity     # ~7 min: throughput vs concurrency for 1/2/4 workers
make perf-profile      # ~2 min: py-spy flamegraph + CPU share per subsystem
make perf-report       # results/report.html
make perf-down         # stop + wipe volumes
make perf-help         # every target, incl. the agent probes below
```

## What each tool answers

| Tool | Question | Method |
| --- | --- | --- |
| `bench_overhead.py` (`make perf-overhead`) | How much latency does the broker add? | [oha](https://github.com/hatoo/oha), **open loop** (fixed `-q` rate, `--latency-correction`), same request direct then via broker, interleaved per case; `--repeat K` reports the spread. |
| `bench_capacity.py` (`make perf-capacity`) | What's the max throughput per worker/CPU config, and what's the bottleneck? | oha **closed loop** (fixed concurrency, no rate cap), broker recreated per `workers x CPUs`, one `docker stats` CPU sample per step. |
| `pyspy_profile.py` (`make perf-profile`) | Where does the broker spend its CPU? | py-spy in a sidecar sharing the broker's PID namespace (image untouched), under oha closed-loop load; flamegraph SVG + CPU share per subsystem (DB client, DNS, OTel, ASGI, …). SQLAlchemy's greenlet bridge splits ORM stacks from their callers, so call-site shares are lower bounds. |
| `dbcost.py` (`make perf-dbcost`) | What does one call cost each database? | `pg_stat_statements` grouped by role (each plane has its own role), idle baseline subtracted, ÷ N sequential calls (one connection); aborts unless all N return 200. `app` is stopped during the run (its scanners would pollute control/admin); `warm` pins TTLs to 300 s. `statements` excludes `BEGIN`/`COMMIT`/`ROLLBACK` (reported as `tx_control`); `transactions` includes the ping transaction per checkout when `pool_pre_ping` is on (the default). |
| `locustfile.py` (`make perf-ui` / `perf-load`) | How do faults, imports and mixed admin load behave? Roughly where's the knee? | [Locust](https://locust.io), **closed loop**, `constant_throughput` pacing, optional step/spike/soak shapes. Closed loop suffers **coordinated omission** above the knee (slow responses lower the offered load, so queueing time is never recorded): its latencies there are a lower bound. |
| Grafana (`make perf-obs`) | What's happening server-side while it runs? | Prometheus scrape of the surfaces + postgres-exporter. Anonymous access is read-only (Viewer, `127.0.0.1` only); edit `obs/grafana/dashboards/broker-perf.json`, or log in as Grafana's built-in `admin` for Explore. Its password is `PERF_GRAFANA_ADMIN_PASSWORD` (default `admin`); Grafana's first-login prompt to change it can be skipped, so the default stays unless you set it. The UI listens on `127.0.0.1` only. |
| `report.py` (`make perf-report`) | How did this run compare to the last ones? | Static HTML (Plotly.js) over the latest overhead / dbcost / capacity / profile results (overhead also as a trend), plus links to every py-spy flamegraph and the latest Locust CSV. |

### Reading the overhead numbers

* **Overhead = broker − direct** for the same request at the same rate.
  The **mean** delta is exact; **percentile deltas are indicative** — percentiles
  don't subtract (`p99(a) − p99(b)` is not `p99(a − b)`).
* Docker Desktop numbers are for **relative** comparison (variant vs variant,
  run vs run), not absolute SLOs.
* Low rates (1–10 rps) measure per-call cost; higher rates start to include
  queueing. For the throughput ceiling use `bench_capacity`; for latency vs
  load use an open-loop rate sweep (`bench_overhead --rates 10,50,100,200`),
  not Locust, whose closed-loop latencies past the knee omit queueing time.
* Direct and broker runs are interleaved per case (direct immediately before
  broker), so host drift lands in both. One sample per case is noisy at the
  1 ms level: use `--repeat 3` (or more) before calling a difference real.
  `--repeat` must be odd, so the row's headline numbers are one real repeat:
  the one whose mean overhead is the median. The row also records
  `overhead_mean_spread` (min/median/max) and the report draws it as error
  bars.
* Every run must be mostly successful: a case (or capacity step, or profile)
  with fewer than `--min-ok` (default 0.99) expected-status responses is marked
  `"valid": false`, left out of the report, and the tool exits 1. Fast
  failures (401, 429, 5xx) skip the upstream and would read as *lower*
  overhead.
* Tokens: `config.perf.yaml` raises the `at_` caller and admin token
  lifetimes to 6 h (`auth.access_ttl_seconds`, `admin.auth.jwt_ttl_seconds`;
  the product default is 1 h) so the full overhead matrix (~54 min per
  `--repeat`) fits `--repeat 3` with headroom. This is perf-stack config only.
  The HS256 `jwt` caller lives 24 h. Each tool refuses to start if a token it
  uses would expire before the run (plus 5 min) can finish — re-run
  `make perf-provision`, or, if the run is longer than the token's whole
  lifetime, split it with `--only` or lower `--repeat`.
* Results record the host checkout's SHA (and `git_dirty`), the id/build time
  of the app image under test (rebuild with `make perf-build` after changes),
  and every service's image reference.
* The host-side tools take `results/.perf.lock`, so only one runs at a time;
  don't run them while a Locust run is driving the same stack.

### Overhead cases

`uv run python -m tests.perf.bench_overhead --list` prints them. They cover
payload size (small/1 KB/100 KB/1 MB), method, credential type (no-auth,
bearer, API key in header/query/cookie, sigv4), caller auth (`jak_` API key,
opaque `at_` token, HS256 JWT), revision pinning, streaming vs buffered
persistence, idempotency replay, cold caches (all TTLs 0) and
`Prefer: respond-async` (time-to-202 only — completion is `AsyncJobUser`).

Targets live in [`Makefile`](Makefile) next to this README; the root
`Makefile` passes `make perf-<target>` through (with variables), so run them
from the repo root.

## Agent probes

Targeted diagnostics for a coding agent (or a human) chasing a specific
question. Each prints markdown — tables, mechanically derived highlights with
`file:line`, and `NEXT:` suggestions — then `ARTIFACT: <json>` as its last line
(full data under `results/agent/`). Exit codes: `0` ran, `2` prerequisite
missing, harness failure or a run where any request failed (the output names
the fix), `3` regression (`compare`). `make` reports any failing recipe as 2,
so read the `RESULT:` / `PREREQUISITE:` line rather than the exit status.
Like the classic tools, the probes hold the suite run lock (`results/.perf.lock`)
and refuse to start on tokens that would expire before they finish.

| Target | Answers | Knobs |
| ------ | ------- | ----- |
| `perf-agent-doctor` | Is the stack up, are the tokens valid, is the running broker the clean one (no trace hook or probe variant left on), and was `jentic-one/app:perf` built from this checkout's commit (image revision label vs `git`)? | — |
| `perf-agent-sql` | Which SQL does one call run, from which line? Sessions / pre-pings / transactions / zero-row SELECTs / read-only COMMITs per plane; statements grouped by (SQL, callsite); a median request's timeline; `EXPLAIN (ANALYZE, BUFFERS)` of the costliest SELECTs. | `N CALLER CACHE=warm\|cold VENDOR EXPLAIN=5` |
| `perf-agent-stages` | Where does a call's wall time go? p50/p99/mean per stage (identity, discovery, authorization, credential resolution, upstream headers vs body, persistence, events) and event-loop lag, under closed-loop load. | `C DURATION CALLER VENDOR FUNCS=mod:Qual,...` |
| `perf-agent-cpu` | Which lines burn CPU at saturation? py-spy subsystem shares plus the hottest `jentic_one` `file:line` (deepest own frame and inclusive), and the flamegraph path. | `C DURATION CALLER VENDOR` |
| `perf-agent-resilience` | Do faults behave? Disconnect / always-503 / idempotent replay / async jobs per streaming mode, retry with the breaker off; Locust assertion failures and broker error logs grouped by event + traceback location. | `SECONDS SCENARIOS` |
| `perf-agent-compare` | Did my change help? Diffs two artifacts of one kind (agent `sql`/`stages`/`cpu`, or classic `overhead`/`capacity`/`dbcost`/`profile`); refuses (exit 2) runs that failed or differ in knobs. | `BASE HEAD TOLERANCE FORCE=1` |

The loop: `perf-agent-doctor` → a probe → read the tables and the cited lines
→ change the code → `make perf-build && make perf-up` → the same probe →
`perf-agent-compare BASE=<first ARTIFACT>`.

**Traced broker.** `sql` and `stages` recreate the broker with
[`agent/hooks/sitecustomize.py`](agent/hooks/sitecustomize.py) on its
`PYTHONPATH` (mounted read-only; the image and `src/` are untouched) and
restore the clean broker afterwards. The hook patches SQLAlchemy events,
`DatabaseSession`, and the stage functions as their modules load, tagging every
event with the broker's request id, and writes JSONL from a background thread.
Traced timings carry the hook's overhead: use them to rank, and the classic
benches for absolute numbers. ORM statements execute in SQLAlchemy greenlets
that inherit the context but not the caller's frames, so callsites come from a
contextvar set where our code opens a session or calls `execute`/`get`/`flush`.
The writer thread keeps file I/O off the event loop, but serialising events
still takes the GIL, so a traced broker is slower than a clean one. Each traced
broker writes `<pid>.hook.json` listing its patch targets; `stages` prints them
and exits 2 if it could not wrap `_handle`, so a renamed function is never a
silent gap.

**Compare thresholds.** `perf-agent-compare` first checks the two runs are
comparable: every request returned 200, and the probe arguments, `PERF_*`
variant and broker mode match (`FORCE=1` overrides and says why). Then:

| Metric | Regresses when |
| ------ | -------------- |
| `sql` statements / sessions / transactions per request (sequential calls) | any increase |
| `dbcost` statements / transactions / writes per call | `> +0.1`/call (absorbs `pg_stat_statements` rounding; `+1` regresses) |
| `stages` calls per request (closed loop, ends mid-flight) | `> ±0.05` |
| means, rps, CPU shares | beyond `TOLERANCE` (10 %) and a small absolute floor |
| p99, event-loop lag p99, capacity p99 | never — reported as `info` |

Tails are informational because each comes from a single run, and the p99 of a
20 s single-worker run can move by more than 10 % between identical runs (the
A/A spread is not calibrated here). To gate on a tail, repeat both runs and
compare the medians yourself.

**Request data in artifacts.** Bind parameter values are captured only while
`perf-agent-sql` runs `EXPLAIN`; they stay in memory and the trace JSONL is
deleted when the traced broker stops. Artifacts keep SQL text, parameter
*types*, call sites, and a whitelisted subset of each grouped log record (event,
level, error type, request id, location, traceback). That can still include
identifiers, so quote the markdown tables in issues and never paste raw
artifacts or traces into a public tracker.

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
| `PERF_TRACING` | `none` | `observability.tracing.exporter` |
| `PERF_WORKERS` | `1` | uvicorn workers (>1 uses the `--factory` path) |
| `PERF_BROKER_CPUS` / `PERF_APP_CPUS` / `PERF_PG_CPUS` / `PERF_UPSTREAM_CPUS` / `PERF_LOADGEN_CPUS` | `2` | container CPU caps (broker / app / Postgres / harness / oha + Locust workers) |
| `PERF_IMAGE` | `jentic-one/app:perf` | image for app, broker, migrate and the harness |
| `PERF_APP_PORT` / `PERF_BROKER_PORT` / `PERF_UPSTREAM_PORT` | `18000` / `18100` / `18084` | host ports (`127.0.0.1` only) |
| `PERF_LOCUST_PORT` / `PERF_PROM_PORT` / `PERF_GRAFANA_PORT` | `8089` / `19090` / `13000` | host ports (`127.0.0.1` only) |
| `PERF_GRAFANA_ADMIN_PASSWORD` | `admin` | Grafana's built-in `admin` password (read when the Grafana container is created) |
| `PERF_WORKERS_LOCUST` | `2` | Locust worker processes for `make perf-ui` |
| `PERF_LOCUST_ARGS` | — | extra args for the Locust master (`make perf-ui`) |
| `PERF_RUN_S` | `600` | planned Locust run length in s; tokens must outlive it |

The provisioning scripts assume the default ports; pass `--app/--broker/--upstream`
to `provision.py` if you change them.

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

With `PERF_SHAPE` set, `perf-load` leaves `-t` off: the shape ends the run
(a step ramp needs `PERF_MAX_USERS / PERF_STEP_USERS × PERF_STEP_S`, 5 min for
the example above).

| User class | Scenario |
| --- | --- |
| `BrokerEchoUser` | Steady small POSTs to one vendor (`PERF_VENDOR`, `PERF_CALLER`). |
| `MultiHostUser` | Same, spread over 5 upstream hosts (global vs per-host pool). |
| `PoolAgentUser` | One agent per user (rate-limit fairness, per-actor caches). |
| `RetryUser` | `X-Mock-Status-Sequence` (`PERF_STATUS_SEQUENCE`, default `503,503,200`). |
| `FailingUpstreamUser` | Always 503 — breaker open / fail-fast. |
| `DisconnectUser` | Upstream drops mid-body. Asserts a problem+json 500/502/503 when `PERF_STREAMING=false` (500 or 502 for the transport error, depending on the broker version; 503 once the breaker opens), 200/abort when streaming. |
| `IdempotentFirstCallUser` | Unique `Idempotency-Key` per call (first-call claim + store cost). |
| `IdempotentReplayUser` | Fixed `Idempotency-Key` per user (replay cost). |
| `AsyncJobUser` | `Prefer: respond-async` → 202 → poll `/jobs/{id}`; reports E2E time. |
| `ImportUser` | Inline spec import + job completion (`PERF_IMPORT_SPEC` for a big spec). |
| `ControlReadUser` | `/executions`, `/monitoring/executions`, `/search` reads. |

Slow upstream: `PERF_UPSTREAM_DELAY=0.5` adds `X-Mock-Delay` to every broker
call. Shapes: `PERF_SHAPE=step|spike|soak` (`PERF_STEP_USERS`, `PERF_STEP_S`,
`PERF_MAX_USERS`, `PERF_SOAK_S`).

Idempotency: an `Idempotency-Key` forces the broker onto the buffered path, so
compare the `Idempotent*User` classes against `BrokerEchoUser` with
`PERF_STREAMING=false`, not the streaming default.

## Provisioning notes

`provision.py` is idempotent and talks to the published host ports
(`127.0.0.1:18000` app, `18100` broker, `18084` harness). For each perf vendor
it imports the harness live spec **inline** with `servers[0].url` rewritten to
that vendor's own network alias, **promotes** the draft revision (the broker
only matches current revisions), replaces the vendor's credential (exactly one
active per API), direct-binds it to the perf agent with an allow-all rule, then
verifies every vendor × caller through the broker — each response must show
the credential actually reached the upstream.

Every harness alias lives under `.perf.internal`: the egress guard exempts a
private DNS name only when it matches **both** an allowed subnet
(`172.28.0.0/24`, pinned in the compose file) **and** an allowed internal
domain suffix.

`results/` is git-ignored; it holds result JSON, Locust CSV/HTML, the report,
and `.perf-env.json` (live tokens for the local stack).

## Known gaps

* Per-stage timings (`perf-agent-stages`) come from the traced broker only;
  the classic overhead bench is end to end.
