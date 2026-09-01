"""Every migration tree must have exactly one alembic head.

Two PRs that each add a migration off the same parent revision merge cleanly
as text — git sees two new files — but leave the tree with two heads, and
every ``upgrade head`` then dies with ``MultipleHeads`` at runtime. That is
exactly how PR #1151 (``aa6b7c8d9e0f``) and PR #1178 (``f2a3b4c5d6e7``)
broke main on 2026-08-31 (hotfixed by #1211): the breakage surfaced only in
the slow fixture-booting CI jobs, *after* both merges. This test is the
in-PR gate: it parses the version scripts (no database, no env.py
execution), so the second PR of any such pair fails unit tests before it
can land. Fix by reparenting one migration's ``down_revision`` onto the
other's revision id (pick an order; if the migrations are order-sensitive,
that order).

Targets are discovered from the migrations package, not hardcoded, so a new
tree (or a downstream ``register_target`` package mirroring the layout) is
covered the day its ``versions/`` directory appears.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from tests.arch.conftest import SRC_ROOT

MIGRATIONS_ROOT = SRC_ROOT / "migrations"


def _migration_trees() -> list[Path]:
    return sorted(
        child for child in MIGRATIONS_ROOT.iterdir() if (child / "versions").is_dir()
    )


def test_migration_trees_discovered() -> None:
    """The discovery itself must keep finding the built-in targets."""
    names = {tree.name for tree in _migration_trees()}
    assert {"admin", "control", "registry"} <= names


@pytest.mark.parametrize("tree", _migration_trees(), ids=lambda tree: tree.name)
def test_single_alembic_head(tree: Path) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(tree))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, (
        f"migration tree '{tree.name}' has {len(heads)} heads: {sorted(heads)}. "
        "Two branches added migrations off the same parent revision; reparent "
        "one file's down_revision onto the other's revision id so the tree is "
        "linear again (see the #1211 incident)."
    )
