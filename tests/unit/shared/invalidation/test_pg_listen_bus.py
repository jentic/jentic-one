"""Unit tests for ``PgListenInvalidationBus`` supervision (no database).

The bus takes its connections from a ``PgListenTarget.connect`` factory, so
these tests hand it an in-memory stand-in for the one connection surface it
uses (listener registration, termination callback, liveness probe). The real
asyncpg path is covered against PostgreSQL in
``tests/integration/shared/test_pg_invalidation_bus.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from jentic_one.shared.config import DatabaseConfig, InvalidationConfig
from jentic_one.shared.invalidation import (
    PG_INVALIDATION_CHANNEL,
    InvalidationMessage,
    PgListenInvalidationBus,
    PgListenTarget,
    build_trigger_payload,
    listen_targets,
    postgres,
)

_FAST = InvalidationConfig(
    reconnect_backoff_base_s=0.01, reconnect_backoff_max_s=0.02, health_check_interval_s=0.05
)


class _Conn:
    """The subset of an asyncpg connection the bus touches."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.closed = False
        self.listeners: dict[str, Callable[..., None]] = {}
        self._on_terminate: list[Callable[[Any], None]] = []

    def add_termination_listener(self, cb: Callable[[Any], None]) -> None:
        self._on_terminate.append(cb)

    async def add_listener(self, channel: str, cb: Callable[..., None]) -> None:
        self.listeners[channel] = cb

    async def fetchval(self, _query: str) -> int:
        return 1

    def get_server_pid(self) -> int:
        return self.pid

    def is_closed(self) -> bool:
        return self.closed

    def terminate(self) -> None:
        self.closed = True

    def drop(self) -> None:
        """The server went away: asyncpg fires its termination listeners."""
        self.closed = True
        for cb in self._on_terminate:
            cb(self)

    def notify(self, payload: str) -> None:
        self.listeners[PG_INVALIDATION_CHANNEL](self, 0, PG_INVALIDATION_CHANNEL, payload)


class _Server:
    """Hands out connections; can be made to refuse them."""

    def __init__(self) -> None:
        self.conns: list[_Conn] = []
        self.refuse = 0

    async def connect(self) -> _Conn:
        if self.refuse:
            self.refuse -= 1
            raise OSError("connection refused")
        conn = _Conn(pid=len(self.conns) + 1)
        self.conns.append(conn)
        return conn


async def _until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not condition():
            await asyncio.sleep(0.005)


def _bus(server: _Server) -> PgListenInvalidationBus:
    target = PgListenTarget(
        name="db",
        connect=server.connect,
        schemas={"admin": ("admin",), "control": ("control",)},
    )
    return PgListenInvalidationBus([target], config=_FAST)


async def test_no_subscribers_opens_no_connection() -> None:
    server = _Server()
    bus = _bus(server)
    await bus.start()
    await bus.stop()
    assert server.conns == []


async def test_listens_only_on_servers_carrying_a_subscribed_database() -> None:
    """Admin-only subscriptions open nothing on a separate control server."""
    admin_server, control_server = _Server(), _Server()
    bus = PgListenInvalidationBus(
        [
            PgListenTarget(name="a", connect=admin_server.connect, schemas={"a": ("admin",)}),
            PgListenTarget(name="c", connect=control_server.connect, schemas={"c": ("control",)}),
        ],
        config=_FAST,
    )
    bus.subscribe("admin.provider_configs", lambda _m: None)
    await bus.start()
    try:
        assert len(admin_server.conns) == 1
        assert control_server.conns == []
    finally:
        await bus.stop()


async def test_routes_by_schema_and_ignores_foreign_schemas() -> None:
    server = _Server()
    bus = _bus(server)
    got: list[InvalidationMessage] = []
    bus.subscribe("admin.agents", got.append)
    await bus.start()
    try:
        conn = server.conns[0]
        conn.notify(
            build_trigger_payload(schema="other_install", table="agents", operation="UPDATE")
        )
        conn.notify(
            build_trigger_payload(
                schema="admin", table="agents", operation="UPDATE", keys={"id": ["agnt_1"]}
            )
        )
        conn.notify("garbage")
    finally:
        await bus.stop()
    assert [(m.topic, m.values("id")) for m in got] == [("admin.agents", ("agnt_1",))]


