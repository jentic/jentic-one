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
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from jentic_one.migrations.targets import DB_TARGETS

_MIGRATIONS_DIR = Path(__file__).resolve().parent


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
        for db_name in order:
            print(f"==> Migrating {db_name} to {target}", flush=True)
            upgrade(db_name, target)
            print(f"==> {db_name} complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
