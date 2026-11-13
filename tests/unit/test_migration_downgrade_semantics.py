"""Unit tests for multi-database and multi-revision downgrade semantics (#1304).

Pins on disposable SQLite stacks that ``jentic_one.migrations.run --direction down``:
1. Refuses ambiguous requests up front (omitted ``--target``, multi-DB relative
   steps like ``-1``, multi-DB single-tree revision IDs, or uninitialized DBs)
   before mutating any database.
2. Exits non-zero with explicit per-database ``DOWNGRADE_STATUS`` lines when a
   multi-revision or multi-DB downgrade stops partway through (for example at an
   irreversible drop revision).
3. Completes cleanly for unambiguous single-DB downgrades (explicit revision or
   explicit relative ``-1``) and full ``--target base`` teardowns on reversible trees.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory

from jentic_one.migrations import run as run_mod


def _head_and_ancestors(db_name: str, depth: int = 3) -> list[str]:
    """Return ``[head, parent, grandparent, ...]`` for ``db_name``."""
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


def _current_rev(stack: Path, db_name: str) -> str | None:
    db_file = stack / f"{db_name}.db"
    if not db_file.exists():
        return None
    with sqlite3.connect(db_file) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "alembic_version" not in tables:
            return None
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return str(row[0]) if row is not None else None


@pytest.mark.parametrize(
    "argv",
    [
        ["--direction", "down"],
        ["--db", "control", "--direction", "down"],
        ["--direction", "down", "--target", "-1"],
        ["--db", "control", "--db", "admin", "--direction", "down", "--target", "-1"],
    ],
    ids=[
        "all-dbs-no-target",
        "single-db-no-target",
        "all-dbs-relative-step",
        "two-dbs-relative-step",
    ],
)
def test_downgrade_refuses_ambiguous_requests_before_mutating_any_db(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    """Ambiguous downgrade invocations fail up front without rolling back any database."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    heads_before = {name: _current_rev(sqlite_stack, name) for name in run_mod._valid_dbs()}
    capsys.readouterr()

    code = run_mod.main(argv)
    captured = capsys.readouterr()

    assert code != 0
    assert captured.err.strip() != ""
    for name in run_mod._valid_dbs():
        assert _current_rev(sqlite_stack, name) == heads_before[name], (
            f"{name} was mutated by refused argv {argv!r}"
        )


