# Configuration reference

<!-- GENERATED FILE — do not hand-edit. Regenerate with `make config-reference`
     (tools/config_reference.py). Drift-gated by
     tests/arch/test_config_reference_conformance.py. -->

Every configuration key jentic-one reads, generated from the
[`AppConfig`](../../src/jentic_one/shared/config/app.py) Pydantic model — the same
source of truth behind [`config/config-schema.json`](../../config/config-schema.json)
and the `jenticctl install` wizard. How the config system works (loading,
`Context`, hot reload) is covered in
[Context and configuration](../development/context-and-config.md).

## How a value is resolved

Highest wins:

1. Environment variable (`JENTIC__SECTION__KEY`)
2. YAML config file (`jentic-one.yaml` in the working directory, or the file
   named by `JENTIC_CONFIG_FILE` — a loader-level variable, not a config key)
3. The field default listed below

Env-var naming: uppercase the YAML path and join nesting levels with `__`
(single underscores within a key are preserved). A `<name>` segment is a
map key you choose; a `<n>` segment is a zero-based list index
(`JENTIC__AUTH__ID_SIGNING__0__KID`). List- and model-valued fields are
usually easier to express in YAML.

A default of *required* means the app refuses to start without a value; `—`
means the default is empty or built at runtime (an empty list/map).
The open `extensions` section (enterprise overlay sub-configs registered at
import time) is machine state, not an operator knob, and is excluded here just
as it is from the JSON schema.

## `databases`

Named database connections.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `databases.registry.backend` | "postgres" \| "sqlite" | `"postgres"` | `JENTIC__DATABASES__REGISTRY__BACKEND` | Database engine for this connection: `postgres` uses the server connection fields; `sqlite` uses `path` and ignores them. |
| `databases.registry.host` | string | `"localhost"` | `JENTIC__DATABASES__REGISTRY__HOST` | PostgreSQL server hostname. |
| `databases.registry.port` | integer | `5432` | `JENTIC__DATABASES__REGISTRY__PORT` | PostgreSQL server port. |
| `databases.registry.name` | string | `""` | `JENTIC__DATABASES__REGISTRY__NAME` | PostgreSQL database name. Required for the `postgres` backend. |
| `databases.registry.user` | string | `"postgres"` | `JENTIC__DATABASES__REGISTRY__USER` | PostgreSQL role to connect as. |
| `databases.registry.password` | string (secret) | `""` | `JENTIC__DATABASES__REGISTRY__PASSWORD` | Password for the PostgreSQL role. |
| `databases.registry.pool_max` | integer | `10` | `JENTIC__DATABASES__REGISTRY__POOL_MAX` | Maximum connections in the PostgreSQL connection pool (ignored for SQLite). |
| `databases.registry.pool_pre_ping` | boolean | `true` | `JENTIC__DATABASES__REGISTRY__POOL_PRE_PING` | PostgreSQL: test pooled connections on checkout and transparently replace a dead one. By default the wire ping (three round trips on asyncpg: `BEGIN`, `;`, `ROLLBACK`) runs only when the connection has sat idle in the pool for at least `pool_pre_ping_idle_s` seconds; active checkouts within that window skip the wire ping unless the driver connection is already closed. With `false`, `pool_recycle_s` and TCP keepalive do not cover a Postgres restart or failover, a pooler/proxy restart, or a terminated backend: after such an event the first request on each pool per process can fail with an HTTP 503 (work run through the retrying transaction helper is re-run on a fresh connection instead). Only disable it where those events are rare and that error is acceptable. Ignored for SQLite. |
| `databases.registry.pool_pre_ping_idle_s` | number (>= 0) | `1.0` | `JENTIC__DATABASES__REGISTRY__POOL_PRE_PING_IDLE_S` | PostgreSQL: seconds a connection must sit idle in the pool before checkout runs the `pool_pre_ping` wire check. Active checkouts returning within this window skip the wire ping unless the driver connection is already closed. `0` pings on every checkout. Only applies when `pool_pre_ping` is `true`. Ignored for SQLite. |
| `databases.registry.pool_recycle_s` | integer (>= 0) | `1800` | `JENTIC__DATABASES__REGISTRY__POOL_RECYCLE_S` | PostgreSQL: replace a pooled connection once it is this many seconds old, on its next checkout, so a connection silently dropped by a middlebox or left behind by a server restart is retired before use. Keep it below any server, proxy, or NAT idle timeout on the path. `0` disables recycling. Ignored for SQLite. |
| `databases.registry.tcp_keepalive` | boolean | `true` | `JENTIC__DATABASES__REGISTRY__TCP_KEEPALIVE` | PostgreSQL: enable TCP keepalive on each pooled connection's client socket so a peer that vanished while the connection sat idle is detected in about `tcp_keepalive_idle_s + tcp_keepalive_count * tcp_keepalive_interval_s` seconds instead of the OS default of hours. `false` leaves the socket at the OS defaults. Ignored for SQLite. |
| `databases.registry.tcp_keepalive_idle_s` | integer (>= 1, <= 32767) | `60` | `JENTIC__DATABASES__REGISTRY__TCP_KEEPALIVE_IDLE_S` | PostgreSQL: seconds a connection may sit idle before the first TCP keepalive probe is sent (`TCP_KEEPIDLE`; `TCP_KEEPALIVE` on macOS). Only applies when `tcp_keepalive` is `true`. Ignored for SQLite. |
| `databases.registry.tcp_keepalive_interval_s` | integer (>= 1, <= 32767) | `10` | `JENTIC__DATABASES__REGISTRY__TCP_KEEPALIVE_INTERVAL_S` | PostgreSQL: seconds between unanswered TCP keepalive probes (`TCP_KEEPINTVL`). Only applies when `tcp_keepalive` is `true`. Ignored for SQLite. |
| `databases.registry.tcp_keepalive_count` | integer (>= 1, <= 127) | `5` | `JENTIC__DATABASES__REGISTRY__TCP_KEEPALIVE_COUNT` | PostgreSQL: unanswered TCP keepalive probes before the connection is declared dead (`TCP_KEEPCNT`). Only applies when `tcp_keepalive` is `true`. Ignored for SQLite. |
| `databases.registry.schema_name` | string | `"public"` | `JENTIC__DATABASES__REGISTRY__SCHEMA_NAME` | PostgreSQL schema for this connection: created if missing by the migration runner (`CREATE SCHEMA IF NOT EXISTS`) and put first on `search_path`, so several connections can share one database. |
| `databases.registry.path` | string \| null | `null` | `JENTIC__DATABASES__REGISTRY__PATH` | SQLite: filesystem path to the database file (`:memory:` for in-memory). Required for the `sqlite` backend. |
| `databases.registry.busy_timeout_ms` | integer | `5000` | `JENTIC__DATABASES__REGISTRY__BUSY_TIMEOUT_MS` | SQLite: per-connection wait for a held write lock, in milliseconds, before failing with `database is locked`. |
| `databases.registry.journal_mode` | string | `"WAL"` | `JENTIC__DATABASES__REGISTRY__JOURNAL_MODE` | SQLite journal mode (persistent per database file). `WAL` lets a writer and readers proceed concurrently instead of blocking each other. |
| `databases.admin.backend` | "postgres" \| "sqlite" | `"postgres"` | `JENTIC__DATABASES__ADMIN__BACKEND` | Database engine for this connection: `postgres` uses the server connection fields; `sqlite` uses `path` and ignores them. |
| `databases.admin.host` | string | `"localhost"` | `JENTIC__DATABASES__ADMIN__HOST` | PostgreSQL server hostname. |
| `databases.admin.port` | integer | `5432` | `JENTIC__DATABASES__ADMIN__PORT` | PostgreSQL server port. |
| `databases.admin.name` | string | `""` | `JENTIC__DATABASES__ADMIN__NAME` | PostgreSQL database name. Required for the `postgres` backend. |
| `databases.admin.user` | string | `"postgres"` | `JENTIC__DATABASES__ADMIN__USER` | PostgreSQL role to connect as. |
| `databases.admin.password` | string (secret) | `""` | `JENTIC__DATABASES__ADMIN__PASSWORD` | Password for the PostgreSQL role. |
| `databases.admin.pool_max` | integer | `10` | `JENTIC__DATABASES__ADMIN__POOL_MAX` | Maximum connections in the PostgreSQL connection pool (ignored for SQLite). |
| `databases.admin.pool_pre_ping` | boolean | `true` | `JENTIC__DATABASES__ADMIN__POOL_PRE_PING` | PostgreSQL: test pooled connections on checkout and transparently replace a dead one. By default the wire ping (three round trips on asyncpg: `BEGIN`, `;`, `ROLLBACK`) runs only when the connection has sat idle in the pool for at least `pool_pre_ping_idle_s` seconds; active checkouts within that window skip the wire ping unless the driver connection is already closed. With `false`, `pool_recycle_s` and TCP keepalive do not cover a Postgres restart or failover, a pooler/proxy restart, or a terminated backend: after such an event the first request on each pool per process can fail with an HTTP 503 (work run through the retrying transaction helper is re-run on a fresh connection instead). Only disable it where those events are rare and that error is acceptable. Ignored for SQLite. |
| `databases.admin.pool_pre_ping_idle_s` | number (>= 0) | `1.0` | `JENTIC__DATABASES__ADMIN__POOL_PRE_PING_IDLE_S` | PostgreSQL: seconds a connection must sit idle in the pool before checkout runs the `pool_pre_ping` wire check. Active checkouts returning within this window skip the wire ping unless the driver connection is already closed. `0` pings on every checkout. Only applies when `pool_pre_ping` is `true`. Ignored for SQLite. |
| `databases.admin.pool_recycle_s` | integer (>= 0) | `1800` | `JENTIC__DATABASES__ADMIN__POOL_RECYCLE_S` | PostgreSQL: replace a pooled connection once it is this many seconds old, on its next checkout, so a connection silently dropped by a middlebox or left behind by a server restart is retired before use. Keep it below any server, proxy, or NAT idle timeout on the path. `0` disables recycling. Ignored for SQLite. |
| `databases.admin.tcp_keepalive` | boolean | `true` | `JENTIC__DATABASES__ADMIN__TCP_KEEPALIVE` | PostgreSQL: enable TCP keepalive on each pooled connection's client socket so a peer that vanished while the connection sat idle is detected in about `tcp_keepalive_idle_s + tcp_keepalive_count * tcp_keepalive_interval_s` seconds instead of the OS default of hours. `false` leaves the socket at the OS defaults. Ignored for SQLite. |
| `databases.admin.tcp_keepalive_idle_s` | integer (>= 1, <= 32767) | `60` | `JENTIC__DATABASES__ADMIN__TCP_KEEPALIVE_IDLE_S` | PostgreSQL: seconds a connection may sit idle before the first TCP keepalive probe is sent (`TCP_KEEPIDLE`; `TCP_KEEPALIVE` on macOS). Only applies when `tcp_keepalive` is `true`. Ignored for SQLite. |
| `databases.admin.tcp_keepalive_interval_s` | integer (>= 1, <= 32767) | `10` | `JENTIC__DATABASES__ADMIN__TCP_KEEPALIVE_INTERVAL_S` | PostgreSQL: seconds between unanswered TCP keepalive probes (`TCP_KEEPINTVL`). Only applies when `tcp_keepalive` is `true`. Ignored for SQLite. |
| `databases.admin.tcp_keepalive_count` | integer (>= 1, <= 127) | `5` | `JENTIC__DATABASES__ADMIN__TCP_KEEPALIVE_COUNT` | PostgreSQL: unanswered TCP keepalive probes before the connection is declared dead (`TCP_KEEPCNT`). Only applies when `tcp_keepalive` is `true`. Ignored for SQLite. |
| `databases.admin.schema_name` | string | `"public"` | `JENTIC__DATABASES__ADMIN__SCHEMA_NAME` | PostgreSQL schema for this connection: created if missing by the migration runner (`CREATE SCHEMA IF NOT EXISTS`) and put first on `search_path`, so several connections can share one database. |
| `databases.admin.path` | string \| null | `null` | `JENTIC__DATABASES__ADMIN__PATH` | SQLite: filesystem path to the database file (`:memory:` for in-memory). Required for the `sqlite` backend. |
| `databases.admin.busy_timeout_ms` | integer | `5000` | `JENTIC__DATABASES__ADMIN__BUSY_TIMEOUT_MS` | SQLite: per-connection wait for a held write lock, in milliseconds, before failing with `database is locked`. |
| `databases.admin.journal_mode` | string | `"WAL"` | `JENTIC__DATABASES__ADMIN__JOURNAL_MODE` | SQLite journal mode (persistent per database file). `WAL` lets a writer and readers proceed concurrently instead of blocking each other. |
| `databases.control.backend` | "postgres" \| "sqlite" | `"postgres"` | `JENTIC__DATABASES__CONTROL__BACKEND` | Database engine for this connection: `postgres` uses the server connection fields; `sqlite` uses `path` and ignores them. |
| `databases.control.host` | string | `"localhost"` | `JENTIC__DATABASES__CONTROL__HOST` | PostgreSQL server hostname. |
| `databases.control.port` | integer | `5432` | `JENTIC__DATABASES__CONTROL__PORT` | PostgreSQL server port. |
| `databases.control.name` | string | `""` | `JENTIC__DATABASES__CONTROL__NAME` | PostgreSQL database name. Required for the `postgres` backend. |
| `databases.control.user` | string | `"postgres"` | `JENTIC__DATABASES__CONTROL__USER` | PostgreSQL role to connect as. |
| `databases.control.password` | string (secret) | `""` | `JENTIC__DATABASES__CONTROL__PASSWORD` | Password for the PostgreSQL role. |
| `databases.control.pool_max` | integer | `10` | `JENTIC__DATABASES__CONTROL__POOL_MAX` | Maximum connections in the PostgreSQL connection pool (ignored for SQLite). |
| `databases.control.pool_pre_ping` | boolean | `true` | `JENTIC__DATABASES__CONTROL__POOL_PRE_PING` | PostgreSQL: test pooled connections on checkout and transparently replace a dead one. By default the wire ping (three round trips on asyncpg: `BEGIN`, `;`, `ROLLBACK`) runs only when the connection has sat idle in the pool for at least `pool_pre_ping_idle_s` seconds; active checkouts within that window skip the wire ping unless the driver connection is already closed. With `false`, `pool_recycle_s` and TCP keepalive do not cover a Postgres restart or failover, a pooler/proxy restart, or a terminated backend: after such an event the first request on each pool per process can fail with an HTTP 503 (work run through the retrying transaction helper is re-run on a fresh connection instead). Only disable it where those events are rare and that error is acceptable. Ignored for SQLite. |
| `databases.control.pool_pre_ping_idle_s` | number (>= 0) | `1.0` | `JENTIC__DATABASES__CONTROL__POOL_PRE_PING_IDLE_S` | PostgreSQL: seconds a connection must sit idle in the pool before checkout runs the `pool_pre_ping` wire check. Active checkouts returning within this window skip the wire ping unless the driver connection is already closed. `0` pings on every checkout. Only applies when `pool_pre_ping` is `true`. Ignored for SQLite. |
| `databases.control.pool_recycle_s` | integer (>= 0) | `1800` | `JENTIC__DATABASES__CONTROL__POOL_RECYCLE_S` | PostgreSQL: replace a pooled connection once it is this many seconds old, on its next checkout, so a connection silently dropped by a middlebox or left behind by a server restart is retired before use. Keep it below any server, proxy, or NAT idle timeout on the path. `0` disables recycling. Ignored for SQLite. |
| `databases.control.tcp_keepalive` | boolean | `true` | `JENTIC__DATABASES__CONTROL__TCP_KEEPALIVE` | PostgreSQL: enable TCP keepalive on each pooled connection's client socket so a peer that vanished while the connection sat idle is detected in about `tcp_keepalive_idle_s + tcp_keepalive_count * tcp_keepalive_interval_s` seconds instead of the OS default of hours. `false` leaves the socket at the OS defaults. Ignored for SQLite. |
| `databases.control.tcp_keepalive_idle_s` | integer (>= 1, <= 32767) | `60` | `JENTIC__DATABASES__CONTROL__TCP_KEEPALIVE_IDLE_S` | PostgreSQL: seconds a connection may sit idle before the first TCP keepalive probe is sent (`TCP_KEEPIDLE`; `TCP_KEEPALIVE` on macOS). Only applies when `tcp_keepalive` is `true`. Ignored for SQLite. |
| `databases.control.tcp_keepalive_interval_s` | integer (>= 1, <= 32767) | `10` | `JENTIC__DATABASES__CONTROL__TCP_KEEPALIVE_INTERVAL_S` | PostgreSQL: seconds between unanswered TCP keepalive probes (`TCP_KEEPINTVL`). Only applies when `tcp_keepalive` is `true`. Ignored for SQLite. |
| `databases.control.tcp_keepalive_count` | integer (>= 1, <= 127) | `5` | `JENTIC__DATABASES__CONTROL__TCP_KEEPALIVE_COUNT` | PostgreSQL: unanswered TCP keepalive probes before the connection is declared dead (`TCP_KEEPCNT`). Only applies when `tcp_keepalive` is `true`. Ignored for SQLite. |
| `databases.control.schema_name` | string | `"public"` | `JENTIC__DATABASES__CONTROL__SCHEMA_NAME` | PostgreSQL schema for this connection: created if missing by the migration runner (`CREATE SCHEMA IF NOT EXISTS`) and put first on `search_path`, so several connections can share one database. |
| `databases.control.path` | string \| null | `null` | `JENTIC__DATABASES__CONTROL__PATH` | SQLite: filesystem path to the database file (`:memory:` for in-memory). Required for the `sqlite` backend. |
| `databases.control.busy_timeout_ms` | integer | `5000` | `JENTIC__DATABASES__CONTROL__BUSY_TIMEOUT_MS` | SQLite: per-connection wait for a held write lock, in milliseconds, before failing with `database is locked`. |
| `databases.control.journal_mode` | string | `"WAL"` | `JENTIC__DATABASES__CONTROL__JOURNAL_MODE` | SQLite journal mode (persistent per database file). `WAL` lets a writer and readers proceed concurrently instead of blocking each other. |

