"""Integration tests for ``advisory_xact_lock`` — transaction-scoped advisory locks.

On PostgreSQL, ``advisory_xact_lock`` takes ``pg_advisory_xact_lock`` bound to the
current transaction (either via a 64-bit ``key`` or ``hashtext(:name)``), blocking
concurrent contenders until the holder commits or rolls back. On SQLite, it is a
no-op that issues no SQL statements.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import event, text

from jentic_one.shared.db.locks import advisory_lock_key, advisory_xact_lock
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_HASHTEXT_NAME = "tests.integration.advisory_xact_lock.hashtext"
_INT_KEY = advisory_lock_key("tests.integration.advisory_xact_lock.key")


def _require_postgres(db: DatabaseSession) -> None:
    if db.engine.dialect.name != "postgresql":
        pytest.skip("advisory locks are Postgres-only (a no-op on SQLite)")


async def test_advisory_xact_lock_hashtext_blocks_second_transaction_until_commit(
    control_db: DatabaseSession,
) -> None:
    """Two transactions taking advisory_xact_lock(hashtext_name=...): second waits until commit."""
    _require_postgres(control_db)
    first_locked = asyncio.Event()
    release_first = asyncio.Event()
    events: list[str] = []

    async def first_tx() -> None:
        async with control_db.transaction() as session:
            await advisory_xact_lock(session, hashtext_name=_HASHTEXT_NAME)
            events.append("first:acquired")
            first_locked.set()
            await release_first.wait()
            events.append("first:committing")

    async def second_tx() -> None:
        await first_locked.wait()
        async with control_db.transaction() as session:
            await advisory_xact_lock(session, hashtext_name=_HASHTEXT_NAME)
            events.append("second:acquired")

    t1 = asyncio.create_task(first_tx())
    t2 = asyncio.create_task(second_tx())

    # Wait until t2 is parked on the advisory lock in pg_locks.
    async with asyncio.timeout(5):
        while True:
            async with control_db.session() as obs:
                waiting = (
                    await obs.execute(
                        text(
                            "SELECT count(*) FROM pg_locks"
                            " WHERE locktype = 'advisory' AND NOT granted"
                            " AND database = (SELECT oid FROM pg_database"
                            " WHERE datname = current_database())"
                            " AND objsubid = 1"
                            " AND ((classid::int8 << 32) | objid::int8)"
                            " = hashtext(:name)::int8"
                        ),
                        {"name": _HASHTEXT_NAME},
                    )
                ).scalar_one()
            if waiting:
                break
            await asyncio.sleep(0.02)

    assert events == ["first:acquired"]
    release_first.set()
    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=5)
    assert events == ["first:acquired", "first:committing", "second:acquired"]


async def test_advisory_xact_lock_hashtext_key_matches_postgres_hashtext(
    control_db: DatabaseSession,
) -> None:
    """The lock recorded in pg_locks for hashtext_name equals SELECT hashtext(:name)."""
    _require_postgres(control_db)

    async with control_db.transaction() as session:
        await advisory_xact_lock(session, hashtext_name="x")
        expected_key = (await session.execute(text("SELECT hashtext('x')::int8"))).scalar_one()
        granted_key = (
            await session.execute(
                text(
                    "SELECT ((classid::int8 << 32) | objid::int8)"
                    " FROM pg_locks"
                    " WHERE pid = pg_backend_pid()"
                    " AND locktype = 'advisory' AND granted AND objsubid = 1"
                )
            )
        ).scalar_one()
        assert int(granted_key) == int(expected_key)


async def test_advisory_xact_lock_int_key_blocks_second_transaction(
    control_db: DatabaseSession,
) -> None:
    """Two transactions taking advisory_xact_lock(key=...): the second waits until commit."""
    _require_postgres(control_db)
    unsigned = _INT_KEY & 0xFFFF_FFFF_FFFF_FFFF
    hi = unsigned >> 32
    lo = unsigned & 0xFFFFFFFF

    first_locked = asyncio.Event()
    release_first = asyncio.Event()
    events: list[str] = []

    async def first_tx() -> None:
        async with control_db.transaction() as session:
            await advisory_xact_lock(session, key=_INT_KEY)
            events.append("first:acquired")
            first_locked.set()
            await release_first.wait()
            events.append("first:committing")

    async def second_tx() -> None:
        await first_locked.wait()
        async with control_db.transaction() as session:
            await advisory_xact_lock(session, key=_INT_KEY)
            events.append("second:acquired")

    t1 = asyncio.create_task(first_tx())
    t2 = asyncio.create_task(second_tx())

    async with asyncio.timeout(5):
        while True:
            async with control_db.session() as obs:
                waiting = (
                    await obs.execute(
                        text(
                            "SELECT count(*) FROM pg_locks"
                            " WHERE locktype = 'advisory' AND NOT granted"
                            " AND classid = :hi AND objid = :lo AND objsubid = 1"
                        ),
                        {"hi": hi, "lo": lo},
                    )
                ).scalar_one()
            if waiting:
                break
            await asyncio.sleep(0.02)

    assert events == ["first:acquired"]
    release_first.set()
    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=5)
    assert events == ["first:acquired", "first:committing", "second:acquired"]


async def test_advisory_xact_lock_sqlite_issues_no_statements(
    control_db: DatabaseSession,
) -> None:
    """On SQLite, advisory_xact_lock is a no-op and emits zero SQL statements."""
    if control_db.engine.dialect.name != "sqlite":
        pytest.skip("SQLite-only")

    async with control_db.transaction() as session:
        statements: list[str] = []

        def _record(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
            statements.append(statement)

        sync_engine = control_db.engine.sync_engine
        event.listen(sync_engine, "before_cursor_execute", _record)
        try:
            await advisory_xact_lock(session, hashtext_name="x")
            await advisory_xact_lock(session, key=_INT_KEY)
        finally:
            event.remove(sync_engine, "before_cursor_execute", _record)

        assert statements == []
