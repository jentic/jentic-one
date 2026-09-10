# Upgrades

The contract is the same on every install shape; only the commands differ.

## The contract

1. **Snapshot first — the [backup](backup-restore.md) *is* the rollback.**
   Migrations are forward-only; nothing below is safe to attempt without a
   pre-upgrade snapshot.
2. **Pin the new version** wherever your install pins it (image tag, env
   file, Helm values) — don't run `latest` in anything you care about.
3. **Run migrations before the new code serves traffic.**
   `python -m jentic_one.migrations.run` applies them — run it inside the
   image, not on the host (the host needs no Python): e.g.
   `docker run --rm --env-file … <image> python -m jentic_one.migrations.run`,
   or the compose file's `migrate` service. Appending `--check` inspects
   without modifying: it prints an
   `OVERALL current|uninitialized|pending` verdict and exits non-zero unless
   `OVERALL current`, so scripts can branch on it.
4. **Restart both roles** (app and broker) on the new version — don't run
   them split across releases.
5. **Keep the CLIs on the same release** as the server:
   `jenticctl update` updates the binaries and checks the stack.

Rolling *back* the app version is supported only together with restoring the
matching pre-upgrade snapshot — old code on a newer schema is not a supported
state. A schema downgrade path exists
(`python -m jentic_one.migrations.run --direction down`), but it is a
break-glass tool, not a supported rollback path — know its sharp edges
before touching it: the default steps back **one revision per database**
(so a release spanning two revisions leaves a database half-downgraded and
still exits 0), and several `down` bodies are deliberate no-ops that do not
restore dropped data. Restore the snapshot
instead, or roll forward to a fixed release. The version
number's exact promises while in beta: [VERSIONING.md](../../VERSIONING.md).

## If a migration run fails mid-flight

The run is **not atomic**. It walks the three databases sequentially (a
failure on one leaves the earlier ones already at head) and applies each
revision in its own transaction (a failure at revision N leaves 1…N−1
committed). Two revisions additionally commit mid-revision to build indexes
`CONCURRENTLY` — a run killed during one of those can leave an `INVALID`
index and an unstamped revision. This is also why the run's *time budget*
matters: Helm's default `--timeout` (5 m) `SIGTERM`s an overrunning hook,
so the Helm guides set an explicit `--timeout 30m` — keep it. The systemd
migrate unit is `Type=oneshot`, which has no start timeout by default; the
unit pins `TimeoutStartSec=infinity` so nothing reintroduces one.

Diagnose before touching anything:
`python -m jentic_one.migrations.run --check` prints one
`STATUS <db> <state> current=<rev> head=<rev>` line **per database** ahead
of the `OVERALL` verdict — that per-database line is the post-failure entry
point, telling you which database stopped where. Recovery is the
[snapshot restore](backup-restore.md#postgresql-installs) into
emptied schemas, then re-run the migration with the cause fixed. Do not
re-run blind: a run that died inside a `CONCURRENTLY` revision can fail its
retry with `column already exists`, which is the signature of exactly this
state — restore, don't patch.

## Where the commands live, per install

| Install | Upgrade steps |
| ------- | ------------- |
| Docker (`docker run`) | [docker.md → Upgrading](../installation/docker.md#upgrading) |
| systemd | [systemd.md → Upgrading](../installation/systemd.md#upgrading) |
| Helm | [helm.md → Upgrading](../installation/helm.md#upgrading) |
| CLI-managed (`jenticctl install`) | `jenticctl update`, or the step-by-step [agent runbook](../agent/operate.md#upgrade) |

## What an upgrade never does

- It never rotates the credential-encryption keyset or other generated
  secrets — rotating would orphan everything already encrypted. Key rotation
  is its own explicit procedure, and it is narrower than it sounds: add a
  new entry to `credentials.encryption.entries` and flip `active_id`. New
  writes use the new key immediately, but a stored secret re-encrypts only
  when its row is next rewritten — there is no bulk re-encrypt command and
  no way to check completion. Retired keys therefore stay in the keyset
  indefinitely; removing an entry makes any secret still encrypted under it
  permanently unreadable.
- It never migrates *down*. `--check` before, snapshot always.
