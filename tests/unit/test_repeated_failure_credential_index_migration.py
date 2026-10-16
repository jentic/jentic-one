"""Up/down test for the credential-keyed repeated-failure index migration.

Pins revision ``c00de2f5d777`` against a real (SQLite) database: upgrade
creates ``ix_execution_records_repeated_failure_credential_scan`` as a partial
index on failed rows and drops the toolkit-keyed scan index, downgrade swaps
them back, and a re-upgrade restores the head state.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from jentic_one.migrations.run import downgrade, upgrade

_DB = "admin"
_NEW_INDEX = "ix_execution_records_repeated_failure_credential_scan"
_OLD_INDEX = "ix_execution_records_repeated_failure_scan"
_PARENT_REV = "3306fb9172f1"  # pragma: allowlist secret


def _indexes(db_path: Path) -> dict[str, str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master"
            " WHERE type = 'index' AND tbl_name = 'execution_records' AND sql IS NOT NULL"
        ).fetchall()
    return dict(rows)


def test_repeated_failure_credential_index_upgrade_downgrade_roundtrip(
    sqlite_stack: Path,
) -> None:
    db_path = sqlite_stack / f"{_DB}.db"

    upgrade(_DB)
    at_head = _indexes(db_path)
    assert _OLD_INDEX not in at_head
    assert "(actor_id, credential_id, operation_id, started_at)" in at_head[_NEW_INDEX]
    assert "WHERE status = 'failed'" in at_head[_NEW_INDEX]

    downgrade(_DB, _PARENT_REV)
    downgraded = _indexes(db_path)
    assert _NEW_INDEX not in downgraded
    assert "(actor_id, toolkit_id, operation_id, started_at)" in downgraded[_OLD_INDEX]
    assert set(at_head) - {_NEW_INDEX} == set(downgraded) - {_OLD_INDEX}

    upgrade(_DB)
    assert _indexes(db_path) == at_head
