"""Up/down test for the connect_sessions poll-token hashing migrations.

Pins revisions ``e1a2b3c4d5f6`` (hash existing values in place) and
``f2b3c4d5e6a7`` (rename ``poll_token`` -> ``poll_token_hash``) against a real
(SQLite) database: a plaintext token written before the upgrade is rewritten
to its SHA-256 digest (so a client still holding the plaintext keeps
verifying), the column and unique index are renamed, and a downgrade restores
the old column/index names while keeping the digest (it cannot be reversed).
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from jentic_one.migrations.run import downgrade, upgrade
from jentic_one.shared.crypto import hash_secret

_DB = "control"
_PARENT_REV = "z7b8c9d0e1f2"  # pragma: allowlist secret
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


def _token_for(db_path: Path, column: str, session_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        (value,) = conn.execute(
            f"SELECT {column} FROM connect_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    return str(value)


def test_poll_token_hash_migration_hashes_renames_and_downgrades(sqlite_stack: Path) -> None:
    db_path = sqlite_stack / f"{_DB}.db"

    upgrade(_DB, _PARENT_REV)
    _insert_session(db_path, "cs_plain", _PLAINTEXT)
    # A value that is already a digest must be left untouched (idempotency).
    _insert_session(db_path, "cs_digest", _ALREADY_HASHED)

    upgrade(_DB)
    columns = _columns(db_path)
    assert "poll_token_hash" in columns
    assert "poll_token" not in columns
    indexes = _indexes(db_path)
    assert "ix_connect_sessions_poll_token_hash" in indexes
    assert "ix_connect_sessions_poll_token" not in indexes
    assert _token_for(db_path, "poll_token_hash", "cs_plain") == hash_secret(_PLAINTEXT)
    assert _token_for(db_path, "poll_token_hash", "cs_digest") == _ALREADY_HASHED

    downgrade(_DB, _PARENT_REV)
    columns = _columns(db_path)
    assert "poll_token" in columns
    assert "poll_token_hash" not in columns
    indexes = _indexes(db_path)
    assert "ix_connect_sessions_poll_token" in indexes
    assert "ix_connect_sessions_poll_token_hash" not in indexes
    # Digests are one-way: the downgrade keeps them rather than inventing plaintext.
    assert _token_for(db_path, "poll_token", "cs_plain") == hash_secret(_PLAINTEXT)

    upgrade(_DB)
    assert _token_for(db_path, "poll_token_hash", "cs_plain") == hash_secret(_PLAINTEXT)
