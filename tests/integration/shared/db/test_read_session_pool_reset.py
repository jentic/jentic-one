"""``DatabaseSession.read_session`` against a real pooled Postgres connection (#1823).

A read session switches its pooled connection to ``AUTOCOMMIT``. SQLAlchemy
restores the default level when the connection goes back to the pool; if it did
not, every later ``transaction()`` on that connection would run in autocommit,
``ROLLBACK`` would undo nothing, and multi-statement writes would stop being
atomic. These tests pin that reset on a single-connection pool so the next
session is guaranteed to reuse the read session's connection.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


@pytest.fixture()
async def single_connection_db(
    integration_config: AppConfig,
) -> AsyncGenerator[DatabaseSession, None]:
    """A dedicated admin ``DatabaseSession`` whose pool holds one connection."""
    config = integration_config.databases.admin
    if config.backend != "postgres":
        pytest.skip("Postgres-only: SQLite read sessions leave the isolation level alone")
    db = DatabaseSession(config.model_copy(update={"pool_max": 1}))
    await db.connect()
    yield db
    await db.close()


async def _driver_and_adapter(sess: AsyncSession) -> tuple[Any, Any]:
    raw = await (await sess.connection()).get_raw_connection()
    return raw.driver_connection, raw.dbapi_connection


class _BoomError(Exception):
    pass


@pytest.mark.asyncio
async def test_read_session_autocommit_is_reset_when_connection_returns_to_pool(
    single_connection_db: DatabaseSession,
) -> None:
    """After a read session, the same pooled connection is transactional again.

    The next ``session()`` runs at ``read_committed`` inside a driver
    transaction, and a failing ``transaction()`` on it leaves no row behind.
    """
    db = single_connection_db
    user_id = f"rs-reset-{uuid.uuid4().hex[:12]}"

    async with db.read_session() as sess:
        await sess.execute(text("SELECT 1"))
        read_driver, adapter = await _driver_and_adapter(sess)
        assert adapter.isolation_level == "autocommit"
        assert read_driver.is_in_transaction() is False

    async with db.session() as sess:
        await sess.execute(text("SELECT 1"))
        driver, adapter = await _driver_and_adapter(sess)
        assert driver is read_driver
        assert adapter.isolation_level == "read_committed"
        assert driver.is_in_transaction() is True

    in_transaction_during_insert: list[bool] = []
    with pytest.raises(_BoomError):
        async with db.transaction() as sess:
            await sess.execute(
                text(
                    "INSERT INTO users (id, email, first_name, last_name) "
                    "VALUES (:id, :email, 'Read', 'Reset')"
                ),
                {"id": user_id, "email": f"{user_id}@test.local"},
            )
            driver, _ = await _driver_and_adapter(sess)
            assert driver is read_driver
            in_transaction_during_insert.append(driver.is_in_transaction())
            raise _BoomError

    assert in_transaction_during_insert == [True]
    async with db.read_session() as sess:
        count = (
            await sess.execute(text("SELECT count(*) FROM users WHERE id = :id"), {"id": user_id})
        ).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_read_session_autocommit_is_reset_after_the_body_raises(
    single_connection_db: DatabaseSession,
) -> None:
    """A read session that raises still hands back a transactional connection."""
    db = single_connection_db

    with pytest.raises(_BoomError):
        async with db.read_session() as sess:
            await sess.execute(text("SELECT 1"))
            read_driver, _ = await _driver_and_adapter(sess)
            raise _BoomError

    async with db.session() as sess:
        await sess.execute(text("SELECT 1"))
        driver, adapter = await _driver_and_adapter(sess)
        assert driver is read_driver
        assert adapter.isolation_level == "read_committed"
        assert driver.is_in_transaction() is True
