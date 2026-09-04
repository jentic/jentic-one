# Upgrades

The contract is the same on every install shape; only the commands differ.

## The contract

1. **Snapshot first.** Migrations are forward-only; the
   [backup](backup-restore.md) *is* the rollback.
2. **Pin the new version** wherever your install pins it (image tag, env
   file, Helm values) — don't run `latest` in anything you care about.
3. **Run migrations before the new code serves traffic.**
   `python -m jentic_one.migrations.run` applies them;
   appending `--check` inspects without modifying (prints an
   `OVERALL current|uninitialized|pending` verdict — non-zero exit on
   `pending` is by design, so scripts can branch on it).
4. **Restart both roles** (app and broker) on the new version — don't run
   them split across releases.
5. **Keep the CLIs on the same release** as the server:
   `jenticctl update` updates the binaries and checks the stack.

Rolling *back* is not a supported path — restore the pre-upgrade snapshot and
re-run the old version, or roll forward to a fixed release. The version
number's exact promises while in beta: [VERSIONING.md](../../VERSIONING.md).

## Where the commands live, per install

| Install | Upgrade steps |
| ------- | ------------- |
| Docker (`docker run`) | [docker.md → Upgrading](../installation/docker.md#upgrading) |
| systemd + Podman | [systemd.md → Upgrading](../installation/systemd.md#upgrading) |
| Helm | [helm.md → Upgrading](../installation/helm.md#upgrading) |
| CLI-managed (`jenticctl install`) | `jenticctl update`, or the step-by-step [agent runbook](../agent/operate.md#upgrade) |

## What an upgrade never does

- It never rotates the credential-encryption keyset or other generated
  secrets — rotating would orphan everything already encrypted. Key rotation
  is its own explicit procedure (add a `v2` entry to
  `credentials.encryption.entries`, flip `active_id`, keep `v1` until
  re-encryption completes).
- It never migrates *down*. `--check` before, snapshot always.
