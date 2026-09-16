"""T-11 — the theme-8 Phase 1 admin migration, up and down, against real SQLite.

``c0d1e2f3a4b5`` adds the stamp pair, the acknowledgement sentinel table, and
the ``uq_agent_credentials_api_key_hash`` partial unique index. Pinned here
against a real database (no DB mocking): the downgrade must remove exactly
what the upgrade added, and the partial index must enforce digest uniqueness
natively — NULL-exempt — because it is the migration job's double-mint
backstop (H-A x F6). The Postgres twin of the enforcement probe lives in
``tests/integration/control/test_service_account_migration.py``
(``test_concurrent_double_run_mints_no_duplicate_digest_row``), which CI runs
on both dialects.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jentic_one.migrations import run as run_mod

_DB = "admin"
_REVISION = "c0d1e2f3a4b5"


@pytest.fixture
def sqlite_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point config at fresh, empty per-database SQLite files."""
    cfg = tmp_path / "jentic-one.yaml"
    lines = ["databases:"]
    for name in ("admin", "control", "registry"):
        lines += [f"  {name}:", "    backend: sqlite", f"    path: {tmp_path / f'{name}.db'}"]
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("JENTIC_CONFIG_FILE", str(cfg))
    return tmp_path


def _connect(stack: Path) -> sqlite3.Connection:
    return sqlite3.connect(stack / f"{_DB}.db")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {name for (name,) in rows}


def _indexes(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    return {name for (name,) in rows}


def test_upgrade_adds_stamp_sentinel_and_partial_index(sqlite_stack: Path) -> None:
    run_mod.upgrade(_DB)
    with _connect(sqlite_stack) as conn:
        assert {"migrated_to_actor_id", "migrated_at"} <= _columns(conn, "service_accounts")
        assert "service_account_migration_acks" in _tables(conn)
        assert {
            "acknowledged_at",
            "unstamped_count",
            "grant_twin_missing_count",
            "unrevoked_token_count",
            "digest_mismatch_count",
            "post_stamp_mutation_count",
            "report_finding_count",
            "tool_version",
        } <= _columns(conn, "service_account_migration_acks")
        assert "uq_agent_credentials_api_key_hash" in _indexes(conn)


def test_downgrade_removes_exactly_what_upgrade_added(sqlite_stack: Path) -> None:
    run_mod.upgrade(_DB)
    run_mod.downgrade(_DB, f"{_REVISION}-1")
    with _connect(sqlite_stack) as conn:
        assert "migrated_to_actor_id" not in _columns(conn, "service_accounts")
        assert "migrated_at" not in _columns(conn, "service_accounts")
        assert "service_account_migration_acks" not in _tables(conn)
        assert "uq_agent_credentials_api_key_hash" not in _indexes(conn)
    # And back up: the pair round-trips.
    run_mod.upgrade(_DB)
    with _connect(sqlite_stack) as conn:
        assert "service_account_migration_acks" in _tables(conn)


def test_partial_index_enforces_digest_uniqueness_null_exempt(sqlite_stack: Path) -> None:
    """The double-mint backstop, probed directly: SQLite enforces the partial
    unique index natively; NULL digests stay exempt."""
    run_mod.upgrade(_DB)
    with _connect(sqlite_stack) as conn:
        conn.execute(
            "INSERT INTO users (id, email, first_name, last_name)"
            " VALUES ('usr_t11', 't11@test.local', 'T', 'Eleven')"
        )
        insert = (
            "INSERT INTO agents (id, name, owner_id, registered_by, status, created_by)"
            " VALUES (?, ?, 'usr_t11', 'system:test', 'active', 'system:test')"
        )
        cred = (
            "INSERT INTO agent_credentials (id, agent_id, api_key_hash, created_by)"
            " VALUES (?, ?, ?, 'system:test')"
        )
        conn.execute(insert, ("agnt_t11_a", "a"))
        conn.execute(insert, ("agnt_t11_b", "b"))
        conn.execute(insert, ("agnt_t11_c", "c"))
        conn.execute(insert, ("agnt_t11_d", "d"))
        conn.execute(cred, ("agc_t11_a", "agnt_t11_a", "shared-digest"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(cred, ("agc_t11_b", "agnt_t11_b", "shared-digest"))
        # NULL-exempt: any number of NULL digests coexist.
        conn.execute(cred, ("agc_t11_c", "agnt_t11_c", None))
        conn.execute(cred, ("agc_t11_d", "agnt_t11_d", None))
