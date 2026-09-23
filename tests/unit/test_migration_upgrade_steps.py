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
    names = {
        str(row[0])
        for row in _query(
            sqlite_stack / "admin.db",
            "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            " AND tbl_name IN ('agent_credential_bindings', 'agent_toolkit_bindings')",
        )
    }
    assert names == {
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
