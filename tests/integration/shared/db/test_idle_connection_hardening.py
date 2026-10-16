"""Integration tests for idle-connection hardening on Postgres (#1082).

Covers the engine wiring that keeps pooled connections healthy across a
dropped/idle connection: TCP keepalive on the client socket and transparent
replacement of a pooled connection the server has terminated.
"""

from __future__ import annotations

import socket

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from jentic_one.shared.db.backends.postgres import (
    POOL_RECYCLE_S,
    TCP_KEEPALIVE_IDLE_S,
    KeepaliveConnection,
)
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


def _require_postgres(db: DatabaseSession) -> None:
    if db.backend.dialect_name != "postgres":
        pytest.skip("Postgres-only: TCP keepalive and backend termination")


@pytest.mark.asyncio
async def test_pooled_connection_has_tcp_keepalive(admin_db: DatabaseSession) -> None:
    """Every pooled asyncpg connection enables keepalive on its TCP socket."""
    _require_postgres(admin_db)
    assert admin_db.engine.pool._recycle == POOL_RECYCLE_S
    async with admin_db.engine.connect() as conn:
        raw = await conn.get_raw_connection()
        driver = raw.driver_connection
        assert isinstance(driver, KeepaliveConnection)
        sock = driver._transport.get_extra_info("socket")
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0
        idle_opt = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
        if idle_opt is not None:
            assert sock.getsockopt(socket.IPPROTO_TCP, idle_opt) == TCP_KEEPALIVE_IDLE_S


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