async def test_lost_connection_reconnects_and_flushes() -> None:
    server = _Server()
    bus = _bus(server)
    admin: list[InvalidationMessage] = []
    control: list[InvalidationMessage] = []
    bus.subscribe("admin.access_tokens", admin.append)
    bus.subscribe("control.credentials", control.append)
    await bus.start()
    try:
        assert len(server.conns) == 1 and not admin, "no flush on the first connection"
        server.refuse = 2  # the first two reconnect attempts fail
        server.conns[0].drop()
        await _until(lambda: len(server.conns) == 2 and bool(admin) and bool(control))
        assert [m.flush for m in admin] == [True]
        assert [m.flush for m in control] == [True]
        assert bus.listener_pid("db") == 2
    finally:
        await bus.stop()
    assert all(c.closed for c in server.conns)


async def test_failed_first_connect_is_retried_and_then_flushes() -> None:
    server = _Server()
    server.refuse = 1
    bus = _bus(server)
    got: list[InvalidationMessage] = []
    bus.subscribe("admin.agents", got.append)
    await bus.start()
    try:
        await _until(lambda: bool(server.conns) and bool(got))
        # Requests may have been cached while nothing was listening.
        assert got[0].flush
    finally:
        await bus.stop()


async def test_failed_liveness_probe_counts_as_lost() -> None:
    class _Stalled(_Conn):
        async def fetchval(self, _query: str) -> int:
            raise OSError("probe failed")

    class _StallOnce(_Server):
        async def connect(self) -> _Conn:
            conn: _Conn = _Stalled(1) if not self.conns else _Conn(len(self.conns) + 1)
            self.conns.append(conn)
            return conn

    server = _StallOnce()
    bus = _bus(server)
    got: list[InvalidationMessage] = []
    bus.subscribe("admin.agents", got.append)
    await bus.start()
    try:
        await _until(lambda: len(server.conns) == 2 and bool(got))
    finally:
        await bus.stop()


async def test_no_delivery_after_stop() -> None:
    server = _Server()
    bus = _bus(server)
    got: list[InvalidationMessage] = []
    bus.subscribe("admin.agents", got.append)
    await bus.start()
    conn = server.conns[0]
    await bus.stop()
    conn.notify(build_trigger_payload(schema="admin", table="agents", operation="DELETE"))
    assert got == []


def test_listen_targets_group_by_server() -> None:
    targets = listen_targets(
        {
            "admin": DatabaseConfig(host="db", name="jentic", schema_name="admin"),
            "control": DatabaseConfig(host="db", name="jentic", schema_name="control"),
            "other": DatabaseConfig(host="db2", name="jentic", schema_name="admin"),
        }
    )
    by_name = {t.name: t for t in targets}
    assert by_name["db:5432/jentic"].schemas == {"admin": ("admin",), "control": ("control",)}
    assert by_name["db2:5432/jentic"].schemas == {"admin": ("other",)}


async def test_shared_schema_feeds_every_database_on_it() -> None:
    """Two logical databases on one schema must both keep their topics."""
    [target] = listen_targets(
        {
            "admin": DatabaseConfig(host="db", name="jentic"),
            "control": DatabaseConfig(host="db", name="jentic"),
        }
    )
    assert target.schemas == {"public": ("admin", "control")}
    server = _Server()
    bus = PgListenInvalidationBus(
        [PgListenTarget(name="db", connect=server.connect, schemas=target.schemas)], config=_FAST
    )
    admin: list[InvalidationMessage] = []
    control: list[InvalidationMessage] = []
    bus.subscribe("admin.access_tokens", admin.append)
    bus.subscribe("control.credentials", control.append)
    await bus.start()
    try:
        conn = server.conns[0]
        conn.notify(build_trigger_payload(schema="public", table="access_tokens", operation="X"))
        conn.notify(build_trigger_payload(schema="public", table="credentials", operation="Y"))
    finally:
        await bus.stop()
    assert [m.operation for m in admin] == ["X"]
    assert [m.operation for m in control] == ["Y"]


async def test_first_listen_after_start_returned_flushes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A first LISTEN that lands once the app is serving must flush what was cached."""
    monkeypatch.setattr(postgres, "_START_WAIT_S", 0.01)
    release = asyncio.Event()

    class _Slow(_Server):
        async def connect(self) -> _Conn:
            await release.wait()
            return await super().connect()

    server = _Slow()
    bus = _bus(server)
    got: list[InvalidationMessage] = []
    bus.subscribe("admin.agents", got.append)
    await bus.start()  # returns after the (shortened) start wait; nothing is listening
    try:
        assert not server.conns
        release.set()
        await _until(lambda: bool(server.conns) and bool(got))
        assert [m.flush for m in got] == [True]
    finally:
        await bus.stop()