## `services`

Service-level settings (immutable after boot).

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `services.request_timeout_s` | number | `30.0` | `JENTIC__SERVICES__REQUEST_TIMEOUT_S` |  |
| `services.retry_max` | integer | `3` | `JENTIC__SERVICES__RETRY_MAX` |  |
| `services.retry_backoff_s` | number | `1.0` | `JENTIC__SERVICES__RETRY_BACKOFF_S` |  |
| `services.background_task_drain_timeout_s` | number (>= 0) | `10.0` | `JENTIC__SERVICES__BACKGROUND_TASK_DRAIN_TIMEOUT_S` | On shutdown, how long (seconds) the app waits for work requests handed off after their response (e.g. streaming execution records) before cancelling what is still running. Runs before the databases close; each cancelled task is logged and counted on `app.background_tasks` (`outcome=cancelled`). |

## `worker`

Background job-worker durability knobs. The worker claims a job, sets a **visibility deadline** (`visibility_timeout_s` from claim), and processes it. A job left `RUNNING` past that deadline by a crashed worker/pod is reclaimed on a later poll. A handler failure requeues the job with capped exponential backoff up to `max_attempts` claims; beyond that it is dead-lettered (poison-message handling) rather than looped forever. A handler that raises `NonRetryableJobError` (a failure no retry can change, such as an import whose spec fails validation) fails the job on that attempt instead. Each claim is a single atomic `UPDATE` (`FOR UPDATE SKIP LOCKED` on Postgres, a serialised write transaction on SQLite), so any number of worker loops — across processes or replicas, each running up to `concurrency` jobs — can poll the same queue without double-claiming a job.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `worker.visibility_timeout_s` | number | `120.0` | `JENTIC__WORKER__VISIBILITY_TIMEOUT_S` | Seconds a claimed (`RUNNING`) job stays invisible to other workers; past it the job is reclaimed and run again. Size it safely above the longest expected handler runtime (upstream timeout plus slack) so a healthy but slow job is never reclaimed mid-flight and processed twice. |
| `worker.max_attempts` | integer | `5` | `JENTIC__WORKER__MAX_ATTEMPTS` | Total claims of a repeatedly failing job before it is dead-lettered instead of requeued. |
| `worker.retry_backoff_base_s` | number | `2.0` | `JENTIC__WORKER__RETRY_BACKOFF_BASE_S` | Base delay (seconds) before a failed job becomes claimable again: attempt `n` waits `min(retry_backoff_base_s * 2**(n-1), retry_backoff_max_s)`. |
| `worker.retry_backoff_max_s` | number | `60.0` | `JENTIC__WORKER__RETRY_BACKOFF_MAX_S` | Cap (seconds) on the exponential backoff between attempts of a failed job. |
| `worker.drain_timeout_s` | number | `25.0` | `JENTIC__WORKER__DRAIN_TIMEOUT_S` | On graceful shutdown, how long (seconds) the worker waits for in-flight jobs to finish. Past it the worker stops; a still-`RUNNING` job is reclaimed via its visibility timeout after restart, so no work is dropped. |
| `worker.reconnect_backoff_base_s` | number (> 0, <= 300) | `2.0` | `JENTIC__WORKER__RECONNECT_BACKOFF_BASE_S` | First delay, in seconds, before the worker retries after losing its database connection. Doubles on each further failed attempt up to `reconnect_backoff_max_s`; each delay is then jittered into the upper half of its value so workers sharing a database do not retry in lockstep. |
| `worker.reconnect_backoff_max_s` | number (> 0, <= 3600) | `30.0` | `JENTIC__WORKER__RECONNECT_BACKOFF_MAX_S` | Cap, in seconds, on the worker's reconnect delay while the database is unreachable — a recovered database is picked up within this long. |
| `worker.poll_interval_s` | number (>= 0.25, <= 60.0) | `2.0` | `JENTIC__WORKER__POLL_INTERVAL_S` | Idle wait (seconds) between job-queue polls when nothing is claimable. A job enqueued in the same process wakes the worker immediately, so this mainly bounds pickup latency for jobs enqueued by another process and how quickly visibility-expired jobs are reclaimed. Every poll is a write transaction (the claim is an `UPDATE`) even when the queue is empty — on SQLite it takes the database write lock that request writes also wait on — so each process issues about `1 / poll_interval_s` of them per second while idle. |
| `worker.concurrency` | integer (>= 1, <= 64) | `1` | `JENTIC__WORKER__CONCURRENCY` | Maximum jobs one worker process runs at once (1 = serial). Postgres only: SQLite requires 1, because a job's handler holds the database write lock across its upstream call, so a second concurrent job's writes would time out. Each in-flight job holds about 2 admin-DB connections at once (its handler transaction plus the run-time authorization or credential-audit session it opens), so `2 * concurrency` must stay below `databases.admin.pool_max`, leaving the remainder for HTTP requests and other background work in the same process. |
| `worker.sweep_interval_s` | number (>= 1.0, <= 86400.0) | `120.0` | `JENTIC__WORKER__SWEEP_INTERVAL_S` | Seconds between the worker's retention sweeps, which expire lapsed execution approvals and delete expired job results and long-expired access/refresh token rows. Measured in wall time, independent of `poll_interval_s` and wake-ups. |

## `runtime`

Hot-reloadable runtime flags.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `runtime.debug` | boolean | `false` | `JENTIC__RUNTIME__DEBUG` | Render stdout logs with the human-readable console renderer instead of JSON lines. For local development. |
| `runtime.log_level` | string | `"INFO"` | `JENTIC__RUNTIME__LOG_LEVEL` | Root log level: an uppercase Python level name such as `DEBUG`, `INFO` or `WARNING`. |
| `runtime.maintenance_mode` | boolean | `false` | `JENTIC__RUNTIME__MAINTENANCE_MODE` |  |

## `logging`

