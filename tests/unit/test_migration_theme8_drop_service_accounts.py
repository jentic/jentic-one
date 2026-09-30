"""The theme-8 Phase-4 admin drop migration, against real SQLite.

``e2f3a4b5c6d7`` drops ``service_accounts``, ``service_account_credentials``
and the retired ``service_account_migration_acks`` behind a guard-and-raise
gate (every SA stamped and swept — the runner's pre-drop retirement does
that). Pinned here against a real database (no DB mocking): the fresh-install
path, every refusal arm, the swept happy path with the orphan cleanup and the
retired-scope sweep, and the irreversible (raising) downgrade. The Postgres twin lives in
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


def test_fresh_install_drops(sqlite_stack: Path) -> None:
    run_mod.upgrade(_DB)
    tables = _tables(sqlite_stack)
    assert not tables & {
        "service_accounts",
        "service_account_credentials",
        "service_account_migration_acks",
    }


def _refuses(stack: Path, match: str) -> None:
    with pytest.raises(RuntimeError, match=match) as excinfo:
        run_mod.upgrade(_DB, _REVISION)
    assert "Nothing was dropped" in str(excinfo.value)
    assert "sva_p4" in str(excinfo.value), "the refusal names the rows"
    assert "service_accounts" in _tables(stack)


Seeder = Callable[[Path], None]

_SA_GRANT = (
    "INSERT INTO actor_scope_grants (id, actor_id, actor_type, scope)"
    " VALUES ('asg_p4_sa', 'sva_p4', 'service_account', 'agents:read')",
    (),
)


@pytest.mark.parametrize(
    ("seed", "match"),
    [
        pytest.param(
            lambda s: _seed(s, _USER, _sa(stamp=None)),
            "1 unmigrated service account",
            id="unstamped",
        ),
        pytest.param(
            lambda s: _seed(s, _USER, _AGENT, _AGENT_CRED, _sa(status="active"), _sac(None)),
            "1 migrated but unswept service account",
            id="unswept-status",
        ),
        pytest.param(
            lambda s: _seed(s, _USER, _AGENT, _AGENT_CRED, _sa(), _sac("digest-1")),
            "1 migrated but unswept service account",
            id="unswept-digest",
        ),
        pytest.param(
            lambda s: _seed(s, _USER, _AGENT, _AGENT_CRED, _sa(), _sac(None), _SA_GRANT),
            "1 migrated but unswept service account",
            id="unswept-grant",
        ),
    ],
)
def test_gate_refuses(sqlite_stack: Path, seed: Seeder, match: str) -> None:
    run_mod.upgrade(_DB, _PRE_DROP)
    seed(sqlite_stack)
    _refuses(sqlite_stack, match)


def test_refusal_is_safe_to_rerun(sqlite_stack: Path) -> None:
    """A refused revision rolls back whole; fixing the row lets the re-run drop."""
    run_mod.upgrade(_DB, _PRE_DROP)
    _seed(sqlite_stack, _USER, _AGENT, _AGENT_CRED, _sa(status="active"), _sac(None))
    _refuses(sqlite_stack, "unswept")
    _seed(sqlite_stack, ("UPDATE service_accounts SET status = 'archived'", ()))
    run_mod.upgrade(_DB, _REVISION)
    assert "service_accounts" not in _tables(sqlite_stack)


def test_swept_drops_cleans_orphans_and_sweeps_retired_scopes(sqlite_stack: Path) -> None:
    run_mod.upgrade(_DB, _PRE_DROP)
    _migrated_and_swept(sqlite_stack)
    _seed(
        sqlite_stack,
        # Session rows of the retired actor: all dead weight once the tables go.
        _access_token(
            revoked_at=_STAMPED_AT,
            expires_at=_FUTURE,
            scopes=["service-accounts:read", "agents:read"],
        ),
        _access_token(revoked_at=None, expires_at=_FUTURE, scopes=["agents:read"]),
        # Orphans: an sva_ id with no SA row (a hand-deleted service account).
        (
            "INSERT INTO agent_credential_bindings (id, agent_id, credential_id)"
            " VALUES ('acb_p4', 'sva_gone', 'cred_p4')",
            (),
        ),
        (
            "INSERT INTO actor_scope_grants (id, actor_id, actor_type, scope)"
            " VALUES ('asg_p4_a', 'agnt_p4', 'agent', 'owner:service-accounts:read'),"
            "        ('asg_p4_b', 'agnt_p4', 'agent', 'agents:read'),"
            "        ('asg_p4_c', 'sva_gone', 'agent', 'agents:read')",
            (),
        ),
    )
    run_mod.upgrade(_DB, _REVISION)

    assert "service_accounts" not in _tables(sqlite_stack)
    with _connect(sqlite_stack) as conn:
        grants = {
            (r[0], r[1]) for r in conn.execute("SELECT actor_id, scope FROM actor_scope_grants")
        }
        (sa_tokens,) = conn.execute(
            "SELECT count(*) FROM access_tokens WHERE actor_id = 'sva_p4'"
        ).fetchone()
        (bindings,) = conn.execute("SELECT count(*) FROM agent_credential_bindings").fetchone()
    assert grants == {("agnt_p4", "agents:read")}
    assert sa_tokens == 0
    assert bindings == 0


def _admin_revision(stack: Path) -> str:
    with _connect(stack) as conn:
        return str(conn.execute("SELECT version_num FROM alembic_version").fetchone()[0])


def test_downgrade_is_irreversible_and_changes_nothing(sqlite_stack: Path) -> None:
    """The drop never recreates empty tables: its downgrade raises, pointing at
    the pre-upgrade snapshot, before any DDL."""
    run_mod.upgrade(_DB)
    with pytest.raises(RuntimeError, match="e2f3a4b5c6d7 is irreversible") as excinfo:
        run_mod.downgrade(_DB, _PRE_DROP)
    assert "restore the admin database from the pre-upgrade snapshot" in str(excinfo.value)
    assert _admin_revision(sqlite_stack) == _REVISION
    assert not _tables(sqlite_stack) & {
        "service_accounts",
        "service_account_credentials",
        "service_account_migration_acks",
    }
