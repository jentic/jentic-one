"""Tests for the migration runner's post-migration upgrade steps.

Pins, against real SQLite databases driven through the CLI entrypoint
(``migrations.run.main``), the property the 0.40 upgrade depends on: a full
upgrade flattens every toolkit-reachable ``(agent, credential)`` pair into a
direct binding before the new version serves traffic, and does so **exactly
once** — a later upgrade must never re-derive a binding an operator purged
from the toolkit rows that stay behind until the drop release.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jentic_one.control.services.key_retirement import KeyRetirementService
from jentic_one.control.services.toolkit_flattening import ToolkitFlatteningService
from jentic_one.migrations import run as run_mod

_AGENT = "agnt_ustest_a"
_CRED = "cred_ustest_1"
_TOOLKIT = "tk_ustest_1"


def _query(db: Path, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).fetchall()


def _execute(db: Path, sql: str, params: tuple[object, ...] = ()) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(sql, params)
        conn.commit()


def _seed_toolkit_bound_agent(stack: Path) -> None:
    """One agent reaching one credential through one toolkit with one allow rule."""
    control, admin = stack / "control.db", stack / "admin.db"
    _execute(control, "INSERT INTO toolkits (id, name) VALUES (?, 'us-toolkit')", (_TOOLKIT,))
    _execute(
        control,
        "INSERT INTO credentials (id, type, name, api_vendor)"
        " VALUES (?, 'token_value', 'us-cred', 'ustest.local')",
        (_CRED,),
    )
    _execute(
        control,
        "INSERT INTO toolkit_credential_bindings (id, toolkit_id, credential_id)"
        " VALUES ('tcb_ustest_1', ?, ?)",
        (_TOOLKIT, _CRED),
    )
    _execute(
        control,
        "INSERT INTO toolkit_permission_rules"
        " (id, toolkit_id, credential_id, effect, path, match_mode, sequence, is_system)"
        " VALUES ('tpr_ustest_1', ?, ?, 'allow', '/.*', 'regex', 0, 0)",
        (_TOOLKIT, _CRED),
    )
    _execute(
        admin,
        "INSERT INTO users (id, email, first_name, last_name)"
        " VALUES ('usr_ustest', 'us@test.local', 'U', 'S')",
    )
    _execute(
        admin,
        "INSERT INTO agents (id, name, registered_by, status)"
        " VALUES (?, 'us-agent', 'usr_ustest', 'approved')",
        (_AGENT,),
    )
    _execute(
        admin,
        "INSERT INTO agent_toolkit_bindings (id, agent_id, toolkit_id)"
        " VALUES ('atb_ustest_1', ?, ?)",
        (_AGENT, _TOOLKIT),
    )


def _direct_bindings(stack: Path) -> list[tuple[object, ...]]:
    return _query(
        stack / "admin.db",
        "SELECT agent_id, credential_id, rule_set_id FROM agent_credential_bindings"
        " WHERE agent_id = ?",
        (_AGENT,),
    )


def _ledger(stack: Path) -> list[str]:
    return [str(row[0]) for row in _query(stack / "control.db", "SELECT name FROM upgrade_steps")]


_BINDING_INDEXES = {
    "ix_agent_credential_bindings_agent_id",
    "ix_agent_credential_bindings_credential_id",
    "ix_agent_credential_bindings_rule_set_id",
    "ix_agent_credential_bindings_created_at",
    "ix_agent_credential_bindings_created_by",
    "ix_agent_toolkit_bindings_agent_id",
    "ix_agent_toolkit_bindings_toolkit_id",
    "ix_agent_toolkit_bindings_created_at",
    "ix_agent_toolkit_bindings_created_by",
}


def _binding_indexes(stack: Path) -> set[str]:
    return {
        str(row[0])
        for row in _query(
            stack / "admin.db",
            "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            " AND tbl_name IN ('agent_credential_bindings', 'agent_toolkit_bindings')",
        )
    }


def test_full_upgrade_flattens_toolkit_bound_agents_exactly_once(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Schema at head with the steps deferred — the state of an install whose
    # toolkit graph predates the direct-binding model.
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    _seed_toolkit_bound_agent(sqlite_stack)
    assert _direct_bindings(sqlite_stack) == []
    capsys.readouterr()

    assert run_mod.main([]) == 0
    out = capsys.readouterr().out
    assert "upgrade step theme5_flatten_toolkits: performed" in out
    bindings = _direct_bindings(sqlite_stack)
    assert len(bindings) == 1
    _agent, credential_id, rule_set_id = bindings[0]
    assert credential_id == _CRED
    # The toolkit rule travelled with the pair — not a default-deny binding.
    assert rule_set_id is not None
    assert "theme5_flatten_toolkits" in _ledger(sqlite_stack)

    # The operator purges the binding; the toolkit rows are still there.
    _execute(
        sqlite_stack / "admin.db",
        "DELETE FROM agent_credential_bindings WHERE agent_id = ?",
        (_AGENT,),
    )
    assert run_mod.main([]) == 0
    assert "upgrade step theme5_flatten_toolkits: already_done" in capsys.readouterr().out
    assert _direct_bindings(sqlite_stack) == [], "a purged binding must stay purged"


@pytest.mark.parametrize(
    "argv",
    [["--skip-upgrade-steps"], ["--db", "admin"], ["--db", "control"]],
    ids=["skip-flag", "admin-only", "control-only"],
)
def test_steps_do_not_run_without_a_full_upgrade(sqlite_stack: Path, argv: list[str]) -> None:
    """A partial upgrade leaves the steps for the next full one."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    _seed_toolkit_bound_agent(sqlite_stack)

    assert run_mod.main(argv) == 0

    assert _direct_bindings(sqlite_stack) == []
    assert _ledger(sqlite_stack) == []


