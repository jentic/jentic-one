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
break-glass tool, not a supported rollback path: restore the snapshot
instead, or roll forward to a fixed release. The version
number's exact promises while in beta: [VERSIONING.md](../../VERSIONING.md).

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
