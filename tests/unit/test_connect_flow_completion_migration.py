"""Upgrade/downgrade coverage for per-flow credential connect completion."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from jentic_one.migrations.run import downgrade, upgrade

_DB = "control"
_PARENT_REV = "d9305dcd765e"  # pragma: allowlist secret
_COLUMNS = {
    "connect_state_nonces": "completed_at",
    "device_authorization_credentials": "flow_nonce",
}


def _columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {row[1] for row in rows}


def test_connect_flow_completion_columns_roundtrip(sqlite_stack: Path) -> None:
    db_path = sqlite_stack / f"{_DB}.db"

    upgrade(_DB)
    for table, column in _COLUMNS.items():
        assert column in _columns(db_path, table)

    downgrade(_DB, _PARENT_REV)
    for table, column in _COLUMNS.items():
        assert column not in _columns(db_path, table)

    upgrade(_DB)
    for table, column in _COLUMNS.items():
        assert column in _columns(db_path, table)