File logging sink (in addition to stdout).

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `logging.file_enabled` | boolean | `false` | `JENTIC__LOGGING__FILE_ENABLED` | Also write logs to a size-rotated file. The file is always JSON lines, whatever `runtime.debug` selects for stdout. |
| `logging.file_dir` | string | `".jentic/logs"` | `JENTIC__LOGGING__FILE_DIR` | Directory for the log file (relative paths resolve against the working directory); created at startup if missing. |
| `logging.file_name` | string | `"app.log"` | `JENTIC__LOGGING__FILE_NAME` | Log file name inside `file_dir`. |
| `logging.file_max_bytes` | integer | `10485760` | `JENTIC__LOGGING__FILE_MAX_BYTES` | Size in bytes at which the log file is rotated (default 10 MiB); 0 never rotates. |
| `logging.file_backup_count` | integer | `5` | `JENTIC__LOGGING__FILE_BACKUP_COUNT` | Number of rotated log files kept beside the active one; older ones are deleted. |
| `logging.http_wire_trace` | boolean | `false` | `JENTIC__LOGGING__HTTP_WIRE_TRACE` | Let the outbound wire-trace DEBUG loggers (httpcore, hpack) through when the log level is DEBUG. Off by default: those lines can quote outbound header values and request paths with their query strings, including injected credentials, unredacted. Not safe for production; enable only for short-lived local debugging. |

## `server`

HTTP server settings.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `server.host` | string | `"0.0.0.0"` | `JENTIC__SERVER__HOST` | Interface the HTTP server binds to. `0.0.0.0` / `::` listen on all interfaces; a loopback address keeps the app reachable from this machine only. |
| `server.port` | integer | `8000` | `JENTIC__SERVER__PORT` | TCP port the HTTP server listens on. |
| `server.reload` | boolean | `false` | `JENTIC__SERVER__RELOAD` | Run uvicorn with auto-reload on code changes (development only). Ignored, with a warning, when the admin database is SQLite or the config comes from a one-shot source such as a pipe. |
| `server.backend` | "local" \| "remote" | `"local"` | `JENTIC__SERVER__BACKEND` | Backend locality reported by `GET /instance`: `local` for a self-hosted install on the operator's own machine or network, `remote` for a hosted install run elsewhere. A hint for clients, not an authorization signal; `remote` also skips the release check. |
| `server.mcp.enabled` | boolean | `false` | `JENTIC__SERVER__MCP__ENABLED` | Serve the Streamable HTTP MCP endpoint at `/mcp`. Off, `/mcp` answers a plain 404, unless `oauth.enabled` keeps its 401 + `WWW-Authenticate` discovery challenge so OAuth clients can walk the RFC 9728 chain before the endpoint is turned on. |
| `server.mcp.broker_url` | string | `"http://127.0.0.1:8100"` | `JENTIC__SERVER__MCP__BROKER_URL` | Broker base URL the MCP `execute` tools forward calls to, carrying the caller's bearer token. The default matches the local install (loopback broker on port 8100); split deployments set the broker service's URL. Plain `http://` is refused for non-loopback hosts. |
| `server.mcp.oauth.enabled` | boolean | `false` | `JENTIC__SERVER__MCP__OAUTH__ENABLED` | Serve the MCP interactive-OAuth surface: anonymous dynamic client registration (`POST /oauth-clients`) and the `/mcp`-scoped OAuth discovery documents. Off, those routes answer a plain 404. |
| `server.mcp.oauth.auto_approve_clients` | boolean | `false` | `JENTIC__SERVER__MCP__OAUTH__AUTO_APPROVE_CLIENTS` | Activate dynamically registered OAuth clients immediately. Off, a new registration lands pending and inactive in the admin queue until an operator approves it; enable only where self-registered clients are acceptable without review (e.g. a single-user self-hosted install). |
| `server.mcp.oauth.registration_gc_days` | integer | `90` | `JENTIC__SERVER__MCP__OAUTH__REGISTRATION_GC_DAYS` |  |
| `server.public_base_url` | string | `""` | `JENTIC__SERVER__PUBLIC_BASE_URL` | The public origin of this deployment (e.g. `https://jentic.example.com`). Absolute URLs the app hands to external clients — the OAuth connect `redirect_uri`, the OIDC issuer and JWT-Bearer audience, DCR `registration_client_uri`, the SPA login callback, approval links — fall back to it when their own more specific key is unset. Unset, they derive from the incoming request's origin, or from the bind (`http://{host}:{port}`) when there is no request; set it when clients reach the app through a reverse proxy, ingress or port mapping. |

## `observability`

Observability settings (metrics, tracing knobs).

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `observability.metrics.exporter` | "otlp" \| "prometheus" \| "none" | `"otlp"` | `JENTIC__OBSERVABILITY__METRICS__EXPORTER` | Where metrics go: `otlp` pushes to the OTLP endpoint named by the standard `OTEL_EXPORTER_OTLP_*` environment variables, `prometheus` serves a scrape endpoint at `/metrics`, and `none` records nothing. |
| `observability.metrics.export_interval_seconds` | integer | `15` | `JENTIC__OBSERVABILITY__METRICS__EXPORT_INTERVAL_SECONDS` | Seconds between OTLP metric pushes. Ignored unless `exporter` is `otlp`. |
| `observability.tracing.exporter` | "otlp" \| "none" | `"otlp"` | `JENTIC__OBSERVABILITY__TRACING__EXPORTER` | Where spans go. `none` records no spans at all (every span is non-recording), while trace ids are still generated and propagated: an inbound `traceparent` is honoured, outbound requests carry `traceparent`/`tracestate`, and execution records and logs keep their `trace_id`. With `none` the outbound `traceparent` has the sampled flag unset (`-00`) even when the caller sent `-01`, since this process records nothing. |
| `observability.tracing.sample_ratio` | number (>= 0.0, <= 1.0) | `1.0` | `JENTIC__OBSERVABILITY__TRACING__SAMPLE_RATIO` | Fraction of traces recorded and exported when `exporter` is `otlp` (1.0 records every trace). It caps every trace entering the process, including requests that arrive with a sampled `traceparent` (the CLI and MCP send one on every call), so a caller cannot force recording. A caller's unsampled `traceparent` stays unsampled. Ignored with `exporter: none`, and overridden by the standard `OTEL_TRACES_SAMPLER` environment variable when that is set (a warning is logged if this field is not 1.0). |

## `admin`

Admin surface configuration.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `admin.auth.jwt_secret` | string (secret) | `""` | `JENTIC__ADMIN__AUTH__JWT_SECRET` | HMAC secret that signs admin login JWTs. Required in production (empty or placeholder values are rejected at startup); in development an ephemeral per-process secret is generated when empty. |
| `admin.auth.jwt_ttl_seconds` | integer (> 0) | `3600` | `JENTIC__ADMIN__AUTH__JWT_TTL_SECONDS` | Lifetime of one admin login JWT. `POST /auth/refresh` re-mints the token, so this bounds a single token, not the session. |
| `admin.auth.session_ttl_seconds` | integer (> 0) | `43200` | `JENTIC__ADMIN__AUTH__SESSION_TTL_SECONDS` | Absolute cap on a web session: refresh re-mints the login JWT only while `now - auth_time` stays inside this window, so a leaked token cannot be kept alive indefinitely. Must be >= `jwt_ttl_seconds`. |
| `admin.auth.failed_login_lockout_threshold` | integer | `5` | `JENTIC__ADMIN__AUTH__FAILED_LOGIN_LOCKOUT_THRESHOLD` | Consecutive failed logins after which an admin account is locked. |
| `admin.auth.failed_login_lockout_seconds` | integer | `900` | `JENTIC__ADMIN__AUTH__FAILED_LOGIN_LOCKOUT_SECONDS` | How long a locked admin account stays locked before logins are accepted again. |
| `admin.auth.argon2_max_concurrency` | integer (>= 1, <= 64) | `4` | `JENTIC__ADMIN__AUTH__ARGON2_MAX_CONCURRENCY` | Per-process cap on concurrent argon2id password and OAuth client-secret hashes and verifications across all surfaces. Each argon2id call allocates 64 MiB (`memory_cost=65536`), so the default of 4 bounds peak argon2 memory at 256 MiB per process. Calls beyond the cap wait up to `argon2_acquire_timeout_s` for a slot and then fail with HTTP 503 + `Retry-After` instead of allocating another 64 MiB buffer. |
| `admin.auth.argon2_acquire_timeout_s` | number (> 0, <= 60.0) | `5.0` | `JENTIC__ADMIN__AUTH__ARGON2_ACQUIRE_TIMEOUT_S` | Maximum seconds a password or OAuth client-secret hash or verification waits for one of the `argon2_max_concurrency` slots before failing with HTTP 503 and a `Retry-After` header. |
| `admin.invite.ttl_days` | integer | `7` | `JENTIC__ADMIN__INVITE__TTL_DAYS` | Days an admin invite token stays redeemable after it is issued. |
| `admin.invite.pepper` | string (secret) | `""` | `JENTIC__ADMIN__INVITE__PEPPER` | HMAC key for stored invite-token hashes. Required in production (empty or placeholder values are rejected at startup); in development an ephemeral per-process value is generated when empty. Changing it invalidates every outstanding invite. |
| `admin.events.stream_recheck_seconds` | number (> 0) | `5.0` | `JENTIC__ADMIN__EVENTS__STREAM_RECHECK_SECONDS` | Minimum seconds between re-verifications of an open `GET /events/stream`: its credential, `events:read` / `org:admin` permission, and the scope of the events it delivers. Checks run only at a poll or page boundary, so a check happens at the first poll or page after the interval elapses; an idle stream polls every few seconds, so a value below its poll interval behaves as that interval. A disabled caller, a revoked or expired token, or a revoked permission ends the stream at that check; a narrower permission set narrows it. Permissions are those the credential carries: API keys and `at_` access tokens resolve them live (an ephemeral agent `at_` keeps the subset it was minted with). A login session JWT carries the set it was issued with, but removing any of a user's effective permissions revokes their login sessions, so a stream opened with one ends at that check. The stream re-verifies the token it was opened with, so a permission added to a user reaches a login-session stream once the client reconnects with a newer login JWT. |

## `auth`

