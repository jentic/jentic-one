"""Up/down test for the events ``(created_by, actor_id)`` index migration.

Pins revision ``a1c3e5f7b9d2`` against a real (SQLite) database: upgrade
creates ``ix_events_created_by_actor`` and drops ``ix_events_created_by``;
downgrade restores exactly the single-column index and removes the new one;
a re-upgrade lands back on the new index.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from jentic_one.migrations.run import downgrade, upgrade

_DB = "admin"
_NEW_INDEX = "ix_events_created_by_actor"
_OLD_INDEX = "ix_events_created_by"
_PARENT_REV = "c00de2f5d777"  # pragma: allowlist secret


def _index_columns(db_path: Path) -> dict[str, list[str]]:
    with sqlite3.connect(db_path) as conn:
        names = [
            name
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='events'"
            ).fetchall()
        ]
        return {
            name: [row[2] for row in conn.execute(f"PRAGMA index_info('{name}')").fetchall()]
            for name in names
        }


def test_created_by_actor_index_upgrade_downgrade_roundtrip(sqlite_stack: Path) -> None:
    db_path = sqlite_stack / f"{_DB}.db"

    upgrade(_DB)
    after_up = _index_columns(db_path)
    assert after_up[_NEW_INDEX] == ["created_by", "actor_id"]
    assert _OLD_INDEX not in after_up
    # The sibling events indexes must survive.
    assert after_up["ix_events_actor"] == ["actor_id", "actor_type"]

    downgrade(_DB, _PARENT_REV)
    after_down = _index_columns(db_path)
    assert after_down[_OLD_INDEX] == ["created_by"]
    assert _NEW_INDEX not in after_down
    assert "ix_events_actor" in after_down

    upgrade(_DB)
    after_reup = _index_columns(db_path)
    assert after_reup[_NEW_INDEX] == ["created_by", "actor_id"]
    assert _OLD_INDEX not in after_reup
