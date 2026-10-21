"""Up/down test for the external_identities issuer column and unique constraint migration.

Pins revision ``e5c9f3a7b802`` against a SQLite database:
- upgrade adds ``external_identities.issuer``, backfills ``'https://accounts.google.com'``
  for ``provider = 'google'`` and ``provider`` for other rows, and replaces
  ``uq_ext_id_provider_subject`` with ``uq_ext_id_issuer_subject`` on
  ``(issuer, external_subject)``;
- two rows with the same ``provider`` and ``external_subject`` but distinct ``issuer``
  values are permitted after upgrade;
- downgrade restores ``uq_ext_id_provider_subject`` and drops ``issuer``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jentic_one.migrations.run import downgrade, upgrade

_DB = "admin"
_REV = "e5c9f3a7b802"  # pragma: allowlist secret
_PARENT_REV = "d4b8e2f6a901"  # pragma: allowlist secret


def _columns(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(external_identities)")}


def _insert_user(db_path: Path, user_id: str, email: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO users (id, email, first_name, last_name, active,"
            " auth_provider, must_change_password, invite_state, created_at, updated_at)"
            " VALUES (?, ?, 'A', 'B', 1, 'oidc', 0, 'redeemed',"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
            (user_id, email),
        )


def test_external_identities_issuer_migration_roundtrip(sqlite_stack: Path) -> None:
    db_path = sqlite_stack / f"{_DB}.db"

    upgrade(_DB, _PARENT_REV)
    assert "issuer" not in _columns(db_path)

    _insert_user(db_path, "usr_1", "u1@example.com")
    _insert_user(db_path, "usr_2", "u2@example.com")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO external_identities (id, provider, external_subject, user_id,"
            " created_at, updated_at)"
            " VALUES ('eid_1', 'google', 'sub-google-1', 'usr_1',"
            " '2026-01-02 00:00:00', '2026-01-02 00:00:00')"
        )
        # Older row whose provider value collides with the backfilled Google issuer
        # URL for the same external_subject; upgrade deduplicates to the newest row.
        conn.execute(
            "INSERT INTO external_identities (id, provider, external_subject, user_id,"
            " created_at, updated_at)"
            " VALUES ('eid_1_old', 'https://accounts.google.com', 'sub-google-1', 'usr_2',"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        conn.execute(
            "INSERT INTO external_identities (id, provider, external_subject, user_id,"
            " created_at, updated_at)"
            " VALUES ('eid_2', 'oidc', 'sub-shared', 'usr_1',"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )

    upgrade(_DB, _REV)
    assert "issuer" in _columns(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = dict(conn.execute("SELECT id, issuer FROM external_identities").fetchall())
        assert rows["eid_1"] == "https://accounts.google.com"
        assert "eid_1_old" not in rows
        assert rows["eid_2"] == "oidc"

        # After upgrade, another row with the same provider='oidc' and external_subject='sub-shared'
        # but a different issuer succeeds.
        conn.execute(
            "INSERT INTO external_identities (id, provider, issuer, external_subject, user_id,"
            " created_at, updated_at)"
            " VALUES ('eid_3', 'oidc', 'https://other-idp.example.com', 'sub-shared', 'usr_2',"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )

        # Duplicate (issuer, external_subject) is rejected.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO external_identities (id, provider, issuer, external_subject, user_id,"
                " created_at, updated_at)"
                " VALUES ('eid_dup', 'oidc', 'https://other-idp.example.com', 'sub-shared',"
                " 'usr_1', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )

        # Remove eid_3 before downgrading so (provider, external_subject) is unique again.
        conn.execute("DELETE FROM external_identities WHERE id = 'eid_3'")

    downgrade(_DB, _PARENT_REV)
    assert "issuer" not in _columns(db_path)

    upgrade(_DB, _REV)
    assert "issuer" in _columns(db_path)
