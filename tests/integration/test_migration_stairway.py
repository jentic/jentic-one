"""Per-revision migration stairway integration tests across all database trees.

Walks every revision from ``head`` down to ``base`` (or the first declared
irreversible revision) on disposable databases for both PostgreSQL and SQLite.
At each revision ``R``, snapshots the live schema, downgrades one step
(``-1``), re-upgrades one step (``+1``), verifies that the schema snapshot is
identical to ``R`` before the round-trip, and then downgrades ``-1`` to advance
to the parent revision.
"""

from __future__ import annotations

from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory

from jentic_one.migrations import run as run_mod
from tests.harness.schema_snapshot import IRREVERSIBLE, _schema_snapshot

pytestmark = pytest.mark.integration

_SURFACES: tuple[str, ...] = ("registry", "control", "admin")

_IRREVERSIBLE_CASES: list[tuple[str, str]] = sorted(
    (surface, rev_id) for surface, revs in IRREVERSIBLE.items() for rev_id in revs
)


def _diff_snapshots(
    expected: dict[str, Any],
    observed: dict[str, Any],
) -> str:
    """Format a concise diff between two schema snapshots."""
    lines: list[str] = []
    all_tables = sorted(set(expected) | set(observed))
    for table in all_tables:
        if table not in expected:
            lines.append(f"  + unexpected table {table!r}: {observed[table]}")
            continue
        if table not in observed:
            lines.append(f"  - missing table {table!r}")
            continue
        exp_table = expected[table]
        obs_table = observed[table]
        for section in ("columns", "indexes", "unique_constraints", "foreign_keys"):
            if exp_table.get(section) != obs_table.get(section):
                lines.append(
                    f"  ! {table}.{section} mismatch:\n"
                    f"      expected: {exp_table.get(section)}\n"
                    f"      observed: {obs_table.get(section)}"
                )
    return "\n".join(lines)


@pytest.mark.parametrize("surface", _SURFACES)
def test_stairway_each_revision_round_trips(
    disposable_migration_stack: Any,
    surface: str,
) -> None:
    """Every reversible revision downgrades ``-1``, re-upgrades ``+1`` to an identical schema."""
    current_rev = disposable_migration_stack
    cfg = run_mod._build_config(surface)
    script = ScriptDirectory.from_config(cfg)

    command.upgrade(cfg, "head")
    irreversible = IRREVERSIBLE.get(surface, set())

    for script_rev in script.walk_revisions():
        rev_id = script_rev.revision
        assert current_rev(surface) == rev_id, (
            f"expected {surface} at revision {rev_id!r}, got {current_rev(surface)!r}"
        )
        snap_r = _schema_snapshot(surface)

        if rev_id in irreversible:
            with pytest.raises(RuntimeError, match=rev_id):
                command.downgrade(cfg, "-1")
            assert current_rev(surface) == rev_id
            assert _schema_snapshot(surface) == snap_r
            break

        command.downgrade(cfg, "-1")
        command.upgrade(cfg, "+1")
        assert current_rev(surface) == rev_id
        snap_after = _schema_snapshot(surface)
        assert snap_after == snap_r, (
            f"Schema mismatch on {surface} after downgrade(-1)/upgrade(+1) at {rev_id} "
            f"({script_rev.doc}):\n{_diff_snapshots(snap_r, snap_after)}"
        )
        command.downgrade(cfg, "-1")
    else:
        assert current_rev(surface) is None
        assert _schema_snapshot(surface) == {}


@pytest.mark.parametrize(
    ("surface", "revision"),
    _IRREVERSIBLE_CASES,
    ids=[f"{surface}-{rev_id}" for surface, rev_id in _IRREVERSIBLE_CASES],
)
def test_irreversible_revisions_refuse_downgrade(
    disposable_migration_stack: Any,
    surface: str,
    revision: str,
) -> None:
    """Declared irreversible revisions raise on downgrade without mutating the schema."""
    current_rev = disposable_migration_stack
    cfg = run_mod._build_config(surface)

    command.upgrade(cfg, revision)
    assert current_rev(surface) == revision
    snap_before = _schema_snapshot(surface)

    with pytest.raises(RuntimeError, match=revision):
        command.downgrade(cfg, "-1")

    assert current_rev(surface) == revision
    assert _schema_snapshot(surface) == snap_before
