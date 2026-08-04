"""Tests for the read-only migration status check (``migrations.run --check``).

The check is what lets ``jenticctl start`` refuse to bring the stack up on a
database that has no schema, or one that is behind the code (#951). Two
properties matter and are pinned here against real SQLite databases:

* it **distinguishes** uninitialized (no data — safe to create) from pending
  (has data — the operator's call), because those warrant opposite responses;
* it is genuinely **read-only** — a status probe that migrated as a side effect
  would be worse than no probe at all.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jentic_one.migrations import run as run_mod
from jentic_one.migrations.run import (
    CHECK_EXIT_NEEDS_MIGRATION,
    STATE_CURRENT,
    STATE_PENDING,
    STATE_UNINITIALIZED,
    status,
)

# One database is enough to pin the semantics and keeps the suite fast; the
# per-database loop is exercised by the CLI-facing --check tests below.
_DB = "admin"


@pytest.fixture
def sqlite_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point config at fresh, empty per-database SQLite files."""
    cfg = tmp_path / "jentic-one.yaml"
    lines = ["databases:"]
    for name in ("admin", "control", "registry"):
        lines += [f"  {name}:", "    backend: sqlite", f"    path: {tmp_path / f'{name}.db'}"]
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("JENTIC_CONFIG_FILE", str(cfg))
    return tmp_path


def _tables(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {name for (name,) in rows}


def test_status_reports_uninitialized_for_a_wiped_database(sqlite_stack: Path) -> None:
    """A wiped/new volume has no version table — the #951 scenario.

    This must NOT be reported as ``pending``: there is no data at risk, so the
    caller is free to create the schema unattended.
    """
    state, current, heads = status(_DB)
    assert state == STATE_UNINITIALIZED
    assert current == []
    assert heads, "the packaged migrations must have a head revision"


def test_status_probe_does_not_create_a_schema(sqlite_stack: Path) -> None:
    """The probe must not migrate as a side effect.

    Asserting on the state alone would not catch a probe that answered
    correctly *and* mutated the database, so check the tables directly.
    """
    status(_DB)
    assert _tables(sqlite_stack / f"{_DB}.db") == set()


def test_status_reports_current_after_migrating(sqlite_stack: Path) -> None:
    run_mod.upgrade(_DB)
    state, current, heads = status(_DB)
    assert state == STATE_CURRENT
    assert current == heads


def test_status_reports_pending_when_behind_head(sqlite_stack: Path) -> None:
    """A populated schema behind head is ``pending``, not ``current``.

    This is the state where forward-only migrations would rewrite existing data,
    so it must be distinguishable from both other states.
    """
    run_mod.upgrade(_DB)
    run_mod.downgrade(_DB, "-1")

    state, current, heads = status(_DB)
    assert state == STATE_PENDING
    assert current and current != heads


def test_status_rejects_an_unknown_database() -> None:
    with pytest.raises(ValueError, match="Unknown database"):
        status("not-a-database")


def test_check_exits_nonzero_and_reports_uninitialized(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI contract `jenticctl` depends on: verdict line + exit code.

    The exit code is deliberately not 1, so a caller can tell "migrations are
    needed" apart from "the check itself failed" and never act on a non-answer.
    """
    code = run_mod.main(["--check"])
    out = capsys.readouterr().out

    assert code == CHECK_EXIT_NEEDS_MIGRATION
    assert "OVERALL uninitialized" in out
    # Per-database detail is present for diagnosis, one line each.
    for name in ("admin", "control", "registry"):
        assert f"STATUS {name} uninitialized" in out


def test_check_exits_zero_when_all_databases_are_current(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_mod.main([]) == 0
    capsys.readouterr()

    assert run_mod.main(["--check"]) == 0
    assert "OVERALL current" in capsys.readouterr().out


def test_check_reports_pending_when_any_database_is_behind(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One lagging database is enough to make the whole stack unsafe to start."""
    run_mod.main([])
    run_mod.downgrade(_DB, "-1")
    capsys.readouterr()

    code = run_mod.main(["--check"])
    out = capsys.readouterr().out

    assert code == CHECK_EXIT_NEEDS_MIGRATION
    assert "OVERALL pending" in out
    assert f"STATUS {_DB} pending" in out


def test_check_does_not_migrate(sqlite_stack: Path) -> None:
    """End-to-end read-only guarantee for the CLI entrypoint."""
    assert run_mod.main(["--check"]) == CHECK_EXIT_NEEDS_MIGRATION
    for name in ("admin", "control", "registry"):
        assert _tables(sqlite_stack / f"{name}.db") == set(), f"{name} was modified by --check"
