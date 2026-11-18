# Scaling and tuning

How many processes you may run, and how much memory and how many database
connections each one needs.
Install-specific mechanics (Helm values, compose files) stay in the
[installation guides](../installation/README.md); every key below is in the
generated [configuration reference](../reference/config.md).

## Process topology

| Rule | Why |
| ---- | --- |
| **One process per surface** on the default `memory` shared-state backend — one replica, one uvicorn worker. | Rate-limit buckets, circuit-breaker counters, `Idempotency-Key` records and the auth surface's single-use markers live in the process. A second process gets its own copy: limits multiply, an `Idempotency-Key` replay can be re-executed, and a consent handle can be redeemed twice. |
| The **gateway** (Helm) is the only component that scales out on OSS alone. | It is a stateless proxy. |
| More processes per surface need a **shared backend** registered with `register_state_backend` and selected with `state.backend`. | See [extending-jentic-one.md](../development/extending-jentic-one.md). The built-in `redis` backend is deprecated, needs the optional `[redis]` extra that the published image does not include, and will be removed. |
| **One shared-state backend per process**, shared by every surface the process serves. | In the combined app the auth and control surfaces share one backend, so `state.memory.max_total_bytes` caps the whole process. The broker always runs as its own process with its own backend. |
| **Scale the broker up, not out.** | Raise its container resources and `broker.resilience.max_in_flight` (a per-process admission cap, so replicas would multiply it). |

The JWT-bearer `jti` replay records are the exception to per-process state:
they live in the admin database, so every auth process sharing that database
refuses a replayed assertion whatever the state backend. The broker's
repeated-failure counters (the pre-filter in front of the
`execution.repeated_failure` database scan) are per process on `memory`, so a
failure spread across several processes reaches the threshold late or not at
all; on a shared backend the count is cluster-wide.

## The `memory` shared-state backend

`state.backend: memory` (the default) gives each key namespace — the key prefix
before the first `:` — a fixed budget, and caps all namespaces together at
`state.memory.max_total_bytes`. It never evicts a live entry. When a namespace,
one owner's share of it, or the total is full, the request that needed the
write gets a **503** with a `Retry-After` of when the earliest entry expires
(`idempotency_unavailable` for an `Idempotency-Key` request). The
repeated-failure namespace is the exception: when full it fails open and every
failure runs the database scan.

| Namespace | Holds | Default budget | Per-owner share |
| --------- | ----- | -------------- | --------------- |
| `idem` | `Idempotency-Key` records (~1.2 KiB each) | 250,000 entries / 128 MiB | 0.1 per caller |
| `idem-body` | their stored response bodies (~830 B + body) | 1,000,000 entries / 32 MiB | 0.1 per caller |
| `login-csrf` | one nonce per login page load (~900 B, 5-minute TTL) | 100,000 entries / 16 MiB | 0.02 per IPv4 address or IPv6 /64 |
| `repeated_failure` | broker repeated-failure counters | 10,000 entries / 8,780,000 B (~8.4 MiB) | none |
| every other (`auth`, `cb`, `mcp-session`, …) | | `max_entries_per_namespace` / `max_bytes_per_namespace` (100,000 / 16 MiB) | none |
| **all together** | | `max_total_bytes` = 192 MiB | |

Override any namespace under `state.memory.namespaces.<name>` (`max_entries`,
`max_bytes`, optional `max_owner_share`). Budgeted bytes count key + value +
about 750 bytes of per-entry overhead; a unit test holds the budget within 15%
of `tracemalloc`-measured memory. Allocator fragmentation is not included, so
leave container headroom.

### Idempotency capacity

The sustained idempotent request rate one broker process holds is

```
min(max_entries, max_bytes / record_bytes) / ttl_s
```

where `record_bytes` is about 1000 bytes plus the stored response headers
(capped at 512 bytes, `Content-Type`, `Location`, `ETag`, `Content-Location`
and `Retry-After` kept first, so a replay may lack the others), and `ttl_s` is
`broker.idempotency.ttl_s` — unset, 1 h on `memory` and 24 h on any other
backend.

| At the defaults | Rate |
| --------------- | ---- |
| `idem`, per process | ~30 idempotent req/s (128 MiB / 1.2 KiB ≈ 112,000 records over 3600 s) |
| `idem`, per caller (10% share) | ~3 req/s |
| `idem-body`, 1 KiB bodies | ~5 req/s; past it replays omit the body (`Jentic-Idempotency-Body-Omitted: true`), requests are not refused |

Bodies over `broker.idempotency.max_response_bytes` (256 KiB) are never stored
for replay. To raise the ceiling, raise `state.memory.namespaces.idem` (and its
`max_owner_share`) together with `max_total_bytes`, or lower
`broker.idempotency.ttl_s` — and raise the container memory limit by at least
as much as the budgets.

### Login CSRF nonces

The 16 MiB `login-csrf` budget holds about 18,600 live nonces; one client
network's 2% share is about 370, about twice the 180 nonces the default
authorize rate limit lets it mint in the 5-minute TTL
(`auth.oauth_rate_limit.authorize_burst` 30 + 5 × `authorize_rpm` 30). A single client flooding the page gets 503 while others still
sign in; a flood spread over about 50 or more networks at once (one IPv6 /56
holds 256 /64s) can still fill it, which a larger budget or a shared backend
absorbs. Behind a reverse proxy, list the proxy in
`auth.oauth_rate_limit.trusted_proxies`, or every client shares its address
and its share.

## Container memory

| Process | Size the limit for |
| ------- | ------------------ |
| Broker | ~130 MiB process baseline + `state.memory.max_total_bytes` (192 MiB). The Helm broker chart's 512Mi limit holds the defaults. |
| App (combined control plane) | ~150 MiB baseline + `max_total_bytes` (~220 MiB real) + `64 MiB × admin.auth.argon2_max_concurrency`. At least 512 MiB; 1 GiB recommended at the defaults. Add 64 MiB per extra argon2 slot. |

Every concurrent password or OAuth client-secret hash or verification
allocates 64 MiB (argon2id, `memory_cost=65536`). `admin.auth.argon2_max_concurrency`
(default 4, so 256 MiB peak) caps them per process; a caller beyond the cap
waits up to `admin.auth.argon2_acquire_timeout_s` (5 s) and then gets a 503
with `Retry-After`.

## Database connection pools

Each process opens one pool per database it is granted: the broker opens all
three (registry, control, admin), and so does the combined app. Each pool holds
up to `databases.<db>.pool_max` connections (default 10) at steady state, plus
SQLAlchemy's default overflow of 10 under burst. So per process:

```
steady = Σ pool_max over its databases      burst = steady + 10 × number of pools
```

Example: `pool_max` 20/15/10 on the app and broker containers holds
(20 + 15 + 10) × 2 = 90 connections steady, up to 150 when every pool bursts.
Size against Postgres `max_connections` (managed entry tiers are often ~100),
count every process and replica plus the migration job, or front Postgres
with a pooler such as pgbouncer.

Pre-ping, recycling and TCP keepalive (`databases.<db>.pool_pre_ping`,
`pool_recycle_s`, `tcp_keepalive*`) are in the
[configuration reference](../reference/config.md#databases).

The in-process job worker draws from the admin pool; its keys, including the
`worker.concurrency` pool constraint, are in the
[configuration reference](../reference/config.md#worker).
