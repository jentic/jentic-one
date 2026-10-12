"""Up/down test for the connect-session dedupe-key migration (``cc3d4e5f6a7b``).

Pins the backfill against a real (SQLite) database holding open duplicate
agent-started OAuth sessions, which are legal before this revision: the newest
of each group keeps the key, an older ``created`` duplicate is expired (outcome
row written, pending credential and session row deleted), an older ``polling``
duplicate keeps a NULL key and survives, and user-started sessions are left
alone. The unique index then refuses a second open session with the same key.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jentic_one.control.services.integrations.dedupe import oauth_dedupe_key
from jentic_one.migrations.control.versions import (
    cc3d4e5f6a7b_add_connect_session_dedupe_keys as migration,
)
from jentic_one.migrations.run import downgrade, upgrade

_DB = "control"
_PARENT_REV = "bb2c3d4e5f6a"  # pragma: allowlist secret
_REV = "cc3d4e5f6a7b"  # pragma: allowlist secret


def _credential(conn: sqlite3.Connection, cid: str, *, registration: str | None = None) -> None:
    conn.execute(
        "INSERT INTO credentials (id, type, name, api_vendor, state, oauth_app_registration_id)"
        " VALUES (?, 'OAUTH2_AUTHORIZATION_CODE', 'GitHub', 'github-com', 'pending', ?)",
        (cid, registration),
    )


def _session(
    conn: sqlite3.Connection,
    sid: str,
    cid: str,
    *,
    created_at: str,
    state: str = "created",
    initiator: str = "agnt_1",
    scopes: list[str] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO connect_sessions (id, credential_id, vendor, agent_id, initiator_actor_id,"
        " state, resolved_flow, poll_token, requested_scopes, created_at)"
        " VALUES (?, ?, 'github', 'agnt_1', ?, ?, 'authorization_code', ?, ?, ?)",
        (sid, cid, initiator, state, f"digest-{sid}", json.dumps(scopes or ["repo"]), created_at),
    )


def test_migration_key_matches_the_service_key() -> None:
    for flow, registration, scopes in (
        ("authorization_code", None, ["repo", "read:user", "repo"]),
        ("device_authorization", "oar_1", []),
    ):
        assert migration._dedupe_key(flow, registration, scopes) == oauth_dedupe_key(
            resolved_flow=flow, registration_id=registration, requested_scopes=scopes
        )


def test_dedupe_migration_expires_older_duplicates(sqlite_stack: Path) -> None:
    db_path = sqlite_stack / f"{_DB}.db"
    upgrade(_DB, _PARENT_REV)
    with sqlite3.connect(db_path) as conn:
        for cid in ("cred_old", "cred_mid", "cred_new", "cred_other", "cred_user", "cred_user2"):
            _credential(conn, cid)
        _session(conn, "cs_old", "cred_old", created_at="2026-10-01 10:00:00")
        _session(conn, "cs_mid", "cred_mid", created_at="2026-10-01 10:05:00", state="polling")
        _session(conn, "cs_new", "cred_new", created_at="2026-10-01 10:10:00")
        _session(conn, "cs_other", "cred_other", created_at="2026-10-01 10:00:00", scopes=["gist"])
        _session(conn, "cs_user", "cred_user", created_at="2026-10-01 10:00:00", initiator="usr_1")
        _session(
            conn, "cs_user2", "cred_user2", created_at="2026-10-01 10:01:00", initiator="usr_1"
        )

    upgrade(_DB, _REV)
    with sqlite3.connect(db_path) as conn:
        keys = dict(conn.execute("SELECT id, dedupe_key FROM connect_sessions").fetchall())
        credentials = {cid for (cid,) in conn.execute("SELECT id FROM credentials")}
        outcomes = conn.execute(
            "SELECT session_id, outcome, poll_token_hash FROM connect_session_outcomes"
        ).fetchall()

        # Newest of the group keeps the key; the older polling one survives
        # without one; the older created one is gone, with an outcome row.
        assert keys["cs_new"] is not None
        assert keys["cs_mid"] is None
        assert "cs_old" not in keys and "cred_old" not in credentials
        assert outcomes == [("cs_old", "expired", "digest-cs_old")]
        # A different scope set is a different ask; user-started sessions get no key.
        assert keys["cs_other"] is not None and keys["cs_other"] != keys["cs_new"]
        assert keys["cs_user"] is None and keys["cs_user2"] is None

        # The index now refuses a second open session for the same ask.
        _credential(conn, "cred_dup")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO connect_sessions (id, credential_id, vendor, agent_id,"
                " initiator_actor_id, state, resolved_flow, poll_token, dedupe_key)"
                " VALUES ('cs_dup', 'cred_dup', 'github', 'agnt_1', 'agnt_1', 'created',"
                " 'authorization_code', 'digest-dup', ?)",
                (keys["cs_new"],),
            )

    downgrade(_DB, _PARENT_REV)
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(connect_sessions)")}
        assert not {"dedupe_key", "vendor_key"} & columns
    upgrade(_DB, _REV)