def test_downgrade_refuses_single_tree_revision_across_multiple_dbs_without_mutating_first_db(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Passing ``--db control --db admin`` with an admin-only revision must not mutate ``admin``.

    Rollback reverses ``order`` to ``['admin', 'control']``, so without up-front
    validation ``admin`` rolls back to ``admin_parent`` before ``control`` fails
    with unknown revision.
    """
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    admin_head, admin_parent, _ = _head_and_ancestors("admin")
    control_head, _, _ = _head_and_ancestors("control")
    capsys.readouterr()

    code = run_mod.main(
        ["--db", "control", "--db", "admin", "--direction", "down", "--target", admin_parent]
    )
    err = capsys.readouterr().err

    assert code != 0
    assert admin_parent in err or "control" in err
    assert _current_rev(sqlite_stack, "admin") == admin_head
    assert _current_rev(sqlite_stack, "control") == control_head


def test_downgrade_refuses_unknown_revision_on_single_db_cleanly(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unknown target revision on a single DB exits non-zero without mutating it."""
    run_mod.upgrade("control")
    control_head, _, _ = _head_and_ancestors("control")
    capsys.readouterr()

    code = run_mod.main(["--db", "control", "--direction", "down", "--target", "deadbeef0000"])
    err = capsys.readouterr().err

    assert code != 0
    assert "deadbeef0000" in err
    assert _current_rev(sqlite_stack, "control") == control_head


def test_downgrade_refuses_uninitialized_db_instead_of_exiting_zero(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Running ``--direction down --target -1`` on an uninitialized DB fails instead of no-oping."""
    code = run_mod.main(["--db", "control", "--direction", "down", "--target", "-1"])
    err = capsys.readouterr().err

    assert code != 0
    assert "uninitialized" in err.lower()
    assert _current_rev(sqlite_stack, "control") is None
    if (sqlite_stack / "control.db").exists():
        with sqlite3.connect(sqlite_stack / "control.db") as conn:
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert tables == []


def test_partial_multi_db_downgrade_exits_nonzero_and_reports_per_db_status(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When one DB rolls back to ``base`` and a later DB hits an irreversible barrier,
    the runner exits non-zero and prints explicit ``DOWNGRADE_STATUS`` lines."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    admin_head, _, _ = _head_and_ancestors("admin")
    registry_head, _, _ = _head_and_ancestors("registry")
    capsys.readouterr()

    # Reversed order runs ``registry`` (succeeds -> base), then ``admin``
    # (rolls back down to SA_DROP_REVISION and stops on its irreversible guard).
    code = run_mod.main(
        ["--db", "admin", "--db", "registry", "--direction", "down", "--target", "base"]
    )
    captured = capsys.readouterr()
    combined = captured.out + captured.err

    assert code == run_mod.EXIT_PARTIAL_DOWNGRADE
    assert _current_rev(sqlite_stack, "registry") is None
    assert _current_rev(sqlite_stack, "admin") == run_mod.SA_DROP_REVISION
    assert f"DOWNGRADE_STATUS registry rolled_back before={registry_head} after=-" in combined
    assert (
        f"DOWNGRADE_STATUS admin partial before={admin_head} after={run_mod.SA_DROP_REVISION}"
        in combined
    )


def test_multi_db_downgrade_stopping_on_first_db_reports_remaining_dbs_untouched(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Full ``--direction down --target base`` hits ``admin`` first and leaves
    ``control``/``registry`` untouched."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    admin_head, _, _ = _head_and_ancestors("admin")
    control_head, _, _ = _head_and_ancestors("control")
    registry_head, _, _ = _head_and_ancestors("registry")
    capsys.readouterr()

    code = run_mod.main(["--direction", "down", "--target", "base"])
    captured = capsys.readouterr()
    combined = captured.out + captured.err

    assert code == run_mod.EXIT_PARTIAL_DOWNGRADE
    assert _current_rev(sqlite_stack, "admin") == run_mod.SA_DROP_REVISION
    assert _current_rev(sqlite_stack, "control") == control_head
    assert _current_rev(sqlite_stack, "registry") == registry_head
    assert (
        f"DOWNGRADE_STATUS admin partial before={admin_head} after={run_mod.SA_DROP_REVISION}"
        in combined
    )
    assert (
        f"DOWNGRADE_STATUS control untouched before={control_head} after={control_head}" in combined
    )
    assert (
        f"DOWNGRADE_STATUS registry untouched before={registry_head} after={registry_head}"
        in combined
    )


def test_single_db_explicit_multi_revision_and_relative_downgrade_succeeds(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Explicit single-DB downgrade to a multi-step ancestor and ``-1`` succeeds
    and reports transitions."""
    run_mod.upgrade("control")
    head, parent, grandparent = _head_and_ancestors("control", depth=3)
    capsys.readouterr()

    assert run_mod.main(["--db", "control", "--direction", "down", "--target", grandparent]) == 0
    out = capsys.readouterr().out
    assert _current_rev(sqlite_stack, "control") == grandparent
    assert f"{head} -> {grandparent}" in out

    # Stepping back one more revision with explicit -1 on a single DB also succeeds.
    script = ScriptDirectory.from_config(run_mod._build_config("control"))
    great_grandparent = script.get_revision(grandparent).down_revision
    assert isinstance(great_grandparent, str)

    assert run_mod.main(["--db", "control", "--direction", "down", "--target", "-1"]) == 0
    assert _current_rev(sqlite_stack, "control") == great_grandparent
    assert parent != great_grandparent


def test_successful_downgrade_with_unavailable_after_status_reports_unknown(
    sqlite_stack: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A committed downgrade with unreadable revision state requires recovery."""
    run_mod.upgrade("control")
    head, parent, _ = _head_and_ancestors("control")
    capsys.readouterr()
    real_status = run_mod.status
    control_status_calls = 0

    def _status(db_name: str) -> tuple[str, list[str], list[str]]:
        nonlocal control_status_calls
        if db_name == "control":
            control_status_calls += 1
            if control_status_calls > 1:
                raise RuntimeError("revision status unavailable")
        return real_status(db_name)

    monkeypatch.setattr(run_mod, "status", _status)
    code = run_mod.main(["--db", "control", "--direction", "down", "--target", parent])
    captured = capsys.readouterr()
    combined = captured.out + captured.err

    assert code == run_mod.EXIT_PARTIAL_DOWNGRADE
    assert _current_rev(sqlite_stack, "control") == parent
    assert f"DOWNGRADE_STATUS control unknown before={head} after=unknown" in combined
    assert "OVERALL partial_downgrade" in combined
    assert "revision status unavailable" in combined


def test_noop_downgrade_does_not_count_as_mutation_when_later_db_fails(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Confirmed no-op databases do not turn an unchanged failure into a partial downgrade."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    assert (
        run_mod.main(
            [
                "--db",
                "admin",
                "--direction",
                "down",
                "--target",
                run_mod.SA_DROP_REVISION,
            ]
        )
        == 0
    )
    assert run_mod.main(["--db", "registry", "--direction", "down", "--target", "base"]) == 0
    capsys.readouterr()

    code = run_mod.main(
        ["--db", "admin", "--db", "registry", "--direction", "down", "--target", "base"]
    )
    captured = capsys.readouterr()
    combined = captured.out + captured.err

    assert code == 1
    assert _current_rev(sqlite_stack, "registry") is None
    assert _current_rev(sqlite_stack, "admin") == run_mod.SA_DROP_REVISION
    assert "DOWNGRADE_STATUS registry unchanged before=- after=-" in combined
    assert (
        f"DOWNGRADE_STATUS admin failed before={run_mod.SA_DROP_REVISION} "
        f"after={run_mod.SA_DROP_REVISION}" in combined
    )
    assert "OVERALL failed" in combined
