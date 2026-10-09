"""Up/down test for the connect-session target-kind + outcomes migration.

Pins revision ``bb2c3d4e5f6a`` against a real (SQLite) database: existing
sessions become ``vendor`` targets, the open-API-target index deduplicates only
live ``api`` sessions that name an agent, the outcomes table appears, and the
downgrade removes ``api`` targets (and their pending credentials, never a
connected one) before dropping the columns. The Postgres side is covered by
``tests/integration/control/test_connect_session_targets_migration.py``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jentic_one.migrations.run import downgrade, upgrade

_DB = "control"
_PARENT_REV = "aa1b2c3d4e5f"  # pragma: allowlist secret
_REV = "bb2c3d4e5f6a"  # pragma: allowlist secret
_NEW_COLUMNS = {
    "target_kind",
    "api_name",
    "api_version",
    "scheme_type",
    "scheme_location",
    "scheme_field_name",
    "pinned_hosts",
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {name for (name,) in rows}


def _insert_credential(conn: sqlite3.Connection, credential_id: str, state: str) -> None:
    conn.execute(
        "INSERT INTO credentials (id, type, name, api_vendor, state)"
        " VALUES (?, 'api_key', 'Example', 'example-com', ?)",
        (credential_id, state),
    )


def _insert_session(
    conn: sqlite3.Connection,
    session_id: str,
    credential_id: str,
    *,
    state: str = "created",
    target_kind: str | None = None,
    agent_id: str | None = "agnt_1",
    api_version: str | None = None,
) -> None:
    columns = "id, credential_id, vendor, agent_id, initiator_actor_id, state, resolved_flow"
    values: list[object] = [
        session_id,
        credential_id,
        "example-com",
        agent_id,
        "agnt_1",
        state,
        "manual_api_key",
    ]
    if target_kind is not None:
        columns += ", target_kind, api_name, api_version"
        values += [target_kind, "example", api_version]
    columns += ", poll_token"
    values.append(f"digest-{session_id}")
    placeholders = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO connect_sessions ({columns}) VALUES ({placeholders})", values)


def test_targets_and_outcomes_migration_roundtrip(sqlite_stack: Path) -> None:
    db_path = sqlite_stack / f"{_DB}.db"

    upgrade(_DB, _PARENT_REV)
    with sqlite3.connect(db_path) as conn:
        _insert_credential(conn, "cred_vendor", "pending")
        _insert_session(conn, "cs_vendor", "cred_vendor")

    upgrade(_DB, _REV)
    with sqlite3.connect(db_path) as conn:
        assert _columns(conn, "connect_sessions") >= _NEW_COLUMNS
        assert "connect_session_outcomes" in _tables(conn)
        (kind,) = conn.execute(
            "SELECT target_kind FROM connect_sessions WHERE id = 'cs_vendor'"
        ).fetchone()
        assert kind == "vendor"

        # Open API targets dedupe per agent and identity …
        _insert_credential(conn, "cred_live", "pending")
        _insert_session(conn, "cs_live", "cred_live", target_kind="api", api_version="1.0.0")
        _insert_credential(conn, "cred_dup", "pending")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_session(
                conn,
                "cs_dup",
                "cred_dup",
                state="awaiting_app",
                target_kind="api",
                api_version="1.0.0",
            )
        # … but an ended session, no agent, or a vendor target never collides.
        _insert_credential(conn, "cred_connected", "connected")
        _insert_session(
            conn,
            "cs_connected",
            "cred_connected",
            state="connected",
            target_kind="api",
            api_version="1.0.0",
        )
        _insert_session(
            conn, "cs_unbound", "cred_dup", target_kind="api", agent_id=None, api_version="1.0.0"
        )
        _insert_credential(conn, "cred_vendor_2", "pending")
        _insert_session(conn, "cs_vendor_2", "cred_vendor_2")

    downgrade(_DB, _PARENT_REV)
    with sqlite3.connect(db_path) as conn:
        assert not _NEW_COLUMNS & _columns(conn, "connect_sessions")
        assert "connect_session_outcomes" not in _tables(conn)
        sessions = {sid for (sid,) in conn.execute("SELECT id FROM connect_sessions")}
        credentials = {cid for (cid,) in conn.execute("SELECT id FROM credentials")}
    # Vendor sessions survive; every API target is gone, and so are the
    # pending credentials of the live ones. The connected credential stays.
    assert sessions == {"cs_vendor", "cs_vendor_2"}
    assert credentials == {"cred_vendor", "cred_vendor_2", "cred_connected"}

    upgrade(_DB, _REV)
    with sqlite3.connect(db_path) as conn:
        assert _columns(conn, "connect_sessions") >= _NEW_COLUMNS
