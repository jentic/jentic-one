# Operating Jentic One

Day-2: what to watch, how to upgrade, what a backup must contain. Each page
here owns the install-method-agnostic contract; the exact commands for your
install shape live in the [installation guides](../installation/README.md),
and the compose-shaped runbook an agent can execute is
[`agent/operate.md`](../agent/operate.md).

| I want to… | Page |
| ---------- | ---- |
| Check it's healthy, watch logs, see what agents did | [Monitoring & logs](monitoring.md) |
| Move to a new release | [Upgrades](upgrades.md) |
| Survive a disk failure or a bad migration | [Backup & restore](backup-restore.md) |
| Fix something that's broken | [Troubleshooting](troubleshooting.md) — the common symptoms, each linking to its fix |

Quick health check:

```bash
jenticctl status                                # install, server, identity health
curl -fsS http://127.0.0.1:8000/health          # app answers (liveness only)
```

The `127.0.0.1:8000`/`8100` ports fit local Docker-shaped installs; Helm and
remote installs need a port-forward or their own host. What the health
endpoints do and don't tell you (they stay green with the database down),
plus the broker's `/ready` probe: [Monitoring & logs](monitoring.md#health).
