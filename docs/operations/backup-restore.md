# Backup & restore

A restorable backup is **data + key, together**. The databases restore
without the keyset — catalog, agents, toolkits, audit trail, and execution
history all come back — but every stored credential secret is then
permanently unreadable, and re-entering credentials is usually the painful
part of a rebuild.

## What a backup must contain

| Piece | Where it lives | Why |
| ----- | -------------- | --- |
| **The three databases** (registry, control, admin) | SQLite files on the data volume, or schemas in your Postgres instance | The catalog, credentials (encrypted), agents, toolkits, audit trail, execution history |
| **The credential-encryption keyset** | Your config — `credentials.encryption` in the config file, or the equivalent env var / Kubernetes Secret | Stored credential **secret material** is AES-encrypted at rest; without the keyset those secrets — and only those — are permanently unreadable. Everything else restores fine, but the credentials all have to be re-entered |
| The rest of the config (`jentic-one.yaml` / env file / Helm values) | Wherever you configured the install | Not secret-critical, but a restore is much faster when you don't have to reconstruct it |

What is deliberately **not** exportable: credential plaintext. There is no
"export secrets" path (that's the product's core guarantee), so a backup of
the encrypted rows plus the keyset is the *only* way stored credentials
survive a machine loss.

## SQLite (single-volume installs)

The quickstart/Docker trial shape keeps all three databases as files on one
volume. Snapshot it with the containers **stopped** (SQLite files mid-write
are not consistent):

```bash
docker stop jentic-app jentic-broker
docker run --rm -v jentic-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/jentic-data-$(date -u +%Y%m%dT%H%M%SZ).tgz -C /data .
docker start jentic-app jentic-broker
```

Restore = the reverse into a fresh volume, then start the same image version
the backup was taken with:

```bash
docker volume create jentic-data
docker run --rm -v jentic-data:/data -v "$PWD":/backup alpine \
  tar xzf /backup/jentic-data-YYYYMMDDTHHMMSSZ.tgz -C /data
```

CLI-managed installs (`jenticctl install`): the volume is
`jentic_jentic-data` and the config to keep is `~/.jentic/jentic-one.yaml` +
`~/.jentic/.env` — the same invariant, spelled out in the runbook's
[uninstall notes](../agent/operate.md#uninstall).

## PostgreSQL installs

The three databases are schemas in your Postgres instance — back them up like
any Postgres database, live:

```bash
pg_dump -h db.prod.internal -U postgres -d jentic \
  -n registry -n control -n admin -Fc -f jentic-$(date -u +%Y%m%dT%H%M%SZ).dump
```

Restoring is where the recipe matters. `pg_restore`'s default behaviour on
error is to **continue**: against schemas that already hold objects — the
rollback case, where the instance was just migrated — every `CREATE` errors
and the data `COPY`s in anyway, leaving duplicated rows and an
`alembic_version` of unknown vintage. A restore must be onto empty schemas,
with the services stopped:

```bash
# 1. Stop everything that writes (docker stop jentic-app jentic-broker,
#    scale the deployments to 0, or systemctl stop jentic-app jentic-broker).

# 2. Empty the three schemas. Drop only — do NOT recreate them: the dump
#    carries its own CREATE SCHEMA and GRANT statements (the roles survive
#    the drop), and a pre-created schema makes step 3 abort on
#    'schema already exists'.
psql -h db.prod.internal -U postgres -d jentic \
  -c 'DROP SCHEMA registry CASCADE' \
  -c 'DROP SCHEMA control  CASCADE' \
  -c 'DROP SCHEMA admin    CASCADE'

# 3. Restore, failing loudly on the first error instead of continuing:
pg_restore -h db.prod.internal -U postgres -d jentic \
  --exit-on-error jentic-YYYYMMDDTHHMMSSZ.dump
```

(If the restore reports missing roles, the target instance was never
provisioned — create the roles first: Docker guide step 3, or the Helm
chart's `pg-init` ConfigMap for bundled-Postgres installs.)

Then run migrations before starting the services if the target release is
newer than the dump, and restart. Note what this is and is not: restoring a
pre-upgrade dump rewinds the *data* to the moment the dump was taken —
writes since then are gone. It is the supported way back from a bad
migration, not a time machine. Managed Postgres (RDS, Cloud SQL) snapshot
schedules count as the database half — you still need the keyset half.

## When to take one

- **Before every upgrade** — migrations are forward-only; the snapshot is
  the rollback ([Upgrades](upgrades.md)).
- On a schedule sized to how painful re-entering credentials and re-approving
  agents would be.

Two sizing/placement notes. Backups grow with use: execution records, audit
entries, and events are append-only and
[never pruned](monitoring.md#what-agents-did-executions-and-the-audit-trail),
so size storage and transfer windows for growth. And escrow the keyset with
the same care as the database dumps, in a separate system from the running
host — one lost or compromised machine must not take both halves.

## Restore drill (verify it, once)

A backup nobody has restored is a hypothesis. On a scratch machine: restore
the data, point a fresh install of the **same version** at it with the
backed-up config, then verify in three steps of increasing strength:

1. `curl -fsS http://127.0.0.1:8000/health` — proves only that the process
   boots; [the endpoint is dependency-free](monitoring.md#health) and passes
   even against an empty or corrupt database.
2. Sign in — proves the restored databases are actually being read.
3. One brokered call against a stored credential — the only step that proves
   the keyset half. This is also where a keyset-less restore shows itself:
   everything up to here looks healthy, then the brokered call fails with a
   decryption error.
