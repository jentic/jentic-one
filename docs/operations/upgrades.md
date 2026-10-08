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
   `OVERALL current|uninitialized|pending|unknown` verdict and exits `0` for
   `current`, `3` for `uninitialized` or `pending`, and `5` for `unknown`
   (the step ledger could not be read), so scripts can branch on it. A full run (all
   databases, no `--target`) also performs the release's **upgrade steps** —
   data changes that span databases — and prints an
   `==> upgrade step <name>: <action>` line for each. Most steps run once per
   install (`already_done` afterwards); a repeatable step runs on every full
   upgrade. `rule_sets_mark_curated` is repeatable: it marks the shared
   permission rule sets an `org:admin` or a system job created as curated
   (so they stay attachable by every credential writer), including sets an
   older release created after the step last ran (for example while that
   release was redeployed on the newer schema).
   A step that leaves blocking work undone exits `4`: fix the logged cause
   and re-run before starting the new version (`--skip-upgrade-step <name>`
   defers one step deliberately; on Helm, via `migrate.extraArgs`).
   Non-blocking follow-ups print as `==> WARNING` lines — read them (see
   [Cross-owner rule set attachments](#cross-owner-rule-set-attachments)).
   A step that has never run — after `--skip-upgrade-steps`, or a partial
   run such as `--db control` — is reported by a `--check` that covers the
   control and admin databases (the default) as a
   `STATUS upgrade-step:<name> pending` line and `OVERALL pending` (exit
   `3`), once every database is at head; a full run clears it. If the
   schemas are at head but the step ledger cannot be read, `--check` prints
   `OVERALL unknown` and exits `5`. `jenticctl start` refuses to start
   while a step is pending, naming `jenticctl update --stack-only`, and
   carries on when the verdict is `unknown`. The run
   lock these steps take is a Postgres session-level advisory lock, so point
   the migration at the database directly, not through a transaction-mode
   pooler (pgbouncer `pool_mode=transaction`).
4. **Restart both roles** (app and broker) on the new version — don't run
   them split across releases.
5. **Keep the CLIs on the same release** as the server:
   `jenticctl update` updates the binaries and checks the stack.
6. **Review what the upgrade carried over.** The upgrade steps keep
   existing access intact and report, rather than remove, admin-level grants
   copied onto successor agents and cross-owner credential bindings — see
   [Reviewing grants and bindings carried over by the upgrade](../development/releasing.md#reviewing-grants-and-bindings-carried-over-by-the-upgrade)
   for the read-only audit queries.

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

## Cross-owner rule set attachments

Only a shared permission rule set's creator or an `org:admin` may attach a
set that is not curated, because the creator can edit its rules — and so the
policy of every binding attached to it. An attachment made before that rule
existed, or one an `org:admin` made, is left in place. Every full upgrade
lists each binding on a non-curated set created by someone other than the
agent or its owner as `==> WARNING (rule_sets_mark_curated)` lines on
stderr: a summary with the remediation, then one line per binding (the
first 50, then `...and N more`) naming the binding, agent (id and name), the
agent's owner, the credential, the rule set (id and name) and the set's
creator. The full count is `cross_owner_bindings` in the step's JSON
summary. Nothing is detached automatically; resolve each binding with one of:

- the agent's owner attaches a set they created or a curated set, or
  detaches the set so the binding's inline rules apply;
- an `org:admin` attaches a curated set (one an `org:admin` created);
- an `org:admin` marks the set curated, after which only an `org:admin` can
  edit it. There is no API for this; in the control database (`control` is
  your control `schema_name`; drop the prefix on SQLite):
  `UPDATE control.permission_rule_sets SET curated = true WHERE id = '<rule set id>';`
  A raw-SQL change leaves no audit record — note it in your change log.

For an agent with no owner only the `org:admin` options apply. The warning
repeats on each full upgrade until no such binding remains. If the listing
itself fails (for example a query error on the admin database), the step
still marks the sets, prints one `could not list cross-owner bindings`
warning, and reports `cross_owner_bindings: null`; the upgrade does not
fail on it.

## Binding rules written against the full upstream path

The broker evaluates binding permission rules on the request path relative
to the API's server URL — the spec's path, which the rule editor, its
preview and `permissions:test` all show. A `prefix`/`exact` rule written with
the server's base path included (`/eu/widgets` for a server
`http://host/{region}`, `/api/v3/pet` for a server `https://host/api/v3`)
matches nothing on that basis, so the binding denies those calls (`403
action_denied`) until the rule is fixed. Each such denial logs a
`rule_denied_on_relative_path_matched_legacy_base_path` warning naming the
binding (`agent_id`, `credential_id`, `rule_set_id`) and both paths, so you
can find affected bindings in the broker logs. Upgrades don't rewrite rules
automatically; run the rewrite job, preview first:

```sh
jentic_one rewrite-rule-base-paths --diff-only --report rewrite-preview.jsonl
jentic_one rewrite-rule-base-paths --report rewrite.jsonl
```

It rewrites a rule only when stripping one of the bound API's server base
paths gives a path that applies to a real operation, and the rule's current
path applies to none. Each rewrite is audited, and a re-run changes nothing.
Report lines with `"category": "skipped"` need a manual fix in the rule
editor; their `reason` says why:

| `reason` | Meaning |
| -------- | ------- |
| `regex_not_rewritable` | `regex` rules are never rewritten; check whether the pattern includes the base path. |
| `matches_no_operation` | Stripping the base path leaves a path no operation uses. |
| `ambiguous_base` | More than one server base path fits, with different results. |
| `rule_set_mixed_apis` | A shared rule set is attached to bindings on APIs that need different rewrites. |
| `api_not_found` | The bound credential's API has no live revision in the registry. |

A `"category": "conflict"` line means the rule was edited while the job ran;
re-run it.

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
