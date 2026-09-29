"""Up/down test for the connect_sessions poll-token hashing migration.

Pins revision ``e1a2b3c4d5f6`` against a real (SQLite) database: a plaintext
token written before the upgrade is rewritten in place to its SHA-256 digest
(so a client still holding the plaintext keeps verifying), an existing digest
is left alone, the schema is untouched, and a downgrade keeps the digest (it
cannot be reversed).
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from jentic_one.migrations.run import downgrade, upgrade
from jentic_one.shared.crypto import hash_secret

_DB = "control"
_PARENT_REV = "z7b8c9d0e1f2"  # pragma: allowlist secret
_REV = "e1a2b3c4d5f6"  # pragma: allowlist secret
_PLAINTEXT = "E0p0Xk1-3ZB2Qm4vJ7yFh9s8RtUwLcNaGdKeIoPqSbV"  # pragma: allowlist secret
_ALREADY_HASHED = hashlib.sha256(b"issued-after-upgrade").hexdigest()


def _columns(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("PRAGMA table_info(connect_sessions)").fetchall()
    return {row[1] for row in rows}


def _indexes(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    return {name for (name,) in rows}


def _insert_session(db_path: Path, session_id: str, token: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO connect_sessions"
            " (id, credential_id, vendor, initiator_actor_id, state, resolved_flow, poll_token)"
            " VALUES (?, 'cred_1', 'github', 'usr_1', 'created', 'device_authorization', ?)",
            (session_id, token),
        )


def _token_for(db_path: Path, session_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        (value,) = conn.execute(
            "SELECT poll_token FROM connect_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    return str(value)


def test_poll_token_hash_migration_hashes_in_place_and_is_idempotent(sqlite_stack: Path) -> None:
    db_path = sqlite_stack / f"{_DB}.db"

    upgrade(_DB, _PARENT_REV)
    _insert_session(db_path, "cs_plain", _PLAINTEXT)
    # A value that is already a digest must be left untouched (idempotency).
    _insert_session(db_path, "cs_digest", _ALREADY_HASHED)

    upgrade(_DB, _REV)
    # Data-only revision: the column and its unique index keep their names so
    # a previous release still serving during a rolling upgrade can load rows.
    assert "poll_token" in _columns(db_path)
    assert "ix_connect_sessions_poll_token" in _indexes(db_path)
    assert _token_for(db_path, "cs_plain") == hash_secret(_PLAINTEXT)
    assert _token_for(db_path, "cs_digest") == _ALREADY_HASHED

    # Digests are one-way: the downgrade keeps them rather than inventing
    # plaintext, and re-applying the upgrade does not hash a digest again.
    downgrade(_DB, _PARENT_REV)
    assert _token_for(db_path, "cs_plain") == hash_secret(_PLAINTEXT)
    upgrade(_DB, _REV)
    assert _token_for(db_path, "cs_plain") == hash_secret(_PLAINTEXT)
    assert _token_for(db_path, "cs_digest") == _ALREADY_HASHED
