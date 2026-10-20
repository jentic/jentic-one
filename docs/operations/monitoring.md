# Monitoring & logs

What a running deployment exposes, and where the records live.

## Health

Every surface answers an unauthenticated `GET /health`; the two you normally
probe are the two published ports:

```bash
curl -fsS http://127.0.0.1:8000/health         # app     → {"status":"ok","version":"…"}
curl -fsS http://127.0.0.1:8100/health         # broker  → {"status":"ok","surface":"broker",…}
curl -fsS http://127.0.0.1:8000/admin/health   # adds setup_required (first-run state)
```

These are what the Docker healthchecks, Helm probes, and `jenticctl status`
poll. Know what they measure: **process liveness only**. `/health` is
deliberately dependency-free, and `/admin/health` swallows database errors
and still reports ok — all three stay green with the database down. Real
monitoring needs two more probes:

- **Broker readiness: `GET /ready`** (port 8100, unauthenticated). Returns
  `503` while the broker is draining or when sustained in-flight load nears
  the admission cap (default: 0.9 of the cap), so a load balancer sheds the
  instance before the broker starts shedding requests. Note the Helm chart's
  broker readinessProbe currently defaults to `/health` — point it at
  `/ready` to get saturation-aware draining.
- **A database-sensitive check.** No unauthenticated endpoint fails on a
  database outage, so an uptime monitor pointed only at `/health` cannot
  detect one. Use an authenticated read that does, e.g.
  `GET /executions?limit=1` with a token holding `executions:read`.

## Logs

Both roles log structured JSON to stdout — collect them the way you collect
any container/service logs (`docker logs`, `journalctl`, your cluster's log
pipeline). A file sink can be enabled alongside stdout with the
[`logging.*` config keys](../reference/config.md#logging)
(`file_enabled`, `file_dir`, rotation size/count); CLI-managed installs
(`jenticctl install`) enable it under `~/.jentic/logs/`.

Log hygiene is enforced, not aspirational: secrets are carried in masked
types, so a stored secret can't leak into a log line or a repr (an
architectural test gates this).

## What agents did: executions and the audit trail

Two append-only records, both in the **admin database** (which is why
[backups](backup-restore.md) must include it):

- **Execution records** — one per brokered call: agent, operation, verdict,
  latency, and the credential used (by id — never the secret). In the UI
  under **Monitor**; over the API via `GET /executions` — note this returns
  records for the **whole instance** to any holder of `executions:read` (a
  default agent scope); it is not scoped to the caller — and
  `GET /monitoring/executions` + `GET /monitoring/usage`
  (operator aggregates); from the CLI via `jentic history`.
- **Audit entries** — one per admin/control-plane mutation: actor (type, id,
  session), action, target, timestamp, and trace id. Append-only by
  construction (the table carries no update column). Over the API via
  `GET /audit` (requires `audit:read`), which filters on `target_type`,
  `target_id`, `actor_id`, `origin`, `since`, and `until` (cursor-paginated
  via `cursor`/`limit`); in the UI under **Monitor → Audit**. Credential
  *use* is not an audit row — it surfaces as a `credential.accessed` event
  and in Executions.

For durability, ship them like any other database rows: they are ordinary
tables in the admin database, so your normal
[backup](backup-restore.md) covers them — or export periodically via the
endpoints above if your compliance story wants an external copy. These
tables (`execution_records`, `audit_entries`, `events`) are append-only and
grow unbounded — there is no pruning or retention mechanism. Size your
database and disk for growth; export-then-trim is the operator's job today.

Live event stream (imports, approvals, catalog updates): `jentic events`
from the CLI, or the notifications surface in the UI.

### Event severity

Every event carries one of four severities. The boundary between them is a
qualitative one, not just an ordering:

| Severity   | Meaning                                                              |
| ---------- | --------------------------------------------------------------------- |
| `info`     | Routine: a lifecycle step happened as intended (import completed, credential connected, agent registration approved). |
| `warning`  | Needs attention soon; nothing has failed *yet* — an advisory, a denial, a credential approaching expiry (`credential.expiring_soon`, `broker.pbac_denied`, an unserved binding). |
| `error`    | One thing failed (an execution, an import, a permanently dead-lettered job, an expired credential). |
| `critical` | A failure *pattern* crossed an operator-configured threshold — not one failure but many of the same kind in a short window. |

