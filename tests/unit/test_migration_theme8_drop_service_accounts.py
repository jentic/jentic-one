"""The theme-8 Phase-4 admin drop migration, against real SQLite.

``e2f3a4b5c6d7`` drops ``service_accounts`` and ``service_account_credentials``
behind a guard-and-raise gate. Pinned here against a real database (no DB
mocking): the fresh-install path, every refusal arm, the acknowledged +
swept happy path with the retired-scope sweep, and the empty-table
downgrade. The Postgres twin lives in
``tests/integration/admin/test_phase4_drop_service_accounts.py``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from jentic_one.migrations import run as run_mod

_DB = "admin"
_REVISION = "e2f3a4b5c6d7"  # pragma: allowlist secret
_PRE_DROP = "d1e2f3a4b5c6"  # pragma: allowlist secret

_STAMPED_AT = "2026-09-01 10:00:00.000000"
_ACKED_AT = "2026-09-02 10:00:00.000000"
_PAST = "2020-01-01 00:00:00.000000"
_FUTURE = "2999-01-01 00:00:00.000000"


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


def _tables(stack: Path) -> set[str]:
    with _connect(stack) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {name for (name,) in rows}


_Stmt = tuple[str, tuple[object, ...]]


def _seed(stack: Path, *statements: _Stmt) -> None:
    with _connect(stack) as conn:
        for sql, params in statements:
            conn.execute(sql, params)


_USER = (
    "INSERT INTO users (id, email, first_name, last_name) VALUES (?, ?, 'P', 'Four')",
    ("usr_p4", "p4@test.local"),
)
_AGENT = (
    "INSERT INTO agents (id, name, owner_id, registered_by, status, created_by)"
    " VALUES (?, ?, 'usr_p4', 'system:test', 'active', 'system:test')",
    ("agnt_p4", "successor"),
)
_AGENT_CRED = (
    "INSERT INTO agent_credentials (id, agent_id, api_key_hash, created_by)"
    " VALUES ('agc_p4', 'agnt_p4', 'digest-1', 'system:test')",
    (),
)


def _sa(*, status: str = "archived", stamp: str | None = "agnt_p4") -> _Stmt:
    return (
        "INSERT INTO service_accounts (id, name, owner_id, registered_by, status,"
        " migrated_to_actor_id, migrated_at, created_by)"
        " VALUES ('sva_p4', 'legacy', 'usr_p4', 'usr_p4', ?, ?, ?, 'system:test')",
        (status, stamp, _STAMPED_AT if stamp else None),
    )


def _sac(digest: str | None) -> _Stmt:
    return (
        "INSERT INTO service_account_credentials (id, service_account_id, api_key_hash,"
        " created_by) VALUES ('sac_p4', 'sva_p4', ?, 'system:test')",
        (digest,),
    )


def _ack(acknowledged_at: str = _ACKED_AT, *, unstamped: int = 0) -> _Stmt:
    return (
        "INSERT INTO service_account_migration_acks (id, acknowledged_at, unstamped_count,"
        " grant_twin_missing_count, unrevoked_token_count, digest_mismatch_count,"
        " post_stamp_mutation_count, report_finding_count, tool_version)"
        " VALUES ('smak_' || hex(randomblob(6)), ?, ?, 0, 0, 0, 0, 0, 'test')",
        (acknowledged_at, unstamped),
    )


def _access_token(*, revoked_at: str | None, expires_at: str, scopes: list[str]) -> _Stmt:
    return (
        "INSERT INTO access_tokens (id, token_hash, actor_id, actor_type, scopes,"
        " token_family_id, expires_at, revoked_at)"
        " VALUES ('at_p4_' || hex(randomblob(4)), hex(randomblob(8)), 'sva_p4',"
        " 'service_account', ?, 'fam_p4', ?, ?)",
        (json.dumps(scopes), expires_at, revoked_at),
    )


def _migrated_and_swept(stack: Path) -> None:
    _seed(stack, _USER, _AGENT, _AGENT_CRED, _sa(), _sac(None))


def test_fresh_install_drops_without_an_ack(sqlite_stack: Path) -> None:
    run_mod.upgrade(_DB)
    tables = _tables(sqlite_stack)
    assert "service_accounts" not in tables
    assert "service_account_credentials" not in tables
    # The upgrade evidence stays.
    assert "service_account_migration_acks" in tables


def _refuses(stack: Path, match: str) -> None:
    with pytest.raises(RuntimeError, match=match):
        run_mod.upgrade(_DB, _REVISION)
    assert "service_accounts" in _tables(stack)


Seeder = Callable[[Path], None]


@pytest.mark.parametrize(
    ("seed", "match"),
    [
        pytest.param(
            lambda s: _seed(s, _USER, _AGENT, _AGENT_CRED, _sa(), _sac(None)),
            "no acknowledgement row exists",
            id="no-ack",
        ),
        pytest.param(
            lambda s: _seed(s, _USER, _sa(stamp=None), _ack()),
            "1 unstamped service account",
            id="unstamped",
        ),
        pytest.param(
            lambda s: _seed(
                s, _USER, _AGENT, _AGENT_CRED, _sa(status="active"), _sac(None), _ack()
            ),
            "1 stamped but unswept service account",
            id="unswept-status",
        ),
        pytest.param(
            lambda s: _seed(s, _USER, _AGENT, _AGENT_CRED, _sa(), _sac("other-digest"), _ack()),
            "1 successor digest mismatch",
            id="digest-mismatch",
        ),
        pytest.param(
            lambda s: _seed(s, _USER, _AGENT, _AGENT_CRED, _sa(), _sac(None), _ack(_PAST)),
            "stamped after the latest acknowledgement",
            id="stale-ack",
        ),
        pytest.param(
            lambda s: _seed(s, _USER, _AGENT, _AGENT_CRED, _sa(), _sac(None), _ack(unstamped=1)),
            "records 1 verification failure",
            id="failed-ack",
        ),
        pytest.param(
            lambda s: _seed(
                s,
                (
                    "INSERT INTO agent_credential_bindings (id, agent_id, credential_id)"
                    " VALUES ('acb_p4', 'sva_p4', 'cred_p4')",
                    (),
                ),
            ),
            "1 sva_-keyed credential binding",
            id="fresh-with-binding",
        ),
        pytest.param(
            lambda s: _seed(
                s,
                _access_token(revoked_at=None, expires_at=_FUTURE, scopes=["agents:read"]),
            ),
            "1 live service-account session token",
            id="fresh-with-live-token",
        ),
    ],
)
def test_gate_refuses(sqlite_stack: Path, seed: Seeder, match: str) -> None:
    run_mod.upgrade(_DB, _PRE_DROP)
    seed(sqlite_stack)
    _refuses(sqlite_stack, match)


def test_latest_ack_wins_over_an_older_one(sqlite_stack: Path) -> None:
    """A newer ack supersedes a stale one (the rehearsal's 'read the latest row')."""
    run_mod.upgrade(_DB, _PRE_DROP)
    _migrated_and_swept(sqlite_stack)
    _seed(sqlite_stack, _ack(_PAST))
    _refuses(sqlite_stack, "stamped after the latest acknowledgement")
    _seed(sqlite_stack, _ack(_ACKED_AT))
    run_mod.upgrade(_DB, _REVISION)
    assert "service_accounts" not in _tables(sqlite_stack)


def test_acknowledged_and_swept_drops_and_sweeps_retired_scopes(sqlite_stack: Path) -> None:
    run_mod.upgrade(_DB, _PRE_DROP)
    _migrated_and_swept(sqlite_stack)
    _seed(
        sqlite_stack,
        _ack(),
        # A revoked SA token is dead weight, not a blocker.
        _access_token(
            revoked_at=_STAMPED_AT,
            expires_at=_FUTURE,
            scopes=["service-accounts:read", "agents:read"],
        ),
        (
            "INSERT INTO actor_scope_grants (id, actor_id, actor_type, scope)"
            " VALUES ('asg_p4_a', 'agnt_p4', 'agent', 'owner:service-accounts:read'),"
            "        ('asg_p4_b', 'agnt_p4', 'agent', 'agents:read')",
            (),
        ),
    )
    run_mod.upgrade(_DB, _REVISION)

    assert "service_accounts" not in _tables(sqlite_stack)
    with _connect(sqlite_stack) as conn:
        grants = {r[0] for r in conn.execute("SELECT scope FROM actor_scope_grants")}
        (token_scopes,) = conn.execute(
            "SELECT scopes FROM access_tokens WHERE actor_id = 'sva_p4'"
        ).fetchone()
    assert grants == {"agents:read"}
    assert json.loads(token_scopes) == ["agents:read"]


def test_downgrade_recreates_empty_tables(sqlite_stack: Path) -> None:
    run_mod.upgrade(_DB)
    run_mod.downgrade(_DB, _PRE_DROP)
    tables = _tables(sqlite_stack)
    assert {"service_accounts", "service_account_credentials"} <= tables
    with _connect(sqlite_stack) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(service_accounts)")}
        (count,) = conn.execute("SELECT count(*) FROM service_accounts").fetchone()
    assert {"migrated_to_actor_id", "migrated_at"} <= columns
    assert count == 0
    # And back up through the fresh-install path.
    run_mod.upgrade(_DB)
    assert "service_accounts" not in _tables(sqlite_stack)
