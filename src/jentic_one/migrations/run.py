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
    args = parser.parse_args(argv)

    order = args.db or list(_valid_dbs())
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
