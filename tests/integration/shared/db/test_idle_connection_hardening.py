"""Integration tests for idle-connection hardening on Postgres (#1082).

Covers the engine wiring that keeps pooled connections healthy across a
dropped/idle connection: TCP keepalive on the client socket and transparent
replacement of a pooled connection the server has terminated — and that real
errors on a live connection are not mistaken for a lost one.
"""

from __future__ import annotations

import socket

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.backends.postgres import KeepaliveConnection
from jentic_one.shared.db.connectivity import is_connection_loss
from jentic_one.shared.db.errors import DatabaseUnavailableError
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


def _require_postgres(db: DatabaseSession) -> None:
    if db.backend.dialect_name != "postgres":
        pytest.skip("Postgres-only: TCP keepalive and backend termination")


def _keepalive_idle(sock: socket.socket) -> int | None:
    idle_opt = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
    return None if idle_opt is None else sock.getsockopt(socket.IPPROTO_TCP, idle_opt)


@pytest.mark.asyncio
async def test_pooled_connection_has_tcp_keepalive(
    admin_db: DatabaseSession, integration_config: AppConfig
) -> None:
    """Every pooled asyncpg connection enables keepalive on its TCP socket."""
    _require_postgres(admin_db)
    config = integration_config.databases.admin
    assert admin_db.engine.pool._recycle == config.pool_recycle_s
    async with admin_db.engine.connect() as conn:
        raw = await conn.get_raw_connection()
        driver = raw.driver_connection
        # asyncpg's metaclass makes isinstance() true for any Connection; check the type.
        assert driver is not None
        assert issubclass(type(driver), KeepaliveConnection)
        sock = driver._transport.get_extra_info("socket")
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0
        idle = _keepalive_idle(sock)
        assert idle is None or idle == config.tcp_keepalive_idle_s


@pytest.mark.asyncio
async def test_configured_keepalive_and_recycle_reach_the_live_connection(
    admin_db: DatabaseSession, integration_config: AppConfig
) -> None:
    """Non-default ``databases.*`` tunables are what the real socket and pool use."""
    _require_postgres(admin_db)
    config = integration_config.databases.admin.model_copy(
        update={
            "pool_recycle_s": 123,
            "tcp_keepalive_idle_s": 77,
            "tcp_keepalive_interval_s": 7,
            "tcp_keepalive_count": 3,
        }
    )
    db = DatabaseSession(config)
    await db.connect()
    try:
        assert db.engine.pool._recycle == 123
        async with db.engine.connect() as conn:
            raw = await conn.get_raw_connection()
            driver = raw.driver_connection
            assert driver is not None
            assert issubclass(type(driver), KeepaliveConnection)
            sock = driver._transport.get_extra_info("socket")
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0
            assert _keepalive_idle(sock) in (None, 77)
            if hasattr(socket, "TCP_KEEPINTVL"):
                assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL) == 7
            if hasattr(socket, "TCP_KEEPCNT"):
                assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT) == 3
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_tcp_keepalive_disabled_leaves_socket_untouched(
    admin_db: DatabaseSession, integration_config: AppConfig
) -> None:
    """``tcp_keepalive: false`` connects with the plain asyncpg connection class."""
    _require_postgres(admin_db)
    config = integration_config.databases.admin.model_copy(update={"tcp_keepalive": False})
    db = DatabaseSession(config)
    await db.connect()
    try:
        async with db.engine.connect() as conn:
            raw = await conn.get_raw_connection()
            assert type(raw.driver_connection) is asyncpg.Connection
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_terminated_pooled_connection_is_replaced_transparently(
    admin_db: DatabaseSession,
) -> None:
    """A pooled connection killed server-side costs a reconnect, not an error.

    Mirrors a connection dropped while idle (VM sleep, proxy timeout): the next
    checkout's pre-ping detects it and the pool reconnects before the caller's
    statement runs.
    """
    _require_postgres(admin_db)

    async def _pid(session: AsyncSession) -> int:
        return int((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())

    stale_pid = await admin_db.run_in_transaction(_pid)

    # Kill the pooled backend from a separate, unpooled engine (a checkout
    # from the same pool would hand back the very connection being killed).
    killer_engine = create_async_engine(admin_db.engine.url, poolclass=NullPool)
    try:
        async with killer_engine.connect() as killer:
            killed = (
                await killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": stale_pid})
            ).scalar_one()
    finally:
        await killer_engine.dispose()
    assert killed is True

    fresh_pid = await admin_db.run_in_transaction(_pid)
    assert fresh_pid != stale_pid


@pytest.mark.asyncio
async def test_live_connection_db_errors_are_not_connection_loss(admin_db: DatabaseSession) -> None:
    """Real errors on a live connection, as DatabaseSession raises them, stay real errors.

    SQLite reports a missing table as ``OperationalError`` (mapped to
    ``DatabaseUnavailableError``); Postgres reports a statement timeout as an
    asyncpg ``QueryCanceledError``. Neither may be mistaken for a lost connection.
    """

    async def _missing_table(session: AsyncSession) -> None:
        await session.execute(text("SELECT * FROM table_that_does_not_exist_1082"))

    async def _statement_timeout(session: AsyncSession) -> None:
        await session.execute(text("SET LOCAL statement_timeout = '50ms'"))
        await session.execute(text("SELECT pg_sleep(2)"))

    is_postgres = admin_db.backend.dialect_name == "postgres"
    with pytest.raises(Exception) as caught:
        await admin_db.run_in_transaction(
            _statement_timeout if is_postgres else _missing_table, retries=0
        )
    assert not is_connection_loss(caught.value), repr(caught.value)
    if not is_postgres:
        assert isinstance(caught.value, DatabaseUnavailableError)

    # The pool is still usable afterwards.
    async def _one(session: AsyncSession) -> int:
        return int((await session.execute(text("SELECT 1"))).scalar_one())

    assert await admin_db.run_in_transaction(_one) == 1