def test_pinned_target_does_not_run_steps(sqlite_stack: Path) -> None:
    """Only the default ``head`` target runs the steps; any explicit target skips them."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    _seed_toolkit_bound_agent(sqlite_stack)

    assert run_mod.main(["--target", "heads"]) == 0

    assert _ledger(sqlite_stack) == []


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


def test_flatten_failure_exits_with_the_upgrade_step_code(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken flatten must stop the upgrade — and must not be ledgered as done."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    _seed_toolkit_bound_agent(sqlite_stack)
    real_run = ToolkitFlatteningService.run

    async def boom(self: ToolkitFlatteningService, **_: object) -> None:
        raise RuntimeError("simulated flatten failure")

    monkeypatch.setattr(ToolkitFlatteningService, "run", boom)
    capsys.readouterr()

    assert run_mod.main([]) == run_mod.EXIT_UPGRADE_STEP_FAILED
    captured = capsys.readouterr()
    assert "upgrade step theme5_flatten_toolkits: failed" in captured.out
    assert "left work undone" in captured.err
    assert _ledger(sqlite_stack) == []

    # Fixed: the next run completes the step.
    monkeypatch.setattr(ToolkitFlatteningService, "run", real_run)
    assert run_mod.main([]) == 0
    assert len(_direct_bindings(sqlite_stack)) == 1


def test_key_retirement_failure_warns_but_does_not_block(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Key retirement is retried at boot; it must never block the flatten."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    _seed_toolkit_bound_agent(sqlite_stack)

    async def boom(self: KeyRetirementService, **_: object) -> None:
        raise RuntimeError("simulated retirement failure")

    monkeypatch.setattr(KeyRetirementService, "run", boom)
    capsys.readouterr()

    assert run_mod.main([]) == 0
    captured = capsys.readouterr()
    assert "upgrade step theme5_retire_toolkit_keys: failed" in captured.out
    assert "WARNING (theme5_retire_toolkit_keys)" in captured.err
    assert "upgrade step theme5_flatten_toolkits: performed" in captured.out
    assert len(_direct_bindings(sqlite_stack)) == 1


def test_unexpected_error_exits_with_the_upgrade_step_code(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schema at head, steps unrunnable: exit 4 ("steps undone"), not a traceback."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0

    def broken_config() -> None:
        raise RuntimeError("simulated config failure")

    monkeypatch.setattr(run_mod, "load_config", broken_config)
    capsys.readouterr()

    assert run_mod.main([]) == run_mod.EXIT_UPGRADE_STEP_FAILED
    assert "upgrade steps could not run" in capsys.readouterr().err


def test_skip_one_step_runs_the_other(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    _seed_toolkit_bound_agent(sqlite_stack)
    capsys.readouterr()

    assert run_mod.main(["--skip-upgrade-step", "theme5_flatten_toolkits"]) == 0
    out = capsys.readouterr().out
    assert "upgrade step theme5_flatten_toolkits: skipped" in out
    assert "upgrade step theme5_retire_toolkit_keys: performed" in out
    assert _direct_bindings(sqlite_stack) == []
    assert _ledger(sqlite_stack) == [], "a skipped step runs on the next full upgrade"

    assert run_mod.main([]) == 0
    assert len(_direct_bindings(sqlite_stack)) == 1


def test_ownerless_key_is_a_named_warning_not_a_failure(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unmigratable key names its recovery command; the upgrade proceeds."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    _seed_toolkit_bound_agent(sqlite_stack)
    _execute(
        sqlite_stack / "control.db",
        "INSERT INTO toolkit_keys (id, toolkit_id, revoked, key_preview, hashed_key,"
        " lookup_hash, created_by) VALUES ('tkk_ustest_1', ?, 0, 'jntc_live_ab', 'h',"
        " 'lookup-ustest', 'usr_gone')",
        (_TOOLKIT,),
    )
    capsys.readouterr()

    assert run_mod.main([]) == 0

    captured = capsys.readouterr()
    assert '"owner_unresolved": 1' in captured.out
    assert "retire-toolkit-keys --owner" in captured.err


def test_unknown_step_name_is_rejected(sqlite_stack: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        run_mod.main(["--skip-upgrade-step", "no_such_step"])
    assert exc.value.code == 2


def test_acknowledged_flatten_is_skipped_and_ledgered(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An operator who already verified + acknowledged keeps their curated state."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    _seed_toolkit_bound_agent(sqlite_stack)
    _execute(
        sqlite_stack / "control.db",
        "INSERT INTO toolkit_flattening_acks (id, acknowledged_at, legacy_pair_count,"
        " direct_binding_count, report_finding_count, tool_version)"
        " VALUES ('tfa_ustest_1', CURRENT_TIMESTAMP, 1, 1, 0, 'test')",
    )
    capsys.readouterr()

    assert run_mod.main([]) == 0

    assert "upgrade step theme5_flatten_toolkits: skipped" in capsys.readouterr().out
    assert _direct_bindings(sqlite_stack) == []
    assert _ledger(sqlite_stack) == ["theme5_flatten_toolkits"]


def test_steps_skip_once_the_toolkit_tables_are_gone(
    sqlite_stack: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Post-Phase-6b (tables dropped) the steps have nothing to do — never an error."""
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    for table in (
        "toolkit_permission_rules",
        "toolkit_credential_bindings",
        "toolkit_keys",
        "toolkits",
    ):
        _execute(sqlite_stack / "control.db", f"DROP TABLE {table}")
    _execute(sqlite_stack / "admin.db", "DROP TABLE agent_toolkit_bindings")
    capsys.readouterr()

    assert run_mod.main([]) == 0

    out = capsys.readouterr().out
    assert out.count('"reason": "toolkit_tables_absent"') == 2
    assert _ledger(sqlite_stack) == []
