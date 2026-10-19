"""Integration tests for ``DatabaseSession.try_advisory_lock`` — non-blocking single-flight.

Used to single-flight work that any process may start (e.g. the catalog
update-notify sweep): concurrent attempts on Postgres must yield exactly one
winner, losers must not wait, and the lock must be free again once the winner
leaves. On SQLite it is a no-op that always acquires.
"""

from __future__ import annotations

import asyncio

import pytest
import structlog
from sqlalchemy import event, text

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


async def test_cancelled_holder_releases_the_lock(registry_db: DatabaseSession) -> None:
    """Cancelling the task inside the block (e.g. shutdown) still frees the lock."""
    _require_postgres(registry_db)
    entered = asyncio.Event()

    async def hold() -> None:
        async with registry_db.try_advisory_lock(_KEY) as acquired:
            assert acquired is True
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(hold())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await _holder_states(registry_db) == []
    async with registry_db.try_advisory_lock(_KEY) as again:
        assert again is True


async def test_cancellation_during_the_unlock_discards_the_lock_connection(
    registry_db: DatabaseSession,
) -> None:
    """A cancel landing on ``pg_advisory_unlock`` itself never pools a held lock."""
    _require_postgres(registry_db)
    entered = asyncio.Event()
    holder: asyncio.Task[None] | None = None

    def _cancel_on_unlock(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        # Runs inside the holder task just before the unlock is sent; a running
        # task's cancel() takes effect at its next suspension, which is the
        # unlock's round trip, so the cancellation lands there.
        task = asyncio.current_task()
        if "pg_advisory_unlock" in statement and task is not None and task is holder:
            task.cancel()

    async def hold() -> None:
        async with registry_db.try_advisory_lock(_KEY) as acquired:
            assert acquired is True
            entered.set()

    sync_engine = registry_db.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _cancel_on_unlock)
    try:
        with structlog.testing.capture_logs() as logs:
            holder = asyncio.create_task(hold())
            with pytest.raises(asyncio.CancelledError):
                await holder
    finally:
        event.remove(sync_engine, "before_cursor_execute", _cancel_on_unlock)

    assert entered.is_set()
    # SQLAlchemy also invalidates a connection whose execute is cancelled; the
    # warning shows the release path itself handled the cancellation.
    failures = [entry for entry in logs if entry["event"] == "advisory_lock_release_failed"]
    assert [(f["key"], f["error"].startswith("CancelledError")) for f in failures] == [(_KEY, True)]
    # The discarded connection's backend exits asynchronously; wait for it.
    async with asyncio.timeout(5):
        while await _holder_states(registry_db):
            await asyncio.sleep(0.05)
    async with registry_db.try_advisory_lock(_KEY) as again:
        assert again is True
