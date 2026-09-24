"""Integration tests for ``DatabaseSession.advisory_lock`` — the run lock.

The upgrade steps and the toolkit-key retirement serialise on it across
processes (boot tasks on every replica, the migration runner, the CLI). On
Postgres it must hold a real session-level lock *without* leaving its
connection idle in a transaction (managed Postgres kills those), and a lost
lock connection must not mask the guarded block's own result. On SQLite it is
a no-op.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_KEY = 0x6A6F_5445_5354  # "joTEST" — distinct from every production key

_HOLDER_SQL = text(
    "SELECT a.pid, a.state FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid"
    " WHERE l.locktype = 'advisory' AND l.granted"
    " AND l.classid = :hi AND l.objid = :lo"
)
_KEY_PARAMS = {"hi": _KEY >> 32, "lo": _KEY & 0xFFFFFFFF}


def _require_postgres(db: DatabaseSession) -> None:
    if db.engine.dialect.name != "postgresql":
        pytest.skip("advisory locks are Postgres-only (a no-op on SQLite)")


async def _holders(db: DatabaseSession) -> list[tuple[int, str]]:
    async with db.session() as session:
        rows = (await session.execute(_HOLDER_SQL, _KEY_PARAMS)).all()
    return [(int(r.pid), str(r.state)) for r in rows]


async def test_lock_is_held_without_an_open_transaction(control_db: DatabaseSession) -> None:
    _require_postgres(control_db)

    async with control_db.advisory_lock(_KEY):
        holders = await _holders(control_db)
        assert len(holders) == 1
        # AUTOCOMMIT: the lock connection is plain "idle", never "idle in
        # transaction" (which idle_in_transaction_session_timeout would kill).
        assert holders[0][1] == "idle"

    assert await _holders(control_db) == []


async def test_lock_serialises_concurrent_holders(control_db: DatabaseSession) -> None:
    _require_postgres(control_db)
    events: list[str] = []

    async def worker(name: str) -> None:
        async with control_db.advisory_lock(_KEY):
            events.append(f"{name}:in")
            await asyncio.sleep(0.2)
            events.append(f"{name}:out")

    await asyncio.gather(worker("a"), worker("b"))

    # Never interleaved: each holder leaves before the next enters.
    assert events[0].endswith(":in") and events[1].endswith(":out")
    assert events[0].split(":")[0] == events[1].split(":")[0]


async def test_lost_lock_connection_does_not_mask_the_block(
    control_db: DatabaseSession,
) -> None:
    """A killed lock connection (timeout, failover) logs, it does not raise."""
    _require_postgres(control_db)
    result: list[str] = []

    async with control_db.advisory_lock(_KEY):
        [(pid, _state)] = await _holders(control_db)
        async with control_db.session() as session:
            await session.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        result.append("block completed")

    assert result == ["block completed"]
    # The server released the lock with the connection; it is free again.
    async with control_db.advisory_lock(_KEY):
        assert len(await _holders(control_db)) == 1


async def test_sqlite_lock_is_a_no_op(control_db: DatabaseSession) -> None:
    if control_db.engine.dialect.name != "sqlite":
        pytest.skip("SQLite-only")
    async with control_db.advisory_lock(_KEY), control_db.advisory_lock(_KEY):
        # Re-entrant by construction: nothing is acquired.
        pass