`critical` is reserved to a single event type today,
`execution.repeated_failure`: it emits `error` once an actor+credential+
operation's failures cross `security.execution_repeated_failure_threshold`
within `security.execution_repeated_failure_window_s`, and escalates to
`critical` only past the higher
`security.execution_repeated_failure_critical_threshold` (defaults: 5 failures
in 300s for `error`, 20 for `critical` — see [config
reference](../reference/config.md)). That means `critical` is rare **by
design** — filtering the Events tab or `GET /events?severity=critical` and
seeing few or no rows is expected, not a sign the classification is broken.
Tune the thresholds if your deployment's failure volume should escalate to
`critical` sooner.

The full type-by-severity mapping is enforced in code:
`EVENT_TYPE_SEVERITIES` in `shared/models/events.py` is the single source of
truth, checked by `tests/unit/shared/test_event_type_severities.py` against
every event type the platform defines.

## Metrics and traces

OpenTelemetry wiring (OTLP metrics/traces, the optional Prometheus exporter
and scrape annotations, collector sidecars) is covered in the
[chart docs](../../deploy/helm/README.md#metrics-exporter). The Prometheus
exporter itself is not Helm-specific: setting
`observability.metrics.exporter: prometheus` mounts `/metrics` on every
surface on any install shape — scrape it as **`/metrics/`** (with the
trailing slash; the bare path answers with a 307 redirect some scrapers
won't follow). Anonymous product telemetry is gated by `telemetry.enabled`:
off when the block is omitted, on in the shipped install examples — set it to
`false` to opt out. The OTel exporters default
to `otlp` targeting a local collector; without one, nothing is delivered
anywhere — set `observability.metrics.exporter: none` to silence the export
attempts.

With `observability.tracing.exporter: none` no span is recorded at all, so no
span is built, attributed or snapshotted on the request path. Trace ids still
flow: an inbound `traceparent` is honoured (a fresh trace id is minted when
there is none), outbound upstream requests carry `traceparent` and the
`jentic=` `tracestate` member, and execution records, events and log lines
keep their `trace_id`. Because this process records nothing, the outbound
`traceparent` always has the sampled flag unset (`-00`), even when the caller
sent `-01`; an upstream that follows its parent's flag will not sample those
calls.

With `otlp`, `observability.tracing.sample_ratio` (default `1.0`) caps the
fraction of traces recorded and exported. The cap also applies to a request
that arrives with a sampled `traceparent` (the CLI and MCP send one on every
call, and any caller can), so no caller can force full recording. The decision
is deterministic on the trace id, so services sharing a ratio keep the same
traces whole. A request whose `traceparent` is unsampled stays unsampled. A
sampled request that the ratio drops is forwarded upstream with the sampled
flag unset (`-00`), as with `exporter: none`. The
standard `OTEL_TRACES_SAMPLER` / `OTEL_TRACES_SAMPLER_ARG` variables replace
the ratio when set; a `tracing_sampler_env_overrides_sample_ratio` warning is
logged at startup if `sample_ratio` is not `1.0`. The SDK treats an
unrecognised `OTEL_TRACES_SAMPLER` value as `parentbased_always_on`, so a typo
means every trace is recorded.

Each request yields one inbound server span (no per-message ASGI
`http send`/`http receive` spans), one CLIENT span per outbound upstream call
(the broker's dispatch to the target API), and one span per SQL statement,
recorded by the SQLAlchemy instrumentation with the statement and
connection-pool context, plus a `connect` span per connection checkout.
Transaction control (COMMIT/ROLLBACK) and pool pre-ping round trips are not
traced; pre-ping time sits inside the `connect` span. To count those round
trips, use `make perf-dbcost` (per-call statements and transactions per DB
plane).

Exported spans never carry request material. Inbound and outbound HTTP
spans mask query-string values in the recorded URL and query attributes
(`http.url`, `url.full`, `http.target`, `url.query`), keeping parameter names,
path and route. Captured headers are limited to a safe-listed set of
structural headers, even when header capture is switched on through the
`OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_*` variables. When a span records
a failure, the export keeps the exception type (`exception.type`,
`error.type`), the status code and the traceback's frame locations that point
at real source files. The exception message, status description text and
source lines are replaced before export. To see the full message, use the structured logs, which
correlate with traces by `trace_id`.

A few instruments are worth alerting on by name:
`broker.streaming_execution.persist_failures` (an execution completed but
its record was silently dropped — the only signal that happened),
`broker.admission.in_flight` (the number the `/ready` shedding threshold is
0.9 of), `broker.circuit.open_total`, `broker.retry.exhausted_total`,
`broker.deadline.exceeded_total`, `broker.rate_limited_total`, and
`audit.events`.

### Broker latency histograms

Two histograms describe where a sync execution's time goes. Both are in
milliseconds and labelled by `operation` (the operation id); the overhead one
also carries `mode`. The Prometheus exporter appends the unit, so the series
are `broker_execution_duration_ms_milliseconds_{bucket,sum,count}` and
`broker_execution_overhead_ms_milliseconds_{bucket,sum,count}`.

| Histogram | Measures |
| --------- | -------- |
| `broker.execution.duration_ms` | The upstream round trip as timed by the HTTP runner. **Buffered:** the final attempt only, request send to response body read. **Streaming:** stream open (including any bulkhead wait) to body end, so a slow-reading client shows up here. Recorded for sync and async executions. |
| `broker.execution.overhead_ms` | **Wall-clock** broker time on a sync execution outside the upstream window, from the broker's middleware receiving the request (inside admission control). **Buffered:** everything except `jentic-upstream`, up to the point the execution record has committed, the response is assembled and any `Idempotency-Key` completed. **Streaming:** everything before the upstream response headers, plus the record's persistence after the body has been sent. |

Overhead is wall-clock, not CPU: database and shared-state round trips,
connection-pool waits, and the client's request-body upload all count.

How the time of one request splits:

- **Buffered:** request time = `overhead_ms` + `jentic-upstream`. The
  upstream window is `duration_ms` plus the resilience time around it:
  bulkhead waits, earlier retry attempts, backoff sleeps and the
  circuit-breaker check. That resilience time is in **neither** histogram, so
  `duration_ms + overhead_ms` falls short of the request time by exactly that
  much. Watch `broker.retry.attempts_total` for it, or read `jentic-upstream`
  from `Server-Timing` per request.
- **Streaming:** request time ≈ `overhead_ms` + `duration_ms`. Streaming
  calls are never retried, and the bulkhead wait is inside `duration_ms`.
  Only the response teardown between the body's end and the persistence
  (sub-millisecond) is in neither.

Always filter or group by `mode`: buffered overhead is time the client waits
for, while streaming persistence runs after the client already has the body.
To see which stage dominates, use the per-request `Server-Timing` stages.

`overhead_ms` is recorded only for an execution that got the upstream response
headers and whose record was persisted. A failure before the upstream response
headers (connect refused, a header timeout, an open circuit, a stream that
fails to open), a failed record write, and paths that never call the upstream
(async `202`, approval holds, idempotent replays, broker-raised errors) record
nothing. A streaming call whose body fails after the headers (a mid-body
reset, the transfer deadline, a client disconnect) records nothing either: its
record is written only after a body that completes, so it has no persisted
record ([#1712](https://github.com/jentic/jentic-one/issues/1712)). Time spent
outside the broker's middleware stack (the admission gate, the ASGI server) is
not included; `http.server.duration` covers the whole request.

### Per-request stage timing (`Server-Timing`)

To see where one broker request spends its time without a profiler, set
`broker.server_timing: true` (`JENTIC__BROKER__SERVER_TIMING=true`). Execute
responses to authenticated callers then carry a W3C
[`Server-Timing`](https://www.w3.org/TR/server-timing/) header — durations in
milliseconds, fixed stage names only (never URLs, ids or secrets):

```text
Server-Timing: jentic-auth;dur=1.4, jentic-egress;dur=0.3, jentic-discover;dur=0.9, jentic-rules;dur=2.1, jentic-body;dur=0.1, jentic-cred;dur=3.0, jentic-upstream;dur=84.2, jentic-persist;dur=4.7, jentic-total;dur=97.6
```

Only the stages a request ran are listed. A stage that fails is still
reported, so a slow failure shows where the time went.

| Stage | Covers |
| ----- | ------ |
| `jentic-auth` | Token validation, the execute-permission check and the rate limit |
| `jentic-egress` | The egress allowlist check of the upstream URL, DNS resolution included — on entry, and again after credential injection when server variables rewrite the host |
| `jentic-discover` | Operation discovery, including `Jentic-Revision` pin resolution and any unregistered-URL handler |
| `jentic-rules` | Credential-binding derivation and permission-rule evaluation |
| `jentic-body` | Reading (and size-capping) the request body |
| `jentic-idempotency` | The `Idempotency-Key` claim and completion round-trips to shared state |
| `jentic-cred` | Credential resolution and injection |
| `jentic-upstream` | The upstream call (buffered: the whole exchange, retries and backoff included; streaming: until the upstream response headers arrive) |
| `jentic-persist` | The request's admin-database write: the execution record and lifecycle events (including the `FAILED` record written when the upstream call fails), the queued job of an async call, or the hold of an approval-gated call |
| `jentic-total` | From the broker's middleware stack admitting the request (inside admission control) to the response headers |

`jentic-total` minus the other stages is routing, response assembly and the
middleware itself. Time spent outside the broker's middleware stack (the
admission gate, the ASGI server) is not included.

`jentic-upstream` wraps the whole pipeline call, so on the buffered path it
is at least the `broker.execution.duration_ms` the runner records for the
same call: that metric is the final attempt's own round trip, while the stage
also covers earlier retry attempts, backoff and the circuit-breaker check.

On the streaming fast path the headers are sent before the body, and the
execution record is written after it, so `jentic-persist` is absent there.

The same per-request recorder feeds `broker.execution.overhead_ms` (see
[Broker latency histograms](#broker-latency-histograms)): with the header off
it still runs whenever metrics are exported, and emits nothing to the caller.

**Namespacing and upstream timing.** Every broker name carries the `jentic-`
prefix, so the broker's entries can't be confused with an upstream's own
`total` or `db` metrics. If the upstream sent its own `Server-Timing`, it is
passed through and the broker's entries follow as a second header field. An
upstream entry that claims the `jentic-` prefix is dropped while the broker
emits its own field, so every `jentic-*` metric a client sees comes from the
broker. An idempotent replay doesn't call the upstream, so the stored
response is kept without its upstream `Server-Timing`. The replay's broker
field has no `jentic-upstream`.

**Who sees what.** Requests rejected before the caller is fully admitted carry
no broker timing at all: a `401`, a `403` for a missing execute permission, a
rate-limit `429`, a load-shed `503`. An error the broker raises after
admission but before calling the upstream lists the stages that ran **without
durations**, for example `jentic-auth, jentic-egress, jentic-discover,
jentic-total` on a `404 operation_not_found`. This covers a `404`, a rule-deny
`403`, a `409`, a `424` and a replayed upstream error. Those responses are
where an authenticated caller could compare server-side branches (an unknown
revision pin versus another caller's draft), and a server-measured duration
strips the network noise that otherwise hides the difference. Once the
upstream was called, the durations are reported in full: success, an upstream
error status, or a transport failure such as `504`. `jentic-upstream` includes
the circuit-breaker check, so a circuit-open `503` also carries durations even
though no upstream request was sent.

The flag is **off by default** and should stay off on deployments exposed to
callers you don't trust with it. On successful calls, per-stage latencies
still help fingerprint the deployment and can tell a cache hit from a
database read (for example in `jentic-auth` or `jentic-rules`).
