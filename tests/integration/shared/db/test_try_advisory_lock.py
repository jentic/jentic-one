"""Integration tests for ``DatabaseSession.try_advisory_lock`` — non-blocking single-flight.

Used to single-flight work that any process may start (e.g. the catalog
update-notify sweep): concurrent attempts on Postgres must yield exactly one
winner, losers must not wait, and the lock must be free again once the winner
leaves. On SQLite it is a no-op that always acquires.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from jentic_one.shared.db.locks import advisory_lock_key
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_KEY = advisory_lock_key("tests.integration.try_advisory_lock")

_HOLDERS_SQL = text(
    "SELECT a.state FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid"
    " WHERE l.locktype = 'advisory' AND l.granted"
    " AND l.classid = :hi AND l.objid = :lo"
)
_UNSIGNED = _KEY & 0xFFFF_FFFF_FFFF_FFFF
_KEY_PARAMS = {"hi": _UNSIGNED >> 32, "lo": _UNSIGNED & 0xFFFFFFFF}


def _require_postgres(db: DatabaseSession) -> None:
    if db.engine.dialect.name != "postgresql":
        pytest.skip("advisory locks are Postgres-only (a no-op on SQLite)")


async def _holder_states(db: DatabaseSession) -> list[str]:
    async with db.session() as session:
        rows = (await session.execute(_HOLDERS_SQL, _KEY_PARAMS)).all()
    return [str(r.state) for r in rows]


async def test_concurrent_attempts_yield_exactly_one_winner(registry_db: DatabaseSession) -> None:
    _require_postgres(registry_db)
    outcomes: list[bool] = []
    all_in = asyncio.Event()
    entered = 0

    async def attempt() -> None:
        nonlocal entered
        async with registry_db.try_advisory_lock(_KEY) as acquired:
            outcomes.append(acquired)
            entered += 1
            if entered == 3:
                all_in.set()
            # Hold the block open until every attempt has decided, so the winner is
            # still holding while the others try.
            await asyncio.wait_for(all_in.wait(), timeout=5)

    await asyncio.gather(attempt(), attempt(), attempt())

    assert sorted(outcomes) == [False, False, True]
    assert await _holder_states(registry_db) == []


async def test_lock_is_held_without_an_open_transaction_and_released(
    registry_db: DatabaseSession,
) -> None:
    _require_postgres(registry_db)

    async with registry_db.try_advisory_lock(_KEY) as acquired:
        assert acquired is True
        # AUTOCOMMIT: plain "idle", never "idle in transaction".
        assert await _holder_states(registry_db) == ["idle"]

    async with registry_db.try_advisory_lock(_KEY) as again:
        assert again is True


async def test_loser_does_not_release_the_winners_lock(registry_db: DatabaseSession) -> None:
    _require_postgres(registry_db)

    async with registry_db.try_advisory_lock(_KEY) as winner:
        assert winner is True
        async with registry_db.try_advisory_lock(_KEY) as loser:
            assert loser is False
        # The loser exiting must leave the winner's lock in place.
        assert len(await _holder_states(registry_db)) == 1


async def test_sqlite_try_lock_always_acquires(registry_db: DatabaseSession) -> None:
    if registry_db.engine.dialect.name != "sqlite":
        pytest.skip("SQLite-only")
    async with registry_db.try_advisory_lock(_KEY) as a, registry_db.try_advisory_lock(_KEY) as b:
        assert a is True
        assert b is True
