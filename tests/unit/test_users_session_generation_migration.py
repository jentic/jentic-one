"""Up/down test for the users session-generation / deleted-at migration.

Pins revision ``eb3688e956fc`` against a real (SQLite) database: upgrade adds
``users.session_generation`` (existing rows read ``0``) and ``users.deleted_at``
(backfilled for inactive rows carrying the delete tombstone email, ``NULL``
otherwise — including an active row that carries it); downgrade drops both
columns in batch mode and a re-upgrade restores them.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from jentic_one.migrations.run import downgrade, upgrade

_DB = "admin"
_REV = "eb3688e956fc"  # pragma: allowlist secret
_PARENT_REV = "c00de2f5d777"  # pragma: allowlist secret
_LIVE = "usr_live"
_DELETED = "usr_deleted"
# Tombstone email but active: deleted, then re-enabled before enable refused it.
_REVIVED = "usr_revived"


def _columns(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(users)")}


def _indexes(db_path: Path) -> dict[str, str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master"
            " WHERE type = 'index' AND tbl_name = 'users' AND sql IS NOT NULL"
        ).fetchall()
    return dict(rows)


def _insert_user(db_path: Path, user_id: str, email: str, *, active: bool = False) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO users (id, email, first_name, last_name, active,"
            " auth_provider, must_change_password, invite_state, created_at, updated_at)"
            " VALUES (?, ?, 'A', 'B', ?, 'local', 0, 'redeemed',"
            " '2026-01-01 00:00:00', '2026-02-01 00:00:00')",
            (user_id, email, int(active)),
        )


def test_users_session_generation_upgrade_downgrade_roundtrip(sqlite_stack: Path) -> None:
    db_path = sqlite_stack / f"{_DB}.db"

    upgrade(_DB, _PARENT_REV)
    assert not {"session_generation", "deleted_at"} & _columns(db_path)
    parent_indexes = _indexes(db_path)
    assert "lower(email)" in parent_indexes["ix_users_email_lower"]
    _insert_user(db_path, _LIVE, "live@example.com")
    _insert_user(db_path, _DELETED, f"deleted-{_DELETED}@local")
    _insert_user(db_path, _REVIVED, f"deleted-{_REVIVED}@local", active=True)

    upgrade(_DB, _REV)
    assert {"session_generation", "deleted_at"} <= _columns(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = {
            row[0]: row[1:]
            for row in conn.execute("SELECT id, session_generation, deleted_at FROM users")
        }
    assert rows[_LIVE] == (0, None)
    assert rows[_REVIVED] == (0, None)
    generation, deleted_at = rows[_DELETED]
    assert generation == 0
    assert deleted_at is not None and deleted_at.startswith("2026-02-01")

    downgrade(_DB, _PARENT_REV)
    columns = _columns(db_path)
    assert not {"session_generation", "deleted_at"} & columns
    # The table rebuild keeps every index, including the expression-based
    # unique email index batch mode cannot reflect.
    assert _indexes(db_path).keys() == parent_indexes.keys()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone() == (3,)

    upgrade(_DB, _REV)
    assert {"session_generation", "deleted_at"} <= _columns(db_path)
