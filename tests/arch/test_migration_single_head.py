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

import ast
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from tests.arch.conftest import SRC_ROOT
from tests.harness.schema_snapshot import IRREVERSIBLE

MIGRATIONS_ROOT = SRC_ROOT / "migrations"


pytestmark = pytest.mark.arch


def _migration_trees() -> list[Path]:
    return sorted(child for child in MIGRATIONS_ROOT.iterdir() if (child / "versions").is_dir())


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


def _extract_revision_id(module_tree: ast.Module, path: Path) -> str:
    for node in module_tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "revision"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    return node.value.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "revision"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise AssertionError(f"Could not find module-level `revision` string in {path}")


def _downgrade_raises(module_tree: ast.Module, path: Path) -> bool:
    for node in module_tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
            return any(isinstance(child, ast.Raise) for child in ast.walk(node))
    raise AssertionError(f"Could not find top-level `def downgrade()` in {path}")


def test_irreversible_set_is_complete() -> None:
    """Every revision whose ``downgrade()`` raises is in ``IRREVERSIBLE``, and nothing else is."""
    observed: dict[str, set[str]] = {}
    for tree in _migration_trees():
        raising_revs: set[str] = set()
        for script_path in sorted((tree / "versions").glob("*.py")):
            if script_path.name == "__init__.py":
                continue
            parsed = ast.parse(script_path.read_text(encoding="utf-8"), filename=str(script_path))
            rev_id = _extract_revision_id(parsed, script_path)
            if _downgrade_raises(parsed, script_path):
                raising_revs.add(rev_id)
        if raising_revs:
            observed[tree.name] = raising_revs

    expected = {surface: set(revs) for surface, revs in IRREVERSIBLE.items() if revs}
    assert observed == expected, (
        "Mismatch between migrations whose downgrade() raises and IRREVERSIBLE:\n"
        f"  observed in migrations/*/versions/*.py: {observed}\n"
        f"  declared in IRREVERSIBLE:               {expected}"
    )
