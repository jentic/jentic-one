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
`broker.streaming_execution.persist_failures` (a streaming execution's record
was not written because of a database failure, a timeout or a shutdown
cut-off — the only signal that happened),
`app.background_tasks` with `outcome` `timed_out`, `cancelled` or `rejected`
(work handed off after a response — the streaming execution record, and the
record of a buffered call that ended in an unmapped error —
that hit its budget or was cut off by a shutdown drain), and `outcome`
`failed` for `kind=unmapped_failure_persist` (that record's database errors
are counted only there),
`broker.admission.in_flight` (the number the `/ready` shedding threshold is
0.9 of), `broker.circuit.open_total`, `broker.retry.exhausted_total`,
`broker.deadline.exceeded_total`, `broker.rate_limited_total`, and
`audit.events`.
