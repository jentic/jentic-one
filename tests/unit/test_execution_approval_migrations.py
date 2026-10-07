"""Up/down tests for the require-approval migrations.

Pins against a real (SQLite) database that each migration is genuinely
reversible, not just syntactically paired:

- admin ``3306fb9172f1`` creates ``execution_approvals`` with its indexes;
  downgrading to its parent drops exactly that table and leaves ``jobs``;
- control ``9f7b048514c6`` widens the rule ``effect`` columns to 16 chars so
  ``require-approval`` fits; downgrading to its parent restores 10.

Each re-upgrade restores the migrated shape.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from jentic_one.migrations.run import downgrade, upgrade

_ADMIN_PARENT_REV = "0679072d60eb"  # pragma: allowlist secret
_CONTROL_PARENT_REV = "g4d5e6f7a8b9"  # pragma: allowlist secret

_APPROVAL_INDEXES = {
    "ix_execution_approvals_agent_state",
    "ix_execution_approvals_created_at",
    "ix_execution_approvals_created_by",
    "ix_execution_approvals_job_id",
    "ix_execution_approvals_state_expires",
    "uq_execution_approvals_pending_fingerprint",
}
_RULE_TABLES = ("agent_permission_rules", "permission_rule_set_rules")


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {name for (name,) in rows}


def _indexes(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name = ?", (table,)
        ).fetchall()
    return {name for (name,) in rows}


def _effect_type(db_path: Path, table: str) -> str:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return next(str(col_type).upper() for _, name, col_type, *_ in rows if name == "effect")


def test_execution_approvals_table_upgrade_downgrade_roundtrip(sqlite_stack: Path) -> None:
    db_path = sqlite_stack / "admin.db"

    upgrade("admin")
    assert "execution_approvals" in _tables(db_path)
    assert _indexes(db_path, "execution_approvals") >= _APPROVAL_INDEXES

    downgrade("admin", _ADMIN_PARENT_REV)
    after_down = _tables(db_path)
    assert "execution_approvals" not in after_down
    assert "jobs" in after_down

    upgrade("admin")
    assert "execution_approvals" in _tables(db_path)
    assert _indexes(db_path, "execution_approvals") >= _APPROVAL_INDEXES


def test_rule_effect_widening_upgrade_downgrade_roundtrip(sqlite_stack: Path) -> None:
    db_path = sqlite_stack / "control.db"

    upgrade("control")
    assert {_effect_type(db_path, t) for t in _RULE_TABLES} == {"VARCHAR(16)"}

    downgrade("control", _CONTROL_PARENT_REV)
    assert {_effect_type(db_path, t) for t in _RULE_TABLES} == {"VARCHAR(10)"}

    upgrade("control")
    assert {_effect_type(db_path, t) for t in _RULE_TABLES} == {"VARCHAR(16)"}
