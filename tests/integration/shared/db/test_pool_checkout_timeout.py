"""Integration tests for ``QueuePool`` checkout timeout mapping against real backends (#1304).

Exercises real constrained ``QueuePool`` instances on PostgreSQL and SQLite to
verify that pool-checkout exhaustion translates ``sqlalchemy.exc.TimeoutError``
to ``DatabaseUnavailableError`` across ``session()``, ``read_session()``,
``read_snapshot()``, ``transaction()``, ``run_in_transaction()``,
``advisory_lock()``, and ``try_advisory_lock()``, without misclassifying
PostgreSQL statement timeouts (SQLSTATE ``57014``) or caller ``TimeoutError``
exceptions.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SATimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.backends import (
    configure_idle_gated_pre_ping,
    configure_sqlite_pragmas,
    enable_sqlite_manual_begin,
)
from jentic_one.shared.db.errors import DatabaseUnavailableError
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


@pytest.fixture()
async def constrained_db(
    integration_config: AppConfig,
) -> AsyncGenerator[DatabaseSession, None]:
    """A dedicated admin ``DatabaseSession`` backed by a 1-slot ``QueuePool`` (0.05s timeout)."""
    cfg = integration_config.databases.admin.model_copy(update={"pool_max": 1})
    db = DatabaseSession(cfg)
    await db.connect()
    await db.engine.dispose()

    url = db.backend.make_url(cfg)
    kwargs = db.backend.engine_kwargs(cfg)
    kwargs["poolclass"] = AsyncAdaptedQueuePool
    kwargs["pool_size"] = 1
    kwargs["max_overflow"] = 0
    kwargs["pool_timeout"] = 0.05
    engine = create_async_engine(url, **kwargs)
    if db.backend.dialect_name == "sqlite":
        configure_sqlite_pragmas(
            engine, journal_mode=cfg.journal_mode, busy_timeout_ms=cfg.busy_timeout_ms
        )
        enable_sqlite_manual_begin(engine)
    else:
        configure_idle_gated_pre_ping(engine, idle_s=cfg.pool_pre_ping_idle_s)
    db._engine = engine
    db.session_factory.configure(bind=engine)
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["session", "read_session", "read_snapshot", "transaction"])
async def test_exhausted_pool_maps_checkout_timeout_to_unavailable(
    constrained_db: DatabaseSession, scope: str
) -> None:
    """A real ``QueuePool`` checkout timeout maps to ``DatabaseUnavailableError``."""
    db = constrained_db
    async with db.session() as holder:
        await holder.execute(text("SELECT 1"))

        with pytest.raises(DatabaseUnavailableError) as caught:
            if scope == "session":
                async with db.session() as sess:
                    await sess.execute(text("SELECT 1"))
            elif scope == "read_session":
                async with db.read_session() as sess:
                    await sess.execute(text("SELECT 1"))
            elif scope == "read_snapshot":
                async with db.read_snapshot() as sess:
                    await sess.execute(text("SELECT 1"))
            else:
                async with db.transaction() as sess:
                    await sess.execute(text("SELECT 1"))

    assert isinstance(caught.value.__cause__, SATimeoutError)
    assert "QueuePool" not in str(caught.value)
    assert "overflow" not in str(caught.value)

    async with db.session() as recovered:
        assert (await recovered.execute(text("SELECT 1"))).scalar_one() == 1


@pytest.mark.asyncio
async def test_advisory_locks_map_pool_checkout_timeout_on_postgres(
    constrained_db: DatabaseSession,
) -> None:
    """Advisory-lock helpers map pool checkout timeouts to ``DatabaseUnavailableError``."""
    if constrained_db.backend.dialect_name != "postgres":
        pytest.skip("Postgres-only: advisory locks are no-ops on SQLite")

    async with constrained_db.session() as holder:
        await holder.execute(text("SELECT 1"))

        with pytest.raises(DatabaseUnavailableError) as lock_exc:
            async with constrained_db.advisory_lock(991304):
                pass
        assert isinstance(lock_exc.value.__cause__, SATimeoutError)

        with pytest.raises(DatabaseUnavailableError) as try_exc:
            async with constrained_db.try_advisory_lock(991305):
                pass
        assert isinstance(try_exc.value.__cause__, SATimeoutError)


@pytest.mark.asyncio
async def test_postgres_statement_timeout_and_caller_timeout_are_not_mapped_to_unavailable(
    constrained_db: DatabaseSession,
) -> None:
    """Postgres statement timeout (57014) and caller ``TimeoutError`` propagate
    without mapping to ``DatabaseUnavailableError``."""
    # Caller asyncio.timeout inside session() and transaction() must stay TimeoutError.
    with pytest.raises(TimeoutError):
        async with constrained_db.session() as sess:
            await sess.execute(text("SELECT 1"))
            async with asyncio.timeout(0.01):
                await asyncio.sleep(1.0)

    with pytest.raises(TimeoutError):
        async with constrained_db.transaction() as sess:
            await sess.execute(text("SELECT 1"))
            async with asyncio.timeout(0.01):
                await asyncio.sleep(1.0)

    if constrained_db.backend.dialect_name != "postgres":
        return

    # Postgres 57014 QueryCanceledError in both session() and transaction() must remain DBAPIError.
    with pytest.raises(DBAPIError) as sess_stmt_exc:
        async with constrained_db.session() as sess:
            await sess.execute(text("SET LOCAL statement_timeout = '40ms'"))
            await sess.execute(text("SELECT pg_sleep(2)"))
    assert not isinstance(sess_stmt_exc.value, DatabaseUnavailableError)

    async def _slow_tx(sess: AsyncSession) -> None:
        await sess.execute(text("SET LOCAL statement_timeout = '40ms'"))
        await sess.execute(text("SELECT pg_sleep(2)"))

    with pytest.raises(DBAPIError) as tx_stmt_exc:
        await constrained_db.run_in_transaction(_slow_tx, retries=1, backoff_s=0)
    assert not isinstance(tx_stmt_exc.value, DatabaseUnavailableError)
