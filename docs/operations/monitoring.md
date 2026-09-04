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
  under **Monitor**; over the API via `GET /executions` (agents see their
  own) and `GET /monitoring/executions` + `GET /monitoring/usage`
  (operator aggregates); from the CLI via `jentic history`.
- **Audit entries** — one per admin/control-plane mutation: actor (type, id,
  session), action, target, timestamp, and trace id. Append-only by
  construction (the table carries no update column). Over the API via
  `GET /audit` (requires `audit:read`).

For durability, ship them like any other database rows: they are ordinary
tables in the admin database, so your normal
[backup](backup-restore.md) covers them — or export periodically via the
endpoints above if your compliance story wants an external copy. These
tables (`execution_records`, `audit_entries`, `events`) are append-only and
grow unbounded — there is no pruning or retention mechanism. Size your
database and disk for growth; export-then-trim is the operator's job today.

Live event stream (imports, approvals, catalog updates): `jentic events`
from the CLI, or the notifications surface in the UI.

## Metrics and traces

OpenTelemetry wiring (OTLP metrics/traces, the optional Prometheus exporter
and scrape annotations, collector sidecars) is covered in the
[chart docs](../../deploy/helm/README.md#metrics-exporter). The Prometheus
exporter itself is not Helm-specific: setting
`observability.metrics.exporter: prometheus` mounts `/metrics` on every
surface on any install shape. Anonymous product telemetry is
**off by default** (`telemetry.enabled: false`). The OTel exporters default
to `otlp` targeting a local collector; without one, nothing is delivered
anywhere — set `observability.metrics.exporter: none` to silence the export
attempts.
