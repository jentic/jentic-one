"""Up/down tests for the ``execution_records`` toolkit/credential index migrations.

Pins revisions ``c00de2f5d777`` and ``d4b8e2f6a901`` (#1796) against a real
(SQLite) database:
- ``c00de2f5d777``: upgrade creates ``ix_execution_records_repeated_failure_credential_scan``
  as a partial index on failed rows and drops the toolkit-keyed scan index,
  downgrade swaps them back, and a re-upgrade restores the head state.
- ``d4b8e2f6a901``: upgrade drops ``ix_execution_records_toolkit_started``,
  downgrade recreates it on ``(toolkit_id, started_at)``, and a re-upgrade
  restores the head state.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from jentic_one.migrations.run import downgrade, upgrade

_DB = "admin"
_NEW_INDEX = "ix_execution_records_repeated_failure_credential_scan"
_OLD_INDEX = "ix_execution_records_repeated_failure_scan"
_REVISION = "c00de2f5d777"  # pragma: allowlist secret
_PARENT_REV = "3306fb9172f1"  # pragma: allowlist secret
_DROP_TOOLKIT_STARTED_REV = "d4b8e2f6a901"  # pragma: allowlist secret
_DROP_TOOLKIT_STARTED_PARENT_REV = "eb3688e956fc"  # pragma: allowlist secret
_TOOLKIT_STARTED_INDEX = "ix_execution_records_toolkit_started"


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

    downgrade(_DB, _REVISION)
    at_rev = _indexes(db_path)

    downgrade(_DB, _PARENT_REV)
    downgraded = _indexes(db_path)
    assert _NEW_INDEX not in downgraded
    assert "(actor_id, toolkit_id, operation_id, started_at)" in downgraded[_OLD_INDEX]
    assert set(at_rev) - {_NEW_INDEX} == set(downgraded) - {_OLD_INDEX}

    upgrade(_DB)
    assert _indexes(db_path) == at_head


def test_drop_toolkit_started_index_upgrade_downgrade_roundtrip(
    sqlite_stack: Path,
) -> None:
    """Pins revision ``d4b8e2f6a901`` (#1796): upgrade drops
    ``ix_execution_records_toolkit_started``, downgrade recreates it on
    ``(toolkit_id, started_at)``, and re-upgrade restores head."""
    db_path = sqlite_stack / f"{_DB}.db"

    upgrade(_DB)
    at_head = _indexes(db_path)
    assert _TOOLKIT_STARTED_INDEX not in at_head

    downgrade(_DB, _DROP_TOOLKIT_STARTED_REV)
    at_drop_rev = _indexes(db_path)
    assert _TOOLKIT_STARTED_INDEX not in at_drop_rev

    downgrade(_DB, _DROP_TOOLKIT_STARTED_PARENT_REV)
    downgraded = _indexes(db_path)
    assert "(toolkit_id, started_at)" in downgraded[_TOOLKIT_STARTED_INDEX]
    assert set(downgraded) - {_TOOLKIT_STARTED_INDEX} == set(at_drop_rev)

    upgrade(_DB)
    assert _indexes(db_path) == at_head
