"""Programmatic Alembic migration runner.

Runs ``alembic upgrade`` for one or more databases without relying on the
repo-root ``alembic.ini`` or a particular working directory. This is the
entry point used by the deployment migration Job (``python -m
jentic_one.migrations.run``) so the same packaged code that ships in the
service image also applies schema migrations.

The runner builds an Alembic :class:`~alembic.config.Config` in memory,
pointing ``script_location`` at the packaged ``migrations`` directory and
``version_locations`` at the per-database ``versions`` folder. Database URLs
and target schemas are resolved by the existing ``env.py`` from application
config (``JENTIC__DATABASES__*`` env vars), so there is a single source of
truth for connection details.

A full upgrade to head (every database, no ``--target``) then runs the
one-shot **upgrade steps** — data steps that span databases and so cannot live
in one Alembic tree (``control/services/upgrade_steps.py``). Running them here
means every install path that migrates performs them before the new version
serves traffic. ``--skip-upgrade-steps`` defers all of them (and
``--skip-upgrade-step NAME`` one of them) to the next full upgrade. A step
that leaves blocking work undone exits ``4`` (``EXIT_UPGRADE_STEP_FAILED``);
non-blocking follow-ups are printed as ``==> WARNING`` lines.

**Service-account retirement (theme-8 Phase 4).** On a full upgrade whose
admin database has not yet applied the service-account drop
(``e2f3a4b5c6d7``), the runner first brings admin to the revision before it,
then migrates every remaining service account to a successor agent, verifies
the copy, and sweeps the service-account leftovers
(``ServiceAccountMigrationService.retire``) — and only then lets admin reach
head. The step lives here rather than inside the Alembic revision because it
writes the control database too (the ``sva_``-keyed inline permission rules),
which an admin migration cannot reliably reach in a split deployment, and
because migrations must not import application code. It is not an upgrade
step: those run after head, i.e. after the drop. A refused verification exits
``4`` with admin still before the drop, and nothing swept; a re-run is safe.
A partial or targeted upgrade skips it — the drop revision then refuses on
any service account the retirement has not finished. A retirement that
retired any service account prints ``==> WARNING`` lines on stdout listing
each service account → successor agent id: ``sak_`` keys stop working in 0.41
and their callers must switch to a ``jak_`` key of the successor agent. It
also prints one ``==> WARNING (service-account retirement, not copied)`` line
per grant, binding, or inline rule list it deliberately did not copy to a
successor (copying could have resurrected access removed from it), so the
operator can re-grant what is still needed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Collection
from dataclasses import asdict
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from jentic_one.control.services.service_account_migration import (
    SAK_KEYS_RETIRED_WARNING,
    RetirementOutcome,
    ServiceAccountMigrationService,
    ServiceAccountRetirementError,
)
from jentic_one.control.services.upgrade_steps import (
    RETIRED_STEP_NAMES,
    UpgradeStepService,
    step_names,
)
from jentic_one.migrations.targets import DB_TARGETS
from jentic_one.shared.config import load_config
from jentic_one.shared.context import Context

_MIGRATIONS_DIR = Path(__file__).resolve().parent

#: Databases the post-migration upgrade steps read and write. The steps run
#: only when a full upgrade brought every one of them to head.
_UPGRADE_STEP_DBS = frozenset({"admin", "control"})

#: Exit code when the schema migrated but an upgrade step left work undone the
#: operator must resolve. Non-zero so a Helm pre-upgrade hook (or any wrapper)
#: stops before the new version serves traffic.
EXIT_UPGRADE_STEP_FAILED = 4

#: The admin revision that drops the service-account tables, and the one
#: before it. The retirement runs between the two (see the module docstring).
SA_DROP_REVISION = "e2f3a4b5c6d7"  # pragma: allowlist secret
SA_DROP_PARENT_REVISION = "d1e2f3a4b5c6"  # pragma: allowlist secret


def _valid_dbs() -> tuple[str, ...]:
    """Live target names (dynamic so targets registered post-import count)."""
    return tuple(DB_TARGETS.keys())


def _build_config(db_name: str) -> Config:
    """Construct an in-memory Alembic config for a single database section."""
    cfg = Config()
    cfg.config_ini_section = db_name
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("version_locations", str(_MIGRATIONS_DIR / db_name / "versions"))
    cfg.set_main_option("path_separator", "os")
    return cfg


def upgrade(db_name: str, target: str = "head") -> None:
    """Apply migrations for a single database up to ``target``."""
    if db_name not in DB_TARGETS:
        raise ValueError(f"Unknown database {db_name!r}; expected one of {_valid_dbs()}")
    command.upgrade(_build_config(db_name), target)


def downgrade(db_name: str, target: str) -> None:
    """Roll a single database back to ``target`` (e.g. ``"-1"`` or a revision/base)."""
    if db_name not in DB_TARGETS:
        raise ValueError(f"Unknown database {db_name!r}; expected one of {_valid_dbs()}")
    command.downgrade(_build_config(db_name), target)


# Schema states reported by ``status`` / ``--check``, in ascending severity.
STATE_CURRENT = "current"
STATE_PENDING = "pending"
STATE_UNINITIALIZED = "uninitialized"

# Exit code for ``--check`` when at least one database is not at head. Distinct
# from 1 so a caller can tell "the schema needs work" apart from "the check
# itself failed" (bad config, database unreachable) and not act on a non-answer.
CHECK_EXIT_NEEDS_MIGRATION = 3


def status(db_name: str) -> tuple[str, list[str], list[str]]:
    """Report a database's schema state without modifying it.

    Returns ``(state, current_revisions, head_revisions)`` where state is one of
    :data:`STATE_CURRENT`, :data:`STATE_PENDING`, :data:`STATE_UNINITIALIZED`.

    ``uninitialized`` (no Alembic version table at all) is deliberately distinct
    from ``pending`` (stamped, but behind head). They call for opposite
    responses: an uninitialized database holds no data, so creating the schema is
    safe and unattended; a pending one holds data that forward-only migrations
    will rewrite, which is a decision for the operator with a backup in hand.

    Implemented via ``alembic current``, which runs ``env.py`` (so URL/schema
    resolution stays in one place) under ``dont_mutate=True`` and with a no-op
    migration function. That is what makes the probe read-only: no migration can
    be applied, and no version table is created on an untouched database.
    """
    if db_name not in DB_TARGETS:
        raise ValueError(f"Unknown database {db_name!r}; expected one of {_valid_dbs()}")
    cfg = _build_config(db_name)
    probe: dict[str, list[str]] = {}
    cfg.attributes["status_probe"] = probe
    command.current(cfg)

    current = probe.get("current", [])
    heads = sorted(ScriptDirectory.from_config(cfg).get_heads())
    if not current:
        return STATE_UNINITIALIZED, current, heads
    if set(current) == set(heads):
        return STATE_CURRENT, current, heads
    return STATE_PENDING, current, heads


def sa_retirement_pending() -> bool:
    """Whether the admin DB still has to pass the service-account drop.

    False for an uninitialized database (a fresh install has no service
    accounts; the drop revision's own gate passes on empty tables) and for one
    that already applied :data:`SA_DROP_REVISION`.
    """
    cfg = _build_config("admin")
    probe: dict[str, list[str]] = {}
    cfg.attributes["status_probe"] = probe
    command.current(cfg)
    current = probe.get("current", [])
    if not current:
        return False
    script = ScriptDirectory.from_config(cfg)
    applied = {
        rev.revision for rev in script.iterate_revisions(tuple(current), "base") if rev is not None
    }
    return SA_DROP_REVISION not in applied


async def _retire_service_accounts_async() -> int:
    config = load_config()
    async with Context(
        config, allowed_dbs=set(_UPGRADE_STEP_DBS), refresh_providers_on_boot=False
    ) as ctx:
        try:
            outcome = await ServiceAccountMigrationService(ctx).retire()
        except ServiceAccountRetirementError as exc:
            print(f"==> {exc}", file=sys.stderr, flush=True)
            return EXIT_UPGRADE_STEP_FAILED
    print(f"==> service-account retirement: {outcome.action}", flush=True)
    print(json.dumps(asdict(outcome)), flush=True)
    _print_sak_warning(outcome)
    _print_not_copied_warnings(outcome)
    return 0


def _print_sak_warning(outcome: RetirementOutcome) -> None:
    """Tell the operator which agents replaced which service accounts (ids only)."""
    if not outcome.successors:
        return
    print(f"==> WARNING (service-account retirement): {SAK_KEYS_RETIRED_WARNING}", flush=True)
    for sa_id, agent_id in outcome.successors.items():
        target = agent_id or "no successor agent (the account was not active or disabled)"
        print(f"==> WARNING   {sa_id} -> {target}", flush=True)


def _print_not_copied_warnings(outcome: RetirementOutcome) -> None:
    """One line per grant/binding/rule list withheld so removed access stays removed."""
    for line in outcome.warnings:
        print(f"==> WARNING (service-account retirement, not copied): {line}", flush=True)


def retire_service_accounts() -> int:
    """Migrate, verify and sweep the remaining service accounts (pre-drop).

    Returns ``0`` on success, :data:`EXIT_UPGRADE_STEP_FAILED` when the
    verification refused or the step could not run — the admin schema then
    stays before the drop revision and nothing was swept.
    """
    try:
        return asyncio.run(_retire_service_accounts_async())
    except Exception as exc:
        print(
            f"==> the service-account retirement could not run ({type(exc).__name__}: "
            f"{exc}); nothing was dropped (admin stays at {SA_DROP_PARENT_REVISION}). "
            "Fix the cause and re-run the migration.",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_UPGRADE_STEP_FAILED


def _run_check(order: list[str]) -> int:
    """Print each database's schema state and return the process exit code.

    The output is line-oriented and stable because `jenticctl` parses it to
    decide whether starting the stack is safe.
    """
    # The overall verdict is the state demanding the most caution, which is
    # ``pending`` — NOT the "worst-looking" one. The caller responds to these
    # states in opposite ways: ``uninitialized`` is migrated unattended (nothing
    # to lose), while ``pending`` aborts so the operator can take a backup.
    #
    # So a mixed stack — say a newly added database target with no version table
    # alongside an existing one behind head — must report ``pending``. Ranking
    # ``uninitialized`` higher would let the "no data to lose" path run
    # forward-only migrations across every database, including the ones holding
    # data, silently bypassing the very safeguard this check exists to provide.
    caution = {STATE_CURRENT: 0, STATE_UNINITIALIZED: 1, STATE_PENDING: 2}
    verdict = STATE_CURRENT
    for db_name in order:
        state, current, heads = status(db_name)
        print(
            f"STATUS {db_name} {state} current={','.join(current) or '-'} "
            f"head={','.join(heads) or '-'}",
            flush=True,
        )
        if caution[state] > caution[verdict]:
            verdict = state
    print(f"OVERALL {verdict}", flush=True)
    return 0 if verdict == STATE_CURRENT else CHECK_EXIT_NEEDS_MIGRATION


async def _run_upgrade_steps_async(skip: Collection[str]) -> int:
    config = load_config()
    # The upgrade steps never resolve a credential provider, and the migrate
    # Job is not handed the credential keyset, so skip the boot-time provider
    # refresh rather than have it fail to decrypt stored client secrets.
    async with Context(
        config, allowed_dbs=set(_UPGRADE_STEP_DBS), refresh_providers_on_boot=False
    ) as ctx:
        outcomes = await UpgradeStepService(ctx).run(skip=skip)
    failed = False
    for outcome in outcomes:
        print(f"==> upgrade step {outcome.name}: {outcome.action}", flush=True)
        print(json.dumps(asdict(outcome)), flush=True)
        for warning in outcome.warnings:
            print(f"==> WARNING ({outcome.name}): {warning}", file=sys.stderr, flush=True)
        failed = failed or outcome.failed
    if failed:
        print(
            "==> an upgrade step left work undone (see the log lines above); "
            "resolve it and re-run the migration before starting the new version.",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_UPGRADE_STEP_FAILED
    return 0


def run_upgrade_steps(skip: Collection[str] = ()) -> int:
    """Run the one-shot post-migration data steps (see ``UpgradeStepService``).

    Any unexpected error (config, connectivity, a bug) is reported as an
    upgrade-step failure — the schema is already at head, so the exit code must
    say "steps undone", not "migration failed". With no step registered this
    is a no-op that never touches config or the databases.
    """
    if not step_names():
        return 0
    try:
        return asyncio.run(_run_upgrade_steps_async(skip))
    except Exception as exc:
        print(
            f"==> upgrade steps could not run ({type(exc).__name__}: {exc}); the schema "
            "is at head. Fix the cause and re-run the migration before starting the "
            "new version.",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_UPGRADE_STEP_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply Alembic migrations.")
    parser.add_argument(
        "--db",
        action="append",
        choices=_valid_dbs(),
        help="Database to migrate (repeatable). Defaults to all, in dependency order.",
    )
    parser.add_argument(
        "--direction",
        choices=("up", "down"),
        default="up",
        help="Migration direction (default: up).",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="Target revision. Default: 'head' (up) / '-1' (down). "
        "The down default of '-1' is applied per --db, so a bare "
        "'--db a --db b down' steps each database back one revision.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report each database's schema state and exit without changing "
        f"anything. Exits {CHECK_EXIT_NEEDS_MIGRATION} if any database is not at head.",
    )
    parser.add_argument(
        "--skip-upgrade-steps",
        action="store_true",
        help="After a full upgrade to head, do not run any of the one-shot "
        "post-migration data steps. They then run on the next full upgrade instead.",
    )
    parser.add_argument(
        "--skip-upgrade-step",
        action="append",
        default=[],
        choices=(*step_names(), *RETIRED_STEP_NAMES),
        metavar="NAME",
        help="Skip one post-migration data step (repeatable; "
        f"one of: {', '.join(step_names()) or 'none registered'}). "
        "It then runs on the next full upgrade. Names of deleted steps are "
        "accepted and ignored.",
    )
    args = parser.parse_args(argv)

    order = args.db or list(_valid_dbs())
    if args.check:
        return _run_check(order)
    if args.direction == "down":
        # Rollback reverses registration order so a dependent schema tears down
        # before the schema it FKs into. Critical for FK safety.
        order = list(reversed(order))
        target = args.target or "-1"
        for db_name in order:
            print(f"==> Rolling back {db_name} to {target}", flush=True)
            downgrade(db_name, target)
            print(f"==> {db_name} rolled back", flush=True)
    else:
        target = args.target or "head"
        # The steps need every database they touch at head; a partial or
        # explicitly targeted upgrade leaves them for the next full one.
        full_upgrade = args.target is None and _UPGRADE_STEP_DBS.issubset(order)
        # The retirement writes control too, so control must be at head first
        # (the default order runs it before admin; a hand-written --db order
        # may not — the drop revision's gate then refuses unfinished rows).
        retire_before_admin = full_upgrade and order.index("control") < order.index("admin")
        for db_name in order:
            if db_name == "admin" and retire_before_admin and sa_retirement_pending():
                print(f"==> Migrating admin to {SA_DROP_PARENT_REVISION}", flush=True)
                upgrade("admin", SA_DROP_PARENT_REVISION)
                print("==> Retiring service accounts (migrate, verify, sweep)", flush=True)
                code = retire_service_accounts()
                if code:
                    return code
            print(f"==> Migrating {db_name} to {target}", flush=True)
            upgrade(db_name, target)
            print(f"==> {db_name} complete", flush=True)
        if full_upgrade and not args.skip_upgrade_steps:
            return run_upgrade_steps(skip=args.skip_upgrade_step)
    return 0


if __name__ == "__main__":
    sys.exit(main())
