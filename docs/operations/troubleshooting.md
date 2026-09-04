# Troubleshooting

Something's broken and you're a human, not a runbook-executing agent — this
page gets you to the right fix. Two rules before touching anything:

1. **Check health first.** `curl -fsS http://127.0.0.1:8000/health` and
   `:8100/health` tell you whether the processes are even up — and
   [only that](monitoring.md#health); they stay green with the database down.
2. **Take a [backup](backup-restore.md) before any repair attempt.** A fix
   that goes wrong without one turns an incident into a loss.

The fixes live in the [agent troubleshooting runbook](../agent/troubleshoot.md)
— written for an AI agent to execute, but the failures and the fixes are the
same for you. Jump straight to your symptom:

| Symptom | Fix |
| ------- | --- |
| App or broker container never reports healthy | [App or broker never becomes healthy](../agent/troubleshoot.md#app-or-broker-never-becomes-healthy) |
| `address already in use` on port 8000 or 8100 | [`address already in use` at start](../agent/troubleshoot.md#address-already-in-use-on-port-8000-or-8100-at-start) |
| Database connection lost or refused at runtime | [Database connection lost/refused (Postgres shape)](../agent/troubleshoot.md#database-connection-lostrefused-at-runtime-postgres-shape) |
| Migrations fail during install or upgrade | [Migrations fail](../agent/troubleshoot.md#migrations-fail) |
| `setup_required` flips between true and false | [`setup_required` flaps](../agent/troubleshoot.md#setup_required-flaps) |
| SQLite "disk I/O error" in the logs | [SQLite "disk I/O error"](../agent/troubleshoot.md#sqlite-disk-io-error) |
| `docker pull` denied | [Image pull denied](../agent/troubleshoot.md#image-pull-denied) |
| Postgres won't start: "superuser password is not specified" | [`${VER}`/`${PGPASS}` resolve empty](../agent/troubleshoot.md#verpgpass-resolve-empty-postgres-exits-with-superuser-password-is-not-specified) |
| Postgres `registry`/`control`/`admin` schemas missing | [Postgres schemas missing](../agent/troubleshoot.md#postgres-schemas-missing-or-someone-suggests-an-init-script) |
| CLI download fails (no URL, or 404) | [CLI download fails](../agent/troubleshoot.md#cli-download-fails-no-url-resolved-or-404) |
| `invalid_grant` when registering an agent | [`invalid_grant` on register or token exchange](../agent/troubleshoot.md#invalid_grant-on-register-or-token-exchange) |
| `jentic register` exits 3 (`TIMEOUT_PENDING`) | [`jentic register` times out](../agent/troubleshoot.md#jentic-register-times-out-exit-3-timeout_pending) |
| `jentic execute` fail-closes against a remote install | [Fail-close against a remote install](../agent/troubleshoot.md#jentic-execute-fail-closes-against-a-remote-install) |
| Nobody around to click through first-run setup | [No human at the first-admin gate](../agent/troubleshoot.md#no-human-available-at-the-first-admin-gate-ci-fleet-installs) |

If your symptom isn't listed, gather `docker compose … logs` (or your install
shape's equivalent) before asking for help — the structured log lines carry
trace ids that make the report actionable.
