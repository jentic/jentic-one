# Backup & restore

A restorable backup is **data + key, together**. Get one of them and not the
other and you have nothing.

## What a backup must contain

| Piece | Where it lives | Why |
| ----- | -------------- | --- |
| **The three databases** (registry, control, admin) | SQLite files on the data volume, or schemas in your Postgres instance | The catalog, credentials (encrypted), agents, toolkits, audit trail, execution history |
| **The credential-encryption keyset** | Your config — `credentials.encryption` in the config file, or the equivalent env var / Kubernetes Secret | Stored credentials are AES-encrypted at rest; **without the keyset the restored data is unreadable, permanently** |
| The rest of the config (`jentic-one.yaml` / env file / Helm values) | Wherever you configured the install | Not secret-critical, but a restore is much faster when you don't have to reconstruct it |

What is deliberately **not** exportable: credential plaintext. There is no
"export secrets" path — that's the product's core guarantee — so a backup of
the encrypted rows plus the keyset is the *only* way stored credentials
survive a machine loss.

## SQLite (single-volume installs)

The quickstart/Docker trial shape keeps all three databases as files on one
volume. Snapshot it with the containers **stopped** (SQLite files mid-write
are not consistent):

```bash
docker stop jentic-app jentic-broker
docker run --rm -v jentic-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/jentic-data-$(date +%F).tgz -C /data .
docker start jentic-app jentic-broker
```

Restore = the reverse into a fresh volume, then start the same image version
the backup was taken with:

```bash
docker volume create jentic-data
docker run --rm -v jentic-data:/data -v "$PWD":/backup alpine \
  tar xzf /backup/jentic-data-YYYY-MM-DD.tgz -C /data
```

CLI-managed installs (`jenticctl install`): the volume is
`jentic_jentic-data` and the config to keep is `~/.jentic/jentic-one.yaml` +
`~/.jentic/.env` — the same invariant, spelled out in the runbook's
[uninstall notes](../agent/operate.md#uninstall).

## PostgreSQL (production installs)

The three surfaces are schemas in your Postgres instance — back them up like
any Postgres database, live:

```bash
pg_dump -h db.prod.internal -U postgres -d jentic \
  -n registry -n control -n admin -Fc -f jentic-$(date +%F).dump
```

Restore with `pg_restore` into a prepared instance (roles/schemas created
first — the SQL is in the [Docker guide, step 3](../installation/docker.md#3-prepare-the-database)),
then run migrations before starting the services if the target release is
newer than the dump. Managed Postgres (RDS, Cloud SQL) snapshot schedules
count as the database half — you still need the keyset half.

## When to take one

- **Before every upgrade** — migrations are forward-only; the snapshot is
  the rollback ([Upgrades](upgrades.md)).
- On a schedule sized to how painful re-entering credentials and re-approving
  agents would be.

## Restore drill (verify it, once)

A backup nobody has restored is a hypothesis. On a scratch machine: restore
the data, point a fresh install of the **same version** at it with the backed-up
config, and check `curl -fsS http://127.0.0.1:8000/health`, sign-in, and one
brokered call against a stored credential. That last step is the one that
proves the keyset half of the backup.
