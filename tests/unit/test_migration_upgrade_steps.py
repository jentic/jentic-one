"""Tests for the migration runner's post-migration upgrade steps.

Pins, against real SQLite databases driven through the CLI entrypoint
(``migrations.run.main``), the runner contract: steps run only after a full
upgrade to head, a failing step exits ``EXIT_UPGRADE_STEP_FAILED`` without
being ledgered, and an operator skip defers a step. No step is registered
since theme-5 Phase 6b deleted the toolkit steps, so the tests register a
fake one; the index-repair tests for ``5c7e2a9d4f16`` also live here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jentic_one.control.services import upgrade_steps as steps_mod
from jentic_one.control.services.upgrade_steps import UpgradeStepOutcome, UpgradeStepSpec
from jentic_one.migrations import run as run_mod
from jentic_one.shared.context import Context

_STEP = "test_step_runner"


def _query(db: Path, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).fetchall()


def _execute(db: Path, sql: str, params: tuple[object, ...] = ()) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(sql, params)
        conn.commit()


def _ledger(stack: Path) -> list[str]:
    return [str(row[0]) for row in _query(stack / "control.db", "SELECT name FROM upgrade_steps")]


_BINDING_INDEXES = {
    "ix_agent_credential_bindings_agent_id",
    "ix_agent_credential_bindings_credential_id",
    "ix_agent_credential_bindings_rule_set_id",
    "ix_agent_credential_bindings_created_at",
    "ix_agent_credential_bindings_created_by",
}


def _binding_indexes(stack: Path) -> set[str]:
    return {
        str(row[0])
        for row in _query(
            stack / "admin.db",
            "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            " AND tbl_name = 'agent_credential_bindings'",
        )
    }


def test_binding_table_indexes_survive_the_sqlite_rebuild(sqlite_stack: Path) -> None:
    """Pins the SQLite batch rebuild in b9d0e1f2a3b4: it must keep every named index."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    assert _binding_indexes(sqlite_stack) == _BINDING_INDEXES


def test_repair_migration_restores_indexes_a_prior_rebuild_dropped(sqlite_stack: Path) -> None:
    """A dev database that ran the original b9d0e1f2a3b4 regains its indexes."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    down_to = "b9d0e1f2a3b4"  # pragma: allowlist secret
    assert run_mod.main(["--db", "admin", "--direction", "down", "--target", down_to]) == 0
    for name in _BINDING_INDEXES:
        _execute(sqlite_stack / "admin.db", f"DROP INDEX {name}")

    assert run_mod.main(["--db", "admin"]) == 0

    assert _binding_indexes(sqlite_stack) == _BINDING_INDEXES


def _register(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> list[int]:
    """Register one fake step for the runner; returns its call log."""
    calls: list[int] = []

    async def _run(_ctx: Context) -> UpgradeStepOutcome:
        calls.append(1)
        if fail:
            raise RuntimeError("simulated step failure")
        return UpgradeStepOutcome(name=_STEP, action="performed")

    monkeypatch.setattr(steps_mod, "STEPS", (UpgradeStepSpec(name=_STEP, run=_run),))
    return calls


def test_no_registered_steps_is_a_clean_no_op(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Post-Phase-6b a full upgrade runs no steps and never touches the ledger."""
    assert run_mod.main([]) == 0
    assert "upgrade step" not in capsys.readouterr().out
    assert _ledger(sqlite_stack) == []


def test_full_upgrade_runs_a_registered_step_exactly_once(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _register(monkeypatch)

    assert run_mod.main([]) == 0
    assert f"upgrade step {_STEP}: performed" in capsys.readouterr().out
    assert run_mod.main([]) == 0
    assert f"upgrade step {_STEP}: already_done" in capsys.readouterr().out
    assert len(calls) == 1
    assert _ledger(sqlite_stack) == [_STEP]


@pytest.mark.parametrize(
    "argv",
    [["--skip-upgrade-steps"], ["--db", "admin"], ["--db", "control"], ["--target", "heads"]],
    ids=["skip-flag", "admin-only", "control-only", "pinned-target"],
)
def test_steps_do_not_run_without_a_full_upgrade(
    sqlite_stack: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    calls = _register(monkeypatch)

    assert run_mod.main(argv) == 0

    assert calls == []
    assert _ledger(sqlite_stack) == []


def test_failing_step_exits_with_the_upgrade_step_code(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed step stops the upgrade and is not ledgered, so the next run retries it."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    _register(monkeypatch, fail=True)
    capsys.readouterr()

    assert run_mod.main([]) == run_mod.EXIT_UPGRADE_STEP_FAILED
    captured = capsys.readouterr()
    assert f"upgrade step {_STEP}: failed" in captured.out
    assert "left work undone" in captured.err
    assert _ledger(sqlite_stack) == []


def test_unexpected_error_exits_with_the_upgrade_step_code(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schema at head, steps unrunnable: exit 4 ("steps undone"), not a traceback."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    _register(monkeypatch)

    def broken_config() -> None:
        raise RuntimeError("simulated config failure")

    monkeypatch.setattr(run_mod, "load_config", broken_config)
    capsys.readouterr()

    assert run_mod.main([]) == run_mod.EXIT_UPGRADE_STEP_FAILED
    assert "upgrade steps could not run" in capsys.readouterr().err


def test_skip_one_step_defers_it(sqlite_stack: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    calls = _register(monkeypatch)

    assert run_mod.main(["--skip-upgrade-step", _STEP]) == 0
    assert calls == []
    assert _ledger(sqlite_stack) == [], "a skipped step runs on the next full upgrade"

    assert run_mod.main([]) == 0
    assert len(calls) == 1


def test_unknown_step_name_is_rejected(sqlite_stack: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        run_mod.main(["--skip-upgrade-step", "no_such_step"])
    assert exc.value.code == 2
