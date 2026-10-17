"""Up/down test for the ``assertion_jtis`` table migration.

Pins revision ``bf1a695f4db0`` against a real (SQLite) database: upgrade
creates the table with its unique ``(issuer, jti_digest)`` index and the
``expires_at`` prune index, downgrade removes exactly those, and a re-upgrade
restores them.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jentic_one.migrations.run import downgrade, upgrade

_DB = "admin"
_TABLE = "assertion_jtis"
_UNIQUE = "ix_assertion_jtis_issuer_jti_digest"
_PRUNE = "ix_assertion_jtis_expires_at"
_PARENT_REV = "c00de2f5d777"  # pragma: allowlist secret


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {name for (name,) in rows}


def _indexes(db_path: Path) -> dict[str, bool]:
    """Index name -> unique, for the jti table."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(f"PRAGMA index_list('{_TABLE}')").fetchall()
    return {row[1]: bool(row[2]) for row in rows}


def test_assertion_jtis_upgrade_downgrade_roundtrip(sqlite_stack: Path) -> None:
    db_path = sqlite_stack / f"{_DB}.db"

    upgrade(_DB)
    assert _TABLE in _tables(db_path)
    indexes = _indexes(db_path)
    assert indexes[_UNIQUE] is True
    assert indexes[_PRUNE] is False

    downgrade(_DB, _PARENT_REV)
    after_down = _tables(db_path)
    assert _TABLE not in after_down
    assert "agents" in after_down

    upgrade(_DB)
    assert _TABLE in _tables(db_path)


def test_the_unique_index_refuses_a_second_record_of_one_jti(sqlite_stack: Path) -> None:
    db_path = sqlite_stack / f"{_DB}.db"
    upgrade(_DB)
    insert = (
        f"INSERT INTO {_TABLE} (id, created_at, issuer, jti_digest, expires_at) "
        "VALUES (?, '2026-01-01 00:00:00', ?, ?, '2026-01-01 00:05:00')"
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(insert, ("ajti_1", "agnt_a", "d" * 64))
        conn.execute(insert, ("ajti_2", "agnt_b", "d" * 64))  # other issuer: fine
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, ("ajti_3", "agnt_a", "d" * 64))
