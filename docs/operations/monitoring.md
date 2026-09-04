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
poll — point your own uptime monitoring at the same two endpoints.

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
endpoints above if your compliance story wants an external copy.

Live event stream (imports, approvals, catalog updates): `jentic events`
from the CLI, or the notifications surface in the UI.

## Metrics and traces

OpenTelemetry wiring (OTLP metrics/traces, the optional Prometheus exporter
and scrape annotations, collector sidecars) is covered in
[deploy/README.md → Metrics](../../deploy/README.md#metrics). Telemetry is
**off by default** and never leaves the deployment unless you configure an
exporter.