Platform-actors OAuth surface configuration.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `auth.canonical_base_url` | string | `""` | `JENTIC__AUTH__CANONICAL_BASE_URL` | Public origin of the auth surface (`scheme://host[:port]`, no path), used for the OIDC issuer, the JWT-Bearer assertion audience and the SPA login callback. Unset falls back to `server.public_base_url`, then the request or bind origin. A value with a path breaks the OAuth well-known discovery routes. |
| `auth.access_ttl_seconds` | integer | `3600` | `JENTIC__AUTH__ACCESS_TTL_SECONDS` | Lifetime (seconds) of an OAuth access token issued by the auth surface. |
| `auth.refresh_ttl_seconds` | integer | `604800` | `JENTIC__AUTH__REFRESH_TTL_SECONDS` | Lifetime (seconds, default 7 days) of an OAuth refresh token; each refresh rotates it and the new token gets a fresh lifetime. |
| `auth.rat_ttl_seconds` | integer | `900` | `JENTIC__AUTH__RAT_TTL_SECONDS` | Lifetime (seconds) of the registration access token returned when an agent registers, which the agent uses to check its registration status. |
| `auth.claim_ttl_seconds` | integer | `900` | `JENTIC__AUTH__CLAIM_TTL_SECONDS` | Lifetime (seconds) of the agent-ownership claim token minted at agent registration. Has no effect unless a claim-token minter is installed; the default build mints none. |
| `auth.assertion_max_ttl_seconds` | integer | `300` | `JENTIC__AUTH__ASSERTION_MAX_TTL_SECONDS` | Furthest in the future (seconds) a JWT-Bearer client assertion's `exp` may be; assertions expiring later are rejected. |
| `auth.auth_code_ttl_seconds` | integer | `300` | `JENTIC__AUTH__AUTH_CODE_TTL_SECONDS` | Lifetime (seconds) of an OAuth authorization code before it must be exchanged. |
| `auth.id_signing` | list of SigningKeyConfig | — | `JENTIC__AUTH__ID_SIGNING` | ES256 keys for signing OIDC ID tokens. The first entry signs; every entry is published in the JWKS, so append the next key and later move it first to rotate. The local-dev key is rejected in production. |
| `auth.id_signing.<n>.kid` | string | *required* | `JENTIC__AUTH__ID_SIGNING__<N>__KID` | Key id published in the JWKS and stamped in the `kid` header of ID tokens signed with this key (`[a-zA-Z0-9_-]+`). |
| `auth.id_signing.<n>.private_key_pem` | string (secret) | *required* | `JENTIC__AUTH__ID_SIGNING__<N>__PRIVATE_KEY_PEM` | PEM-encoded EC P-256 private key used for ES256 signing. |
| `auth.idp.enabled` | boolean | `false` | `JENTIC__AUTH__IDP__ENABLED` | Enable login via an external OIDC identity provider. When false no IdP adapter is built; /authorize stays routed and (unless auth.local_login.enabled provides the password form) ends in an OAuth server_error redirect because no sign-in path exists. |
| `auth.idp.provider` | string | `"oidc"` | `JENTIC__AUTH__IDP__PROVIDER` | Adapter selector: `google` supplies Google's well-known endpoints and `hd` claim handling; any other value uses the generic standards-compliant OIDC adapter. |
| `auth.idp.issuer` | string | `""` | `JENTIC__AUTH__IDP__ISSUER` | OIDC issuer base URL. Default authorization/token/userinfo endpoints are derived from it when the explicit `*_endpoint` keys are unset. |
| `auth.idp.client_id` | string | `""` | `JENTIC__AUTH__IDP__CLIENT_ID` | OAuth client ID registered with the identity provider. |
| `auth.idp.client_secret` | string (secret) | `""` | `JENTIC__AUTH__IDP__CLIENT_SECRET` | OAuth client secret, sent on the authorization-code exchange. |
| `auth.idp.scopes` | list of string | — | `JENTIC__AUTH__IDP__SCOPES` | OAuth scopes requested on the IdP authorization redirect. |
| `auth.idp.authorization_endpoint` | string \| null | `null` | `JENTIC__AUTH__IDP__AUTHORIZATION_ENDPOINT` | Explicit IdP authorization endpoint URL; overrides the issuer-derived or provider well-known default. |
| `auth.idp.exchange_endpoint` | string \| null | `null` | `JENTIC__AUTH__IDP__EXCHANGE_ENDPOINT` | Explicit IdP token (code-exchange) endpoint URL; overrides the issuer-derived or provider well-known default. |
| `auth.idp.userinfo_endpoint` | string \| null | `null` | `JENTIC__AUTH__IDP__USERINFO_ENDPOINT` | Explicit IdP userinfo endpoint URL; overrides the issuer-derived or provider well-known default. |
| `auth.idp.hosted_domain` | string \| null | `null` | `JENTIC__AUTH__IDP__HOSTED_DOMAIN` | Google `hd` (hosted-domain) hint. **Not an access control in the OSS build**: the claim is surfaced (see IdpClaims.hosted_domain) but never compared — the default admission policy admits every brand-new email (with zero permissions until an operator grants some). Restricting sign-in to a domain requires configuring it at the IdP or installing a custom admission policy. |
| `auth.local_login.enabled` | boolean | `false` | `JENTIC__AUTH__LOCAL_LOGIN__ENABLED` | Offer a first-party password login form on the /authorize flow. Off by default; when auth.idp.enabled is true the external IdP always wins and the form is never offered. |
| `auth.platform_clients` | list of PlatformClientConfig | — | `JENTIC__AUTH__PLATFORM_CLIENTS` | Static first-party OAuth clients that authenticate with PKCE only (no secret). When `auth.canonical_base_url` is set, the operator SPA (`jentic-one-spa`) is added automatically if not listed. |
| `auth.platform_clients.<n>.client_id` | string | *required* | `JENTIC__AUTH__PLATFORM_CLIENTS__<N>__CLIENT_ID` | OAuth client id the first-party client presents. |
| `auth.platform_clients.<n>.redirect_uris` | list of string | *required* | `JENTIC__AUTH__PLATFORM_CLIENTS__<N>__REDIRECT_URIS` | Allowed absolute redirect URIs (exact match). `https` is required except for `localhost` / `127.0.0.1`, where `http` is accepted. |
| `auth.oauth_rate_limit.authorize_rpm` | integer | `30` | `JENTIC__AUTH__OAUTH_RATE_LIMIT__AUTHORIZE_RPM` | Sustained requests/minute allowed per client network (IPv4 address or IPv6 /64) across the unauthenticated authorization flow: `/authorize`, the IdP callback, the consent page, its submit and inline agent create, and the local-login form share one bucket, whatever `client_id` they name. A sign-in spends two to six requests (more after a mistyped password), so the defaults admit at least five back-to-back sign-ins and then at least five a minute per network; raise both for a large shared NAT or egress IP. Behind a proxy, set `auth.oauth_rate_limit.trusted_proxies` or every client shares the proxy's bucket. |
| `auth.oauth_rate_limit.authorize_burst` | integer | `30` | `JENTIC__AUTH__OAUTH_RATE_LIMIT__AUTHORIZE_BURST` | Burst allowance on top of `authorize_rpm`. |
| `auth.oauth_rate_limit.exchange_rpm` | integer | `60` | `JENTIC__AUTH__OAUTH_RATE_LIMIT__EXCHANGE_RPM` | Sustained requests/minute allowed per `client_id` + client network (IPv4 address or IPv6 /64) on the token endpoint (also reused per client network for token revocation). |
| `auth.oauth_rate_limit.exchange_burst` | integer | `60` | `JENTIC__AUTH__OAUTH_RATE_LIMIT__EXCHANGE_BURST` | Burst allowance on top of `exchange_rpm`. |
| `auth.oauth_rate_limit.registration_rpm` | integer | `10` | `JENTIC__AUTH__OAUTH_RATE_LIMIT__REGISTRATION_RPM` | Sustained requests/minute allowed per client network (IPv4 address or IPv6 /64) on anonymous dynamic client registration (`POST /oauth-clients`). |
| `auth.oauth_rate_limit.registration_burst` | integer | `5` | `JENTIC__AUTH__OAUTH_RATE_LIMIT__REGISTRATION_BURST` | Burst allowance on top of `registration_rpm`. |
| `auth.oauth_rate_limit.trusted_proxies` | list of string | — | `JENTIC__AUTH__OAUTH_RATE_LIMIT__TRUSTED_PROXIES` | Reverse proxies whose `X-Forwarded-For` header is honored when deriving the per-client rate-limit identity: IP addresses or CIDR networks (e.g. `10.0.0.0/8`, `fd00::/8`), matched against the socket address and each forwarded hop by network membership. Accepts a YAML list or a comma-separated string. Invalid entries fail config load. Empty (default) means the socket address is used as-is — behind a reverse proxy every request then carries the proxy's IP and all clients share one bucket, so list the proxy IPs or pod CIDR when deploying behind one. Only list ranges your proxies alone hold: any host inside a trusted network can choose its own rate-limit identity. |
| `auth.oauth_rate_limit.approval_status_rpm` | integer | `120` | `JENTIC__AUTH__OAUTH_RATE_LIMIT__APPROVAL_STATUS_RPM` | Sustained requests/minute allowed on the approval-pending status poll (`GET /oauth/approval/status`). Its own namespace, so polling can never drain the `/authorize` or registration quota: one pending tab polls at 12 rpm, so the default keeps ~10 concurrent pending tabs behind one NAT inside the bucket, and the page honors `Retry-After` with backoff, so saturation degrades to a slower cadence rather than a thundering retry. |
| `auth.oauth_rate_limit.approval_status_burst` | integer | `60` | `JENTIC__AUTH__OAUTH_RATE_LIMIT__APPROVAL_STATUS_BURST` | Burst allowance on top of `approval_status_rpm`. |

## `broker`

