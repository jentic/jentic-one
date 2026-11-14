"""Integration tests for ``migrations.run --direction down`` on disposable databases (#1304).

Exercises multi-DB and single-DB downgrade paths against disposable PostgreSQL
schemas (created per test and dropped with ``CASCADE`` in teardown) and
disposable ``tmp_path`` SQLite databases — never mutating shared harness schemas.
"""

from __future__ import annotations

from typing import Any

import pytest
from alembic.script import ScriptDirectory

from jentic_one.migrations import run as run_mod

pytestmark = pytest.mark.integration

_SURFACES = ("registry", "control", "admin")


def _head_and_ancestors(db_name: str, depth: int = 3) -> list[str]:
    script = ScriptDirectory.from_config(run_mod._build_config(db_name))
    (head,) = script.get_heads()
    chain = [head]
    cur: str | None = head
    for _ in range(depth - 1):
        assert cur is not None
        rev = script.get_revision(cur)
        assert rev is not None and isinstance(rev.down_revision, str)
        cur = rev.down_revision
        chain.append(cur)
    return chain


def test_disposable_stack_refuses_ambiguous_downgrades_without_mutating_any_schema(
    disposable_migration_stack: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ambiguous multi-DB or targetless downgrades refuse before touching any disposable DB."""
    current_rev = disposable_migration_stack
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    heads = {surface: current_rev(surface) for surface in _SURFACES}
    _, admin_parent, _ = _head_and_ancestors("admin")
    capsys.readouterr()

    for argv in (
        ["--direction", "down"],
        ["--direction", "down", "--target", "-1"],
        ["--db", "control", "--direction", "down"],
        ["--db", "control", "--db", "admin", "--direction", "down", "--target", admin_parent],
    ):
        code = run_mod.main(argv)
        captured = capsys.readouterr()
        assert code != 0, f"expected refusal for {argv!r}"
        assert captured.err.strip() != ""
        for surface in _SURFACES:
            assert current_rev(surface) == heads[surface], (
                f"{surface} was mutated by refused argv {argv!r}"
            )


def test_disposable_stack_partial_downgrade_exits_nonzero_and_reports_explicit_state(
    disposable_migration_stack: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Multi-DB ``--target base`` hitting ``admin``'s irreversible drop exits
    non-zero with explicit ``DOWNGRADE_STATUS`` lines."""
    current_rev = disposable_migration_stack
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    admin_head = current_rev("admin")
    registry_head = current_rev("registry")
    capsys.readouterr()

    code = run_mod.main(
        ["--db", "admin", "--db", "registry", "--direction", "down", "--target", "base"]
    )
    combined = "".join(capsys.readouterr())

    assert code == run_mod.EXIT_PARTIAL_DOWNGRADE
    assert current_rev("registry") is None
    assert current_rev("admin") == run_mod.SA_DROP_REVISION
    assert f"DOWNGRADE_STATUS registry rolled_back before={registry_head} after=-" in combined
    assert (
        f"DOWNGRADE_STATUS admin partial before={admin_head} after={run_mod.SA_DROP_REVISION}"
        in combined
    )


def test_disposable_stack_explicit_single_db_multi_revision_downgrade_and_reupgrade(
    disposable_migration_stack: Any,
) -> None:
    """Explicit multi-revision single-DB downgrade lands on target and re-upgrades cleanly."""
    current_rev = disposable_migration_stack
    assert run_mod.main(["--db", "control", "--skip-upgrade-steps"]) == 0
    head, _parent, grandparent = _head_and_ancestors("control", depth=3)
    assert current_rev("control") == head

    assert run_mod.main(["--db", "control", "--direction", "down", "--target", grandparent]) == 0
    assert current_rev("control") == grandparent

    assert run_mod.main(["--db", "control", "--skip-upgrade-steps"]) == 0
    assert current_rev("control") == head
