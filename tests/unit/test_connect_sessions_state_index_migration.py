"""Up/down test for the connect_sessions ``(state, created_at)`` index migration.

Pins revision ``z7b8c9d0e1f2`` against a real (SQLite) database: upgrade
creates ``ix_connect_sessions_state_created_at``, downgrade removes exactly
that index and nothing else, and a re-upgrade restores it — i.e. the
migration is genuinely reversible, not just syntactically paired.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from jentic_one.migrations.run import downgrade, upgrade

_DB = "control"
_INDEX = "ix_connect_sessions_state_created_at"
# The migration under test and its parent — downgrading to the parent must
# remove exactly the index this migration created.
_PARENT_REV = "y6a7b8c9d0e1"  # pragma: allowlist secret


def _indexes(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    return {name for (name,) in rows}


def test_state_created_at_index_upgrade_downgrade_roundtrip(sqlite_stack: Path) -> None:
    db_path = sqlite_stack / f"{_DB}.db"

    upgrade(_DB)
    assert _INDEX in _indexes(db_path)

    downgrade(_DB, _PARENT_REV)
    after_down = _indexes(db_path)
    assert _INDEX not in after_down
    # The sibling connect_sessions indexes from v3d4e5f6a7b8 must survive.
    assert "ix_connect_sessions_agent" in after_down
    assert "ix_connect_sessions_poll_token" in after_down

    upgrade(_DB)
    assert _INDEX in _indexes(db_path)