Broker surface configuration.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `broker.upstream_timeout_s` | number | `30.0` | `JENTIC__BROKER__UPSTREAM_TIMEOUT_S` | Timeout (seconds) handed to the execution runner for one upstream call on the buffered sync path and the async job worker. Distinct from the transport-level `broker.resilience.upstream` timeouts and the `request_deadline_s` envelope. |
| `broker.resolve_cache_ttl_seconds` | number | `3.0` | `JENTIC__BROKER__RESOLVE_CACHE_TTL_SECONDS` | Seconds the broker caches a token or API-key validation verdict (and a JWT caller's active status) in process. Bounds how long a revoked token or disabled agent keeps working on each broker; 0 disables the cache. |
| `broker.toolkit_cache_ttl_s` | number | `3.0` | `JENTIC__BROKER__TOOLKIT_CACHE_TTL_S` | Seconds the broker caches an agent's derived credential bindings in process. The cache is per instance, so a grant or revoke takes effect cluster-wide only once this lapses on every broker; 0 disables the cache. |
| `broker.rule_cache_ttl_s` | number | `3.0` | `JENTIC__BROKER__RULE_CACHE_TTL_S` | Seconds the broker caches the permission rules of an (agent, credential) binding in process; a rule change reaches each broker within this long. 0 disables the cache. |
| `broker.rule_cache_max_entries` | integer | `5000` | `JENTIC__BROKER__RULE_CACHE_MAX_ENTRIES` | Maximum entries in each broker process's permission-rule cache, one per active (agent, credential) binding; least recently used entries are evicted first. |
| `broker.streaming_persist_timeout_s` | number (> 0) | `10.0` | `JENTIC__BROKER__STREAMING_PERSIST_TIMEOUT_S` | Budget (seconds) for writing one streaming execution's record and lifecycle event. The write runs after the response, off the request, so it never delays the client; past the budget it is abandoned, logged and counted on `app.background_tasks` (`kind=streaming_execution_persist`, `outcome=timed_out`). |
| `broker.streaming_persist_max_concurrency` | integer (> 0) | `5` | `JENTIC__BROKER__STREAMING_PERSIST_MAX_CONCURRENCY` | Per-process cap on concurrent post-response persists of streaming execution records (each holds one admin-DB connection). Excess persists wait for a slot, up to `broker.streaming_persist_acquire_timeout_s`; the waiting and writing counts are exported as `broker.streaming_execution.persist_pending`. The effective cap is clamped to `databases.admin.pool_max - 1` (minimum 1; a `streaming_persist_limit_clamped` warning is logged when it applies), so with `pool_max >= 2` persists never take every connection of the admin base pool. `pool_max` is the base pool size only; SQLAlchemy's default overflow can open up to 10 more connections on top of it. |
| `broker.streaming_persist_acquire_timeout_s` | number (> 0) | `5.0` | `JENTIC__BROKER__STREAMING_PERSIST_ACQUIRE_TIMEOUT_S` | How long (seconds) a streaming execution persist waits for one of the `broker.streaming_persist_max_concurrency` slots. Past it the record is abandoned: logged as `streaming_execution_persist_abandoned` and counted on `broker.streaming_execution.persist_failures` (`reason=backlog_timeout`). The wait runs after the response is complete or the client has disconnected, when the request no longer holds its admission slot, so the wait delays neither the client nor `/ready`. The timeout bounds how long a stalled admin database can keep records queued in memory, including at shutdown; it does not bound the write itself once a slot is held. |
| `broker.jobs_api_base_url` | string \| null | `null` | `JENTIC__BROKER__JOBS_API_BASE_URL` | Public base URL of the admin API (e.g. `https://api.example.com`), used for the links a broker returns for async and held executions (`/jobs/{id}`, `/executions/holds/{id}`, the approval review page). Unset, they are relative to the broker's own origin (the review page link tries `server.public_base_url` first); set it when the broker runs without the admin surface. |
| `broker.jwt_secret` | string (secret) \| null | `null` | `JENTIC__BROKER__JWT_SECRET` | HS256 shared secret for verifying self-contained JWTs at the broker (signature and `exp` only), for development and tests. Unset disables this path; `jwt_verification.trusted_issuers` takes precedence when set and is the production option. |
| `broker.jwt_verification.audience` | string \| null | `null` | `JENTIC__BROKER__JWT_VERIFICATION__AUDIENCE` | Required `aud` claim on trusted-issuer JWTs. Unset, tokens must not carry an `aud` claim at all (any token with one is rejected). |
| `broker.jwt_verification.leeway_s` | number | `60.0` | `JENTIC__BROKER__JWT_VERIFICATION__LEEWAY_S` | Clock-skew tolerance (seconds) applied to `exp`, `nbf` and `iat`. |
| `broker.jwt_verification.trusted_issuers` | list of TrustedIssuerConfig | — | `JENTIC__BROKER__JWT_VERIFICATION__TRUSTED_ISSUERS` | Identity providers whose self-contained JWTs the broker accepts, each verified against its published JWKS. When non-empty this takes precedence over `broker.jwt_secret`; empty leaves the JWKS path off. |
| `broker.jwt_verification.trusted_issuers.<n>.issuer` | string | *required* | `JENTIC__BROKER__JWT_VERIFICATION__TRUSTED_ISSUERS__<N>__ISSUER` | Expected `iss` claim of tokens from this issuer (exact match). |
| `broker.jwt_verification.trusted_issuers.<n>.jwks_url` | string | *required* | `JENTIC__BROKER__JWT_VERIFICATION__TRUSTED_ISSUERS__<N>__JWKS_URL` | URL of the issuer's published JWKS. Must be https:// when JENTIC_ENV=production; plain http:// is accepted outside production (local/CI stacks) and logs a warning at config load. |
| `broker.jwt_verification.trusted_issuers.<n>.algorithms` | list of string | — | `JENTIC__BROKER__JWT_VERIFICATION__TRUSTED_ISSUERS__<N>__ALGORITHMS` | Signature algorithms accepted from this issuer. Asymmetric only (RS*, ES*, PS*, EdDSA); HMAC and `none` are rejected at load. |
| `broker.resilience.max_in_flight` | integer | `200` | `JENTIC__BROKER__RESILIENCE__MAX_IN_FLIGHT` | Hard admission cap on concurrently executing brokered calls, **per broker process** — replicas multiply it. At the cap, new requests are shed with 503 + `Retry-After: shed_retry_after_s`. |
| `broker.resilience.shed_retry_after_s` | integer | `5` | `JENTIC__BROKER__RESILIENCE__SHED_RETRY_AFTER_S` | `Retry-After` (seconds) returned with the 503 when admission sheds at `max_in_flight`. |
| `broker.resilience.request_deadline_s` | number | `30.0` | `JENTIC__BROKER__RESILIENCE__REQUEST_DEADLINE_S` | Overall wall-clock budget (seconds) for one upstream call, enforced by the always-on DeadlineRunner outside the circuit breaker — distinct from the per-attempt connect/read timeout on the transport client. Exceeding it returns 504 with a `wait` agent directive; 0 disables the budget. Size ABOVE upstream read timeouts so a single healthy slow attempt isn't pre-empted by the envelope deadline. |
| `broker.resilience.readiness_saturation_threshold` | number (> 0.0, <= 1.0) | `0.9` | `JENTIC__BROKER__RESILIENCE__READINESS_SATURATION_THRESHOLD` | Fraction of `max_in_flight` at/above which `/ready` reports unready, so the LB drains this instance before it hits the hard admission shed wall. Kept < 1.0 for that headroom. |
| `broker.resilience.upstream.connect_timeout_s` | number | `5.0` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__CONNECT_TIMEOUT_S` | Timeout (seconds) for establishing an upstream connection. |
| `broker.resilience.upstream.read_timeout_s` | number | `30.0` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__READ_TIMEOUT_S` | Per-read *between-bytes* gap timeout (seconds, httpx semantics) on upstream responses — not a whole-stream cap. Pairs with `broker.resilience.request_deadline_s`, which must be sized above it so one healthy slow attempt isn't pre-empted by the envelope deadline. |
| `broker.resilience.upstream.write_timeout_s` | number | `30.0` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__WRITE_TIMEOUT_S` | Per-write timeout (seconds) while sending a request body upstream. |
| `broker.resilience.upstream.pool_timeout_s` | number | `2.0` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__POOL_TIMEOUT_S` | How long (seconds) a call waits for a free connection from the shared pool before failing. |
| `broker.resilience.upstream.http2` | boolean | `true` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__HTTP2` | Negotiate HTTP/2 via ALPN, falling back to HTTP/1.1 when the upstream does not offer it. Some upstreams (APNs, certain CDNs) refuse 1.1; set false only to pin HTTP/1.1. |
| `broker.resilience.upstream.max_connections` | integer | `200` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__MAX_CONNECTIONS` | Maximum open upstream connections in the shared client pool. |
| `broker.resilience.upstream.max_keepalive` | integer | `50` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__MAX_KEEPALIVE` | Maximum idle keep-alive connections the shared pool retains. |
| `broker.resilience.upstream.max_per_host` | integer | `50` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__MAX_PER_HOST` | Maximum concurrent calls to one upstream host, so one slow upstream cannot drain the whole connection pool. |
| `broker.resilience.upstream.max_request_bytes` | integer | `10485760` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__MAX_REQUEST_BYTES` | Default cap (bytes) on an execute request body, for content types without an entry in `max_request_bytes_by_type`; over it the broker answers 413. |
| `broker.resilience.upstream.max_request_bytes_by_type` | map of integer | — | `JENTIC__BROKER__RESILIENCE__UPSTREAM__MAX_REQUEST_BYTES_BY_TYPE` | Per-Content-Type request-body caps (bytes), matched most specific first: exact type (`application/json`), then wildcard (`audio/*`), then `max_request_bytes`. A missing or unknown type gets the default, never unbounded. |
| `broker.resilience.upstream.max_response_bytes` | integer | `10485760` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__MAX_RESPONSE_BYTES` | Cap (bytes) on an upstream response body, enforced while reading: the connection is aborted as soon as it is exceeded. 0 disables the cap (unbounded; not recommended). |
| `broker.resilience.upstream.stream_passthrough_enabled` | boolean | `true` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__STREAM_PASSTHROUGH_ENABLED` | Stream upstream responses straight through to the client instead of buffering them. Applies to synchronous executions only; async executions always buffer, and so do executions with an `Idempotency-Key` when `broker.idempotency.enabled` is true. Off forces buffering everywhere. |
| `broker.resilience.upstream.transfer_deadline_s` | number | `300.0` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__TRANSFER_DEADLINE_S` | Total time (seconds) a streamed response body may take to transfer, so a steady trickle or a slow-draining client cannot hold an upstream connection forever; the stream is aborted when it is exceeded. Unlike `read_timeout_s` this bounds the whole body. 0 disables the deadline. |
| `broker.resilience.rate_limit.enabled` | boolean | `true` | `JENTIC__BROKER__RESILIENCE__RATE_LIMIT__ENABLED` | Rate-limit broker executions per caller; over the limit the broker answers 429 with `RateLimit-*` and `Retry-After` headers. Per instance on the memory state backend, cluster-wide on Redis. |
| `broker.resilience.rate_limit.default_rpm` | integer | `600` | `JENTIC__BROKER__RESILIENCE__RATE_LIMIT__DEFAULT_RPM` | Sustained refill rate of each caller's token bucket, in requests per minute. |
| `broker.resilience.rate_limit.burst` | integer | `100` | `JENTIC__BROKER__RESILIENCE__RATE_LIMIT__BURST` | Token-bucket capacity: the most requests a caller can spend at once. Also the `RateLimit-Limit` header value. |
| `broker.resilience.circuit_breaker.enabled` | boolean | `true` | `JENTIC__BROKER__RESILIENCE__CIRCUIT_BREAKER__ENABLED` | Track upstream failures per host and fast-fail calls to a host whose circuit is open (5xx responses and transport errors count as failures). |
| `broker.resilience.circuit_breaker.enforcement_mode` | "blocking" \| "observation" | `"blocking"` | `JENTIC__BROKER__RESILIENCE__CIRCUIT_BREAKER__ENFORCEMENT_MODE` | `blocking` fast-fails calls while the circuit is open; `observation` still calls the upstream and only counts `circuit_would_block_total`, to validate thresholds before blocking. |
| `broker.resilience.circuit_breaker.failure_ratio` | number | `0.5` | `JENTIC__BROKER__RESILIENCE__CIRCUIT_BREAKER__FAILURE_RATIO` | Failure fraction within `window_s` at which the circuit opens. |
| `broker.resilience.circuit_breaker.min_calls` | integer | `10` | `JENTIC__BROKER__RESILIENCE__CIRCUIT_BREAKER__MIN_CALLS` | Minimum calls in the window before the failure ratio can open the circuit, so a single early failure does not trip it. |
| `broker.resilience.circuit_breaker.window_s` | integer | `30` | `JENTIC__BROKER__RESILIENCE__CIRCUIT_BREAKER__WINDOW_S` | Rolling window (seconds) over which failures and totals are counted. |
| `broker.resilience.circuit_breaker.cooldown_s` | integer | `15` | `JENTIC__BROKER__RESILIENCE__CIRCUIT_BREAKER__COOLDOWN_S` | Seconds an open circuit fast-fails calls before the upstream is tried again. |
| `broker.resilience.retry.enabled` | boolean | `false` | `JENTIC__BROKER__RESILIENCE__RETRY__ENABLED` | Retry failed upstream attempts when safe: connect failures for any method, post-send failures (read timeouts, `retry_statuses`) only for idempotent HTTP methods. Bounded by the request deadline. Streamed executions are never retried, and streaming is the default synchronous path while `broker.resilience.upstream.stream_passthrough_enabled` is true. |
| `broker.resilience.retry.max_attempts` | integer | `3` | `JENTIC__BROKER__RESILIENCE__RETRY__MAX_ATTEMPTS` | Total attempts including the first (3 = the initial call plus up to 2 retries). |
| `broker.resilience.retry.base_backoff_s` | number | `0.2` | `JENTIC__BROKER__RESILIENCE__RETRY__BASE_BACKOFF_S` | Base delay (seconds) of the exponential backoff `base_backoff_s * 2**(attempt-1)`, capped at `max_backoff_s`, then fully jittered and clamped to the request deadline. |
| `broker.resilience.retry.max_backoff_s` | number | `5.0` | `JENTIC__BROKER__RESILIENCE__RETRY__MAX_BACKOFF_S` | Cap (seconds) on one retry backoff before jitter. |
| `broker.resilience.retry.retry_statuses` | list of integer | — | `JENTIC__BROKER__RESILIENCE__RETRY__RETRY_STATUSES` | Upstream status codes treated as transient and retried; an upstream `Retry-After` on them is honoured, capped at the request deadline. |
| `broker.idempotency.enabled` | boolean | `true` | `JENTIC__BROKER__IDEMPOTENCY__ENABLED` | Honour the `Idempotency-Key` request header on broker executions: a repeat of a completed key replays the stored response instead of calling the upstream again. Off ignores the header. |
| `broker.idempotency.ttl_s` | number (> 0) \| null | `null` | `JENTIC__BROKER__IDEMPOTENCY__TTL_S` | Replay window of a completed Idempotency-Key record, in seconds. Unset, it is 3600 (1 h) on the memory shared-state backend and 86400 (24 h) on any other backend. On memory, every record is held in process for this long, so the TTL divides the sustained idempotent request rate the idem budget can hold. |
| `broker.idempotency.pending_ttl_s` | number | `35.0` | `JENTIC__BROKER__IDEMPOTENCY__PENDING_TTL_S` | Lifetime (seconds) of the in-flight claim on an `Idempotency-Key`, so a broker killed mid-request frees the key within seconds. Size it just above `broker.resilience.request_deadline_s`. |
| `broker.idempotency.max_response_bytes` | integer | `262144` | `JENTIC__BROKER__IDEMPOTENCY__MAX_RESPONSE_BYTES` | Largest response body (bytes) stored for byte-for-byte replay. Larger responses are recorded without their body: a replay still prevents the duplicate call but does not return the body. |
| `broker.egress.allowed_private_subnets` | list of string | — | `JENTIC__BROKER__EGRESS__ALLOWED_PRIVATE_SUBNETS` | CIDRs exempted from the private-IP egress block (e.g. `["10.50.0.0/16"]`). The cloud-metadata and platform-credential IPs (e.g. 169.254.169.254, 169.254.170.2, fd00:ec2::254, 100.100.100.200) are never exempted, even when a listed range covers them. Accepts a YAML list or a comma-separated string. |
| `broker.egress.allowed_internal_domains` | list of string | — | `JENTIC__BROKER__EGRESS__ALLOWED_INTERNAL_DOMAINS` | Domain suffixes (e.g. `[".svc.cluster.local"]`) whose resolved private IP is permitted. The resolved IP must still fall in an allowed subnet. Accepts a YAML list or a comma-separated string. |
| `broker.egress.dns_pinning_enabled` | boolean | `true` | `JENTIC__BROKER__EGRESS__DNS_PINNING_ENABLED` | Pin the outbound connection to the IP validated at connect time, closing the DNS-rebinding TOCTOU between pre-request validation and the runner's own resolution. Disable only to debug egress issues. |
| `broker.server_timing` | boolean | `false` | `JENTIC__BROKER__SERVER_TIMING` | Emit a W3C `Server-Timing` header on execute responses with per-stage durations (`jentic-auth`, `jentic-egress`, `jentic-discover`, `jentic-rules`, `jentic-body`, `jentic-idempotency`, `jentic-cred`, `jentic-upstream`, `jentic-persist`, `jentic-total`) for authenticated callers. A broker-generated error raised before the upstream call lists the stages that ran without durations. `jentic-upstream` includes the circuit-breaker check, so a circuit-open `503` carries durations even though no upstream request was sent. Off by default: timing data aids side-channel probing and fingerprinting, so enable it only where callers are trusted with it (e.g. while diagnosing latency). Streaming responses carry only the stages known before the headers are sent (no `jentic-persist`). |

## `control`

Control surface configuration. `egress` is the allowlist for the platform's outbound OAuth and credential calls. Each `control.egress` key left unset takes its value from the matching `broker.egress` key. The policy is read by whichever process makes the call: the control surface for credential and OAuth app writes, the connect-session confirm, code exchange, device authorization and the identity probe, and the broker surface for the token refresh it runs before injecting a credential. With one process per surface, set `control.egress` (and the `broker.egress` keys it inherits) identically on both. Unknown subkeys of `control:` are ignored, so a section written for another release keeps validating; `control.egress` rejects unknown keys so a mistyped allowlist key fails at startup instead of silently allowing less.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `control.connect.manual_flows_enabled` | boolean | `true` | `JENTIC__CONTROL__CONNECT__MANUAL_FLOWS_ENABLED` | Allow connect sessions that target a registry API (rather than a vendor-registry key), where a human enters the credential, or brings an OAuth app, when approving them in the dashboard. On by default; while off, `:connect` refuses API targets with `manual_flows_disabled`. |
| `control.connect.manual_flows_ttl_hours` | integer (>= 1) | `72` | `JENTIC__CONTROL__CONNECT__MANUAL_FLOWS_TTL_HOURS` | Hours an API-target connect session (a human-entered credential, or an OAuth API waiting for an app) stays open before it expires. Vendor OAuth sessions keep their fixed 30-minute lifetime. |
| `control.connect.max_open_sessions_per_agent` | integer (>= 1) | `10` | `JENTIC__CONTROL__CONNECT__MAX_OPEN_SESSIONS_PER_AGENT` | Most open connect sessions one agent may hold at once while `manual_flows_enabled` is on; one more `:connect` gets 429 `too_many_open_sessions`. |
| `control.connect.max_open_sessions_per_owner` | integer (>= 1) | `50` | `JENTIC__CONTROL__CONNECT__MAX_OPEN_SESSIONS_PER_OWNER` | Most open connect sessions across one user and the agents they own while `manual_flows_enabled` is on; one more `:connect` gets 429 `too_many_open_sessions`. |
| `control.connect.rejection_cooldown_hours` | integer (>= 0) | `0` | `JENTIC__CONTROL__CONNECT__REJECTION_COOLDOWN_HOURS` | Hours after a human rejects an agent's connect or access-change request during which the agent's repeat ask for the same target (or binding) gets 429 `recently_rejected`. Off (0) by default: an agent may ask again at once. |
| `control.egress.allowed_private_subnets` | list of string \| null | `null` | `JENTIC__CONTROL__EGRESS__ALLOWED_PRIVATE_SUBNETS` | CIDRs exempted from the private-IP block for these calls (e.g. `["10.50.0.0/16"]`); unset inherits `broker.egress.allowed_private_subnets`, and `[]` blocks every private range for these calls even when the broker allows one. The cloud-metadata and platform-credential IPs are never exempted. Accepts a YAML list or a comma-separated string. |
| `control.egress.allowed_internal_domains` | list of string \| null | `null` | `JENTIC__CONTROL__EGRESS__ALLOWED_INTERNAL_DOMAINS` | Domain suffixes (e.g. `[".corp.internal"]`) whose resolved private IP is permitted for these calls; unset inherits `broker.egress.allowed_internal_domains`. The resolved IP must still fall in an allowed subnet. Accepts a YAML list or a comma-separated string. |

## `ingest`

Spec ingestion settings (fetch limits, timeouts, egress policy).

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `ingest.max_spec_bytes` | integer | `26214400` | `JENTIC__INGEST__MAX_SPEC_BYTES` | Largest API spec (bytes) accepted from a URL fetch or an inline import source, fetched for a catalog preview, or produced by applying an overlay (default 25 MiB). |
| `ingest.fetch_timeout_s` | number | `30.0` | `JENTIC__INGEST__FETCH_TIMEOUT_S` | Timeout (seconds) for fetching a spec or the catalog manifest from a URL. |
| `ingest.max_redirects` | integer | `5` | `JENTIC__INGEST__MAX_REDIRECTS` | Maximum HTTP redirects followed when fetching a spec or the catalog manifest; each hop is re-checked against the egress policy. |
| `ingest.egress.allowed_private_subnets` | list of string | — | `JENTIC__INGEST__EGRESS__ALLOWED_PRIVATE_SUBNETS` | CIDRs exempted from the private-IP egress block (e.g. `["10.50.0.0/16"]`). The cloud-metadata and platform-credential IPs (e.g. 169.254.169.254, 169.254.170.2, fd00:ec2::254, 100.100.100.200) are never exempted, even when a listed range covers them. Accepts a YAML list or a comma-separated string. |
| `ingest.egress.allowed_internal_domains` | list of string | — | `JENTIC__INGEST__EGRESS__ALLOWED_INTERNAL_DOMAINS` | Domain suffixes (e.g. `[".svc.cluster.local"]`) whose resolved private IP is permitted. The resolved IP must still fall in an allowed subnet. Accepts a YAML list or a comma-separated string. |
| `ingest.egress.dns_pinning_enabled` | boolean | `true` | `JENTIC__INGEST__EGRESS__DNS_PINNING_ENABLED` | Pin the outbound connection to the IP validated at connect time, closing the DNS-rebinding TOCTOU between pre-request validation and the runner's own resolution. Disable only to debug egress issues. |
| `ingest.max_import_request_bytes` | integer (> 0) \| null | `null` | `JENTIC__INGEST__MAX_IMPORT_REQUEST_BYTES` | Cap on the raw `POST /apis` request body (all sources together). Unset means twice `max_spec_bytes`, which covers one maximum-size inline spec with typical JSON-string escaping; raise it for batches of heavily escaped specs (`\uXXXX` or HTML-escaped content). Each inline source's content is separately capped at `max_spec_bytes`. |

## `catalog`

Public API catalog settings (manifest source + staleness).

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `catalog.manifest_url` | string | `"https://raw.githubusercontent.com/jentic/jentic-public-a…` | `JENTIC__CATALOG__MANIFEST_URL` | Manifest source for the public API catalog. Full default: https://raw.githubusercontent.com/jentic/jentic-public-apis/main/apis/openapi/apis.json |
| `catalog.manifest_max_age_seconds` | integer | `86400` | `JENTIC__CATALOG__MANIFEST_MAX_AGE_SECONDS` | Age (seconds) after which the cached catalog manifest is refreshed on the next catalog read. 0 disables automatic refresh (only `POST /catalog:refresh`). |
| `catalog.update_check_interval_seconds` | integer | `86400` | `JENTIC__CATALOG__UPDATE_CHECK_INTERVAL_SECONDS` | Seconds between update-check sweeps, which conditionally re-fetch the spec URLs of upstream-tracked APIs and emit `catalog.update_available` when an upstream spec changed; each API is probed at most once per interval. `POST /catalog:refresh` also triggers a sweep. 0 disables the check entirely (no events, no egress). |
| `catalog.update_sweep_deadline_seconds` | integer | `300` | `JENTIC__CATALOG__UPDATE_SWEEP_DEADLINE_SECONDS` | Wall-clock budget (seconds) for one update-check sweep; it stops when exceeded. |
| `catalog.update_sweep_max_concurrency` | integer | `4` | `JENTIC__CATALOG__UPDATE_SWEEP_MAX_CONCURRENCY` | Maximum concurrent spec probes in one update-check sweep. Keep it below the registry database pool size so sweeps never starve request traffic. |
| `catalog.update_sweep_jitter_ratio` | number (>= 0.0, <= 1.0) | `0.15` | `JENTIC__CATALOG__UPDATE_SWEEP_JITTER_RATIO` | Random extra delay added to each sweep interval, as a fraction of it (next sweep after `interval * (1 + uniform(0, ratio))`), so replicas started together drift apart. 0 disables jitter. |
| `catalog.preview_cache_ttl_seconds` | integer (>= 0) | `300` | `JENTIC__CATALOG__PREVIEW_CACHE_TTL_SECONDS` | How long a catalog preview's upstream spec projection is reused before refetching. Zero disables the cache. The cache is per process: `POST /catalog:refresh` clears it only on the process that serves the refresh, so other workers or replicas can serve the previous projection until this TTL expires. |
| `catalog.preview_cache_failure_ttl_seconds` | integer (>= 0) | `30` | `JENTIC__CATALOG__PREVIEW_CACHE_FAILURE_TTL_SECONDS` | How long a failed catalog preview fetch (upstream unavailable, oversized or unparseable spec) is remembered per spec URL before it is retried, so repeated previews of a broken spec do not refetch it on every request. Zero retries on the next request. Ignored when the preview cache is disabled. |
| `catalog.preview_cache_max_entries` | integer (>= 0) | `16` | `JENTIC__CATALOG__PREVIEW_CACHE_MAX_ENTRIES` | Maximum number of catalog spec projections held in memory per process (least recently used evicted first). Zero disables the cache. |

## `credentials`

Credentials subsystem configuration.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `credentials.encryption.active_id` | string | `"v1"` | `JENTIC__CREDENTIALS__ENCRYPTION__ACTIVE_ID` | Id of the key that encrypts new values; must match an entry in `entries`. Older keys stay usable for decryption, so rotate by adding a key and switching this id. |
| `credentials.encryption.entries` | list of EncryptionKey | — | `JENTIC__CREDENTIALS__ENCRYPTION__ENTRIES` | AES-256-GCM keys (32 bytes, base64) for credentials at rest. Every entry can decrypt; ids must be unique. Empty still boots, but credential writes and approval-gated executions then fail. Never remove a key that still has data encrypted under it. |
| `credentials.encryption.entries.<n>.id` | string | *required* | `JENTIC__CREDENTIALS__ENCRYPTION__ENTRIES__<N>__ID` | Key id (`[a-zA-Z0-9_-]+`), stored as the prefix of every value encrypted with this key so it can be decrypted after rotation. |
| `credentials.encryption.entries.<n>.material` | string (secret) \| null | `null` | `JENTIC__CREDENTIALS__ENCRYPTION__ENTRIES__<N>__MATERIAL` | Base64-encoded key material, inline in the config. |
| `credentials.encryption.entries.<n>.material_env` | string \| null | `null` | `JENTIC__CREDENTIALS__ENCRYPTION__ENTRIES__<N>__MATERIAL_ENV` | Name of an environment variable holding the base64-encoded key material. |
| `credentials.encryption.entries.<n>.material_file` | string \| null | `null` | `JENTIC__CREDENTIALS__ENCRYPTION__ENTRIES__<N>__MATERIAL_FILE` | Path to a regular file holding the base64-encoded key material (docker/k8s secret mount, systemd LoadCredential path). |
| `credentials.providers` | map of DirectOAuth2ProviderConfig \| PipedreamProviderConfig | — | `JENTIC__CREDENTIALS__PROVIDERS` | Credential providers keyed by provider id, which credentials name in their `provider` field; `kind` selects the provider type. The built-in `static` and `device_authorization` providers are always present, and the connect flow uses the `direct_oauth2` entry. |
| `credentials.providers.<name>.kind` | "direct_oauth2" \| "pipedream" | `"direct_oauth2"` | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__KIND` |  |
| `credentials.providers.<name>.redirect_uri` | string \| null | `null` | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__REDIRECT_URI` | OAuth callback URL registered with the IdP. Unset, it is derived per request as `{server.public_base_url or request origin}/credentials/oauth/callback`; set it only when the public origin differs from `server.public_base_url` (e.g. a proxy the app cannot see). Matched byte-for-byte by the IdP. |
| `credentials.providers.<name>.default_scopes` | list of string | — | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__DEFAULT_SCOPES` | Scopes requested on the authorize URL when a connect request names none. |
| `credentials.providers.<name>.expiry_skew_seconds` | integer | `60` | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__EXPIRY_SKEW_SECONDS` | How many seconds before an access token's real expiry the broker refreshes it. Applied at comparison time; the stored expiry is never shifted. |
| `credentials.providers.<name>.authorize_extra_params` | map of string | — | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__AUTHORIZE_EXTRA_PARAMS` | Extra query parameters appended to every authorize URL. The defaults force the consent screen and ask Google for a refresh token; for Microsoft, Okta or Auth0 add the `offline_access` scope to `default_scopes` instead. Keys that collide with the OAuth2 parameters the provider builds (`response_type`, `client_id`, `redirect_uri`, `state`, `scope`) override them and break the connect flow. |
| `credentials.providers.<name>.project_id` | string | *required* | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__PROJECT_ID` | Pipedream project id. |
| `credentials.providers.<name>.environment` | "production" \| "development" | `"production"` | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__ENVIRONMENT` | Pipedream project environment the connections live in. |
| `credentials.providers.<name>.client_id` | string | *required* | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__CLIENT_ID` | Pipedream OAuth client id. |
| `credentials.providers.<name>.client_secret` | string (secret) | *required* | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__CLIENT_SECRET` | Pipedream OAuth client secret. |
| `credentials.providers.<name>.connect_base_url` | string | `"https://api.pipedream.com/v1"` | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__CONNECT_BASE_URL` | Base URL of the Pipedream Connect API. |
| `credentials.connect.state_secret` | string (secret) | `""` | `JENTIC__CREDENTIALS__CONNECT__STATE_SECRET` | Key that signs the OAuth connect `state` parameter. Required in production (empty or placeholder values are rejected at startup); in development an ephemeral per-process value is generated when empty. |
| `credentials.connect.state_ttl_seconds` | integer | `600` | `JENTIC__CREDENTIALS__CONNECT__STATE_TTL_SECONDS` | Seconds a signed connect `state` (and its browser-binding cookie) stays valid, i.e. how long a user has to finish the provider's consent screen. |

## `vendors`

Top-level vendor auth registry.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `vendors.entries` | map of VendorAuthConfig | — | `JENTIC__VENDORS__ENTRIES` | Verified vendors offered by the agent-driven connect flow, keyed by a short slug (e.g. `github`). |
| `vendors.entries.<name>.vendor` | string | *required* | `JENTIC__VENDORS__ENTRIES__<NAME>__VENDOR` | Catalog API id of the vendor's API, in `<domain>/<name>` form (e.g. `github.com/api.github.com`); credentials created through this entry are scoped to it. |
| `vendors.entries.<name>.display_name` | string | *required* | `JENTIC__VENDORS__ENTRIES__<NAME>__DISPLAY_NAME` | Vendor name shown in the connect picker. |
| `vendors.entries.<name>.flows` | list of VendorDeviceAuthorizationFlowConfig \| VendorAuthorizationCodeFlowConfig | *required* | `JENTIC__VENDORS__ENTRIES__<NAME>__FLOWS` | OAuth flows the vendor supports, selected by `kind`. The first entry is used when the caller expresses no preference. |
| `vendors.entries.<name>.flows.<n>.kind` | "device_authorization" \| "authorization_code" | `"device_authorization"` | `JENTIC__VENDORS__ENTRIES__<NAME>__FLOWS__<N>__KIND` |  |
| `vendors.entries.<name>.flows.<n>.client_id` | string | *required* | `JENTIC__VENDORS__ENTRIES__<NAME>__FLOWS__<N>__CLIENT_ID` | Platform-shipped OAuth application id (public client, no secret). |
| `vendors.entries.<name>.flows.<n>.authorization_endpoint` | string | *required* | `JENTIC__VENDORS__ENTRIES__<NAME>__FLOWS__<N>__AUTHORIZATION_ENDPOINT` | The vendor's device authorization endpoint URL. |
| `vendors.entries.<name>.flows.<n>.token_endpoint` | string | *required* | `JENTIC__VENDORS__ENTRIES__<NAME>__FLOWS__<N>__TOKEN_ENDPOINT` | The vendor's token endpoint URL. |
| `vendors.entries.<name>.flows.<n>.client_secret` | string (secret) | *required* | `JENTIC__VENDORS__ENTRIES__<NAME>__FLOWS__<N>__CLIENT_SECRET` | OAuth application secret used to exchange the code at the token endpoint. |
| `vendors.entries.<name>.flows.<n>.authorize_url` | string | *required* | `JENTIC__VENDORS__ENTRIES__<NAME>__FLOWS__<N>__AUTHORIZE_URL` | The vendor's authorization endpoint URL. |
| `vendors.entries.<name>.flows.<n>.token_url` | string | *required* | `JENTIC__VENDORS__ENTRIES__<NAME>__FLOWS__<N>__TOKEN_URL` | The vendor's token endpoint URL. |
| `vendors.entries.<name>.scopes` | list of VendorScopeConfig | — | `JENTIC__VENDORS__ENTRIES__<NAME>__SCOPES` | The vendor's scope catalog offered on the review page. |
| `vendors.entries.<name>.scopes.<n>.name` | string | *required* | `JENTIC__VENDORS__ENTRIES__<NAME>__SCOPES__<N>__NAME` | Scope string as the vendor expects it on the authorize request. |
| `vendors.entries.<name>.scopes.<n>.classification` | "read" \| "write" \| "admin" | `"read"` | `JENTIC__VENDORS__ENTRIES__<NAME>__SCOPES__<N>__CLASSIFICATION` | Risk class shown on the review page: write and admin scopes carry a warning. |
| `vendors.entries.<name>.scopes.<n>.default` | boolean | `false` | `JENTIC__VENDORS__ENTRIES__<NAME>__SCOPES__<N>__DEFAULT` | Pre-select this scope on the review page. |
| `vendors.entries.<name>.scopes.<n>.description` | string | `""` | `JENTIC__VENDORS__ENTRIES__<NAME>__SCOPES__<N>__DESCRIPTION` | Human-facing explanation of the scope, shown on the review page. |
| `vendors.entries.<name>.identity_probe.endpoint` | string | *required* | `JENTIC__VENDORS__ENTRIES__<NAME>__IDENTITY_PROBE__ENDPOINT` | URL called with the new access token to identify the connected account. |
| `vendors.entries.<name>.identity_probe.method` | "GET" \| "POST" | `"GET"` | `JENTIC__VENDORS__ENTRIES__<NAME>__IDENTITY_PROBE__METHOD` | HTTP method of the identity probe. |
| `vendors.entries.<name>.identity_probe.identity_field` | string | *required* | `JENTIC__VENDORS__ENTRIES__<NAME>__IDENTITY_PROBE__IDENTITY_FIELD` | Dotted JSON path of the identity value in the probe response (e.g. `login`). |
| `vendors.entries.<name>.identity_probe.display_template` | string | *required* | `JENTIC__VENDORS__ENTRIES__<NAME>__IDENTITY_PROBE__DISPLAY_TEMPLATE` | Python `str.format` template for the `connected_as` label; it can reference top-level response fields and `{value}` (the extracted identity value). |

## `search`

Search configuration. The built-in mode is "lexical" (BM25 on SQLite, native full-text on PostgreSQL). `search_mode` is validated against the registered SearchStrategy set at resolve time (`resolve_strategy`), so an unknown mode fails loudly with the available modes for the active dialect rather than at config load. Additional modes (e.g. "semantic", "vector") can be registered via `register_strategy` without editing this schema.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `search.enabled` | boolean | `true` | `JENTIC__SEARCH__ENABLED` | Build the lexical search text for operations at import time. APIs imported while this is off are not findable by lexical search until re-imported. |
| `search.search_enabled` | boolean | `true` | `JENTIC__SEARCH__SEARCH_ENABLED` | Serve search queries. Off, search requests fail with a search-disabled error; indexing at import is controlled separately by `enabled`. |
| `search.search_mode` | string | `"lexical"` | `JENTIC__SEARCH__SEARCH_MODE` | Search strategy name, resolved per database dialect against the registered strategies. `lexical` (BM25 on SQLite, native full-text on PostgreSQL) is built in; an unknown mode fails on first use with the available modes listed. |

## `security`

Platform security event thresholds.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `security.auth_failure_event_threshold` | integer (>= 1) | `10` | `JENTIC__SECURITY__AUTH_FAILURE_EVENT_THRESHOLD` | Broker execute requests from one actor refused within a clock minute for lacking the execute permission before a `security.unauthorized_access_attempt` event is emitted (at most one per actor per minute). |
| `security.credential_expiring_soon_window_h` | integer (>= 1) | `72` | `JENTIC__SECURITY__CREDENTIAL_EXPIRING_SOON_WINDOW_H` | Hours before an OAuth token's expiry at which `credential.expiring_soon` events start; the token stays in that warning state until it expires. |
| `security.credential_expiry_sweep_interval_ticks` | integer (>= 1) | `60` | `JENTIC__SECURITY__CREDENTIAL_EXPIRY_SWEEP_INTERVAL_TICKS` | Run the credential-expiry scan every Nth worker tick. One tick is about `worker.poll_interval_s`, so the default is roughly every 2 minutes. |
| `security.execution_repeated_failure_threshold` | integer (>= 1) | `5` | `JENTIC__SECURITY__EXECUTION_REPEATED_FAILURE_THRESHOLD` | Failed executions for one actor, credential and operation within `execution_repeated_failure_window_s` before an `execution.repeated_failure` event is emitted. |
| `security.execution_repeated_failure_window_s` | integer (>= 1) | `300` | `JENTIC__SECURITY__EXECUTION_REPEATED_FAILURE_WINDOW_S` | Rolling window (seconds) over which repeated failures are counted; at most one `execution.repeated_failure` event is emitted per window. |
| `security.execution_repeated_failure_critical_threshold` | integer (>= 1) | `20` | `JENTIC__SECURITY__EXECUTION_REPEATED_FAILURE_CRITICAL_THRESHOLD` | Failure count within the window at or above which the `execution.repeated_failure` event escalates from `error` to `critical` severity. Keep it above the base threshold. |

## `telemetry`

Anonymous product-telemetry settings. Defaults to **OFF**: an instance whose config omits this block (non-onboarded or hand-rolled) sends nothing. The onboarding CLI writes `enabled` explicitly (a yes-default `[Y]/n` prompt), which is where the on-by-default install experience comes from. `instance_id` seeds the durable admin-DB identity row on first startup for opted-in instances. `host_os` is the operator's OS family, stamped by the CLI at install time so a Docker-run instance reports the host's OS rather than the container's; sent once per boot, on the `instance_booted` event.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `telemetry.enabled` | boolean | `false` | `JENTIC__TELEMETRY__ENABLED` | Send anonymous product telemetry. Off when the block is omitted; the onboarding CLI writes it explicitly. |
| `telemetry.instance_id` | string \| null | `null` | `JENTIC__TELEMETRY__INSTANCE_ID` | Opaque instance id reported with telemetry (at most 64 characters). When set it is authoritative and overwrites the id stored in the admin database; unset, a random UUID is generated on first startup and kept. |
| `telemetry.host_os` | string \| null | `null` | `JENTIC__TELEMETRY__HOST_OS` | Host OS family stamped by the onboarding CLI, so an instance running in Docker reports the operator's OS rather than the container's Linux. Unset falls back to runtime detection; unrecognised values are reported as `other`. Sent once per boot, on the `instance_booted` event. |
| `telemetry.endpoint` | string | `"https://api.jentic.com/api/v1"` | `JENTIC__TELEMETRY__ENDPOINT` |  |
| `telemetry.flush_interval_s` | number | `30.0` | `JENTIC__TELEMETRY__FLUSH_INTERVAL_S` |  |
| `telemetry.max_batch` | integer | `100` | `JENTIC__TELEMETRY__MAX_BATCH` |  |
| `telemetry.queue_max` | integer | `10000` | `JENTIC__TELEMETRY__QUEUE_MAX` |  |
| `telemetry.request_timeout_s` | number | `5.0` | `JENTIC__TELEMETRY__REQUEST_TIMEOUT_S` |  |

## `release_check`

"Update available" check for the running jentic-one build itself. Powers `GET /system/version`: the backend asks GitHub for the newest published release of `repo` and compares it against the running build so the web console can surface an "update available" banner (and the user menu can always show the current version). This covers *jentic-one's own* release; `CatalogConfig` tracks the public *API catalog* instead. Runs only on a `local` backend (a self-hosted install the operator can actually update); the hosted platform (`server.backend == "remote"`) skips it. The result is cached in-process for `cache_ttl_seconds` (fetch-on-read, no background job), so at most one GitHub request happens per TTL regardless of how many clients poll. Every failure degrades to "latest unknown" (no banner) rather than erroring — the version probe must never break the app.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `release_check.enabled` | boolean | `true` | `JENTIC__RELEASE_CHECK__ENABLED` | Check GitHub for a newer jentic-one release so the web console can show an "update available" banner. Runs only when `server.backend` is `local`. |
| `release_check.repo` | string | `"jentic/jentic-one"` | `JENTIC__RELEASE_CHECK__REPO` |  |
| `release_check.cache_ttl_seconds` | integer | `21600` | `JENTIC__RELEASE_CHECK__CACHE_TTL_SECONDS` | Seconds the latest-release lookup is cached in process (default 6 h), so at most one GitHub request is made per TTL. 0 disables the check entirely (air-gapped installs, no egress). |

## `entitlement`

AWS Marketplace license gate for the Marketplace-listed deployment. Powers the entitlement checker (`integrations/aws_marketplace`): on startup, and every `refresh_interval_seconds` after, the process asks AWS whether this deployment's Marketplace subscription is still active, and locks the HTTP surface (503, health excepted) when it definitively is not. Defaults to **OFF**: a non-Marketplace install that omits this block wires nothing and never makes an AWS call. Failure posture: an *unreachable* or *erroring* AWS API is never grounds for lockout by itself — the last definitive verdict holds for `grace_period_seconds` before the gate fails closed. Only an explicit "not entitled" answer from AWS locks out immediately.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `entitlement.enabled` | boolean | `false` | `JENTIC__ENTITLEMENT__ENABLED` | Gate the HTTP surface on an active AWS Marketplace subscription (503 except health checks when AWS reports the deployment is not entitled). Off makes no AWS calls. |
| `entitlement.product_code` | string \| null | `null` | `JENTIC__ENTITLEMENT__PRODUCT_CODE` | Marketplace product code issued by the AWS Marketplace portal for the container product. Required when `enabled`. |
| `entitlement.region` | string | `"us-east-1"` | `JENTIC__ENTITLEMENT__REGION` | AWS region the License Manager / Metering Service calls go to. |
| `entitlement.pricing_model` | "usage" \| "contract" | `"contract"` | `JENTIC__ENTITLEMENT__PRICING_MODEL` | Which Marketplace API the check calls: `contract` uses License Manager `CheckoutLicense` (requires `license_sku`); `usage` uses Metering Service `RegisterUsage` (hourly/usage pricing). |
| `entitlement.refresh_interval_seconds` | integer | `3600` | `JENTIC__ENTITLEMENT__REFRESH_INTERVAL_SECONDS` | Seconds between entitlement re-checks after the one at startup. |
| `entitlement.grace_period_seconds` | integer | `86400` | `JENTIC__ENTITLEMENT__GRACE_PERIOD_SECONDS` | How long (seconds) the last definitive verdict holds while the AWS API is unreachable or erroring, before the gate fails closed. An explicit "not entitled" answer locks out immediately. |
| `entitlement.license_sku` | string \| null | `null` | `JENTIC__ENTITLEMENT__LICENSE_SKU` | Contract pricing only: the Marketplace product ID from the portal (`CheckoutLicense` `ProductSKU`) — not the product code; the portal issues both. Required when `pricing_model` is `contract`. |
| `entitlement.license_dimensions` | list of string | — | `JENTIC__ENTITLEMENT__LICENSE_DIMENSIONS` | Contract pricing only: entitlement dimension keys checked out from the buyer's license; every one must be granted. Accepts a YAML list or a comma-separated string. |
| `entitlement.endpoint` | string \| null | `null` | `JENTIC__ENTITLEMENT__ENDPOINT` |  |

## `held_executions`

Require-approval holds: held execution jobs awaiting a human reviewer. An execute call matching a permission rule with `effect="require-approval"` is enqueued as a `held` job with a `pending` approval; a reviewer (the agent's owner or an `org:admin`) approves or denies it, or it expires.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `held_executions.ttl_seconds` | integer (> 0) | `86400` | `JENTIC__HELD_EXECUTIONS__TTL_SECONDS` | Seconds a pending approval lives before the expiry sweep marks it `expired` and fails its held job with a permission-denied result. Defaults to 24 hours. |
| `held_executions.max_pending_per_agent` | integer (>= 1) | `10` | `JENTIC__HELD_EXECUTIONS__MAX_PENDING_PER_AGENT` | Maximum pending approvals per agent. An execute call that would file another hold beyond this is denied with the `approval_pending_limit_reached` problem type. |
| `held_executions.result_retention_seconds` | integer (> 0) | `86400` | `JENTIC__HELD_EXECUTIONS__RESULT_RETENTION_SECONDS` | Seconds the result of an approved execution stays readable via `GET /jobs/{id}/result` before the result sweep removes it. Defaults to 24 hours. |

## `apps`

Surfaces this process serves: any of `registry`, `admin`, `control`, `auth`, or `broker` alone (the broker cannot be combined with other surfaces). Also decides which secrets production requires.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `apps` | list of string | — | `JENTIC__APPS` | Surfaces this process serves: any of `registry`, `admin`, `control`, `auth`, or `broker` alone (the broker cannot be combined with other surfaces). Also decides which secrets production requires. |

## `state`

The process's shared-state backend (top-level `state:` section). One backend per process holds the rate-limit buckets, circuit-breaker counters, `Idempotency-Key` records, the auth surface's single-use markers, the broker's auth-failure counters and the MCP session-event dedupe of every surface the process serves.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `state.backend` | string | `"memory"` | `JENTIC__STATE__BACKEND` | Name of the shared-state backend to build: `memory` (in-process, the default), `redis` (deprecated), or a name an extension registered with `register_state_backend`. An unregistered name fails at startup with the list of registered names. |
| `state.redis_url` | string (secret) \| null | `null` | `JENTIC__STATE__REDIS_URL` | Connection URL of the deprecated `redis` backend; required by it. |
| `state.redis_key_prefix` | string | `"jentic:broker:"` | `JENTIC__STATE__REDIS_KEY_PREFIX` | Prefix the deprecated `redis` backend puts on every key; set a per-environment value when deployments share one Redis. |
| `state.memory.max_entries_per_namespace` | integer (>= 1) | `100000` | `JENTIC__STATE__MEMORY__MAX_ENTRIES_PER_NAMESPACE` | Default most live entries one key namespace may hold before its writes are refused. |
| `state.memory.max_bytes_per_namespace` | integer (>= 1) | `16777216` | `JENTIC__STATE__MEMORY__MAX_BYTES_PER_NAMESPACE` | Default most approximate bytes (key + value + about 750 bytes of per-entry overhead) one key namespace may hold before its writes are refused. |
| `state.memory.max_total_bytes` | integer (>= 1) | `201326592` | `JENTIC__STATE__MEMORY__MAX_TOTAL_BYTES` | Most approximate bytes all namespaces of the memory backend may hold together (201326592, 192 MiB, by default); a write past it is refused like a full namespace, never by evicting a live entry. A process builds one backend shared by every surface it serves, so this caps the whole process's shared state. Raise the container memory limit together with it. |
| `state.memory.namespaces` | map of MemoryNamespaceBudgetConfig | — | `JENTIC__STATE__MEMORY__NAMESPACES` | Per-namespace budget overrides, keyed by namespace (the key prefix before the first `:`). Unless overridden here, idem (Idempotency-Key records) is 250000 entries / 134217728 bytes (128 MiB) and idem-body (their stored response bodies) 1000000 entries / 33554432 bytes (32 MiB), each with a 0.1 per-caller share; login-csrf (login page CSRF nonces) uses max_entries_per_namespace / max_bytes_per_namespace with a 0.02 per-client share, about 370 live nonces per IPv4 address or IPv6 /64 at the defaults (about 900 bytes each, 5-minute TTL), twice what the default authorize rate limit lets one client network request in that time; repeated_failure (the broker's repeated-failure counters, which gate the execution.repeated_failure database scan) is 10000 entries / 8780000 bytes (about 8.4 MiB), about 2500 concurrently failing actor + credential + operation keys, and fails open when full (every failure runs the scan); every other namespace uses max_entries_per_namespace / max_bytes_per_namespace. The sustained idempotent request rate idem can hold is min(max_entries, max_bytes / record_bytes) / broker.idempotency.ttl_s, where record_bytes is about 1000 bytes plus the response headers (capped at 512 bytes): about 30 req/s at the defaults and a 1 h TTL, and a tenth of that per caller. Above it, Idempotency-Key requests get 503 idempotency_unavailable; bodies (about 830 bytes + body length each) stop being stored for replay sooner, about 5 req/s of 1 KiB bodies, without refusing requests. |
| `state.memory.namespaces.<name>.max_entries` | integer (>= 1) | *required* | `JENTIC__STATE__MEMORY__NAMESPACES__<NAME>__MAX_ENTRIES` | Most live entries the namespace may hold. |
| `state.memory.namespaces.<name>.max_bytes` | integer (>= 1) | *required* | `JENTIC__STATE__MEMORY__NAMESPACES__<NAME>__MAX_BYTES` | Most approximate bytes (key + value + about 750 bytes of per-entry overhead) the namespace may hold. |
| `state.memory.namespaces.<name>.max_owner_share` | number (> 0, <= 1) \| null | `null` | `JENTIC__STATE__MEMORY__NAMESPACES__<NAME>__MAX_OWNER_SHARE` | Most of max_entries and max_bytes that one owner (the key's second `:` segment: for idem and idem-body the calling actor, for login-csrf the client's IPv4 address or IPv6 /64) may hold; an owner over its share is refused without affecting the others. Unset, it is 0.1 for idem and idem-body, 0.02 for login-csrf and unlimited elsewhere; 1.0 lets one owner use the whole namespace. |
| `state.options` | object | — | `JENTIC__STATE__OPTIONS` | Settings for a backend registered by an extension, passed to its builder unvalidated (the builder validates them). Ignored by the built-in memory and redis backends. |
