"""Tests for pluggable database backend selection and engine wiring."""

from __future__ import annotations

import socket
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import create_async_engine

from jentic_one.shared.config import (
    POOL_PRE_PING_IDLE_S,
    POOL_RECYCLE_S,
    TCP_KEEPALIVE_COUNT,
    TCP_KEEPALIVE_IDLE_S,
    TCP_KEEPALIVE_INTERVAL_S,
    DatabaseConfig,
)
from jentic_one.shared.db.backends import (
    PostgresBackend,
    SqliteBackend,
    configure_idle_gated_pre_ping,
    configure_sqlite_pragmas,
    get_backend,
)
from jentic_one.shared.db.backends.postgres import (
    KeepaliveConnection,
    TcpKeepalive,
    _is_driver_closed,
    apply_tcp_keepalive,
    keepalive_connection_class,
)
from jentic_one.shared.db.session import DatabaseSession


def test_get_backend_returns_postgres_by_default() -> None:
    config = DatabaseConfig(name="reg")
    backend = get_backend(config)
    assert isinstance(backend, PostgresBackend)
    assert backend.dialect_name == "postgres"


def test_get_backend_returns_sqlite() -> None:
    config = DatabaseConfig(backend="sqlite", path=":memory:")
    backend = get_backend(config)
    assert isinstance(backend, SqliteBackend)
    assert backend.dialect_name == "sqlite"


def test_postgres_config_requires_name() -> None:
    with pytest.raises(ValueError, match="requires a database 'name'"):
        DatabaseConfig(backend="postgres", name="")


def test_sqlite_config_requires_path() -> None:
    with pytest.raises(ValueError, match="requires a 'path'"):
        DatabaseConfig(backend="sqlite")


def test_postgres_make_url_and_engine_kwargs() -> None:
    config = DatabaseConfig(name="reg", schema_name="registry")
    backend = PostgresBackend()
    url = backend.make_url(config)
    assert url.drivername == "postgresql+asyncpg"
    assert url.database == "reg"
    kwargs = backend.engine_kwargs(config)
    assert kwargs["connect_args"]["server_settings"]["search_path"] == "registry,public"


def test_sqlite_engine_kwargs_ignore_pool_pre_ping() -> None:
    config = DatabaseConfig(backend="sqlite", path=":memory:", pool_pre_ping=False)
    assert "pool_pre_ping" not in SqliteBackend().engine_kwargs(config)


def test_postgres_engine_kwargs_harden_idle_connections() -> None:
    """Default: pre-ping on, recycle every 1800s, TCP keepalive (#1082)."""
    kwargs = PostgresBackend().engine_kwargs(DatabaseConfig(name="reg"))
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == POOL_RECYCLE_S == 1800
    assert kwargs["connect_args"]["connection_class"] is KeepaliveConnection
    assert issubclass(KeepaliveConnection, asyncpg.Connection)
    assert KeepaliveConnection.keepalive == TcpKeepalive(
        idle_s=TCP_KEEPALIVE_IDLE_S, interval_s=TCP_KEEPALIVE_INTERVAL_S, count=TCP_KEEPALIVE_COUNT
    )


def test_postgres_engine_kwargs_follow_configured_tunables() -> None:
    """``databases.*`` pre-ping, pool-recycle and keepalive settings reach the engine kwargs."""
    config = DatabaseConfig(
        name="reg",
        pool_pre_ping=False,
        pool_recycle_s=300,
        tcp_keepalive_idle_s=30,
        tcp_keepalive_interval_s=3,
        tcp_keepalive_count=4,
    )
    kwargs = PostgresBackend().engine_kwargs(config)
    assert kwargs["pool_pre_ping"] is False
    assert kwargs["pool_recycle"] == 300
    conn_cls = kwargs["connect_args"]["connection_class"]
    assert issubclass(conn_cls, KeepaliveConnection)
    assert conn_cls.keepalive == TcpKeepalive(idle_s=30, interval_s=3, count=4)
    # The shared default class is never mutated by a per-database override.
    assert KeepaliveConnection.keepalive == TcpKeepalive()
    # Databases configured alike share one class.
    assert PostgresBackend().engine_kwargs(config)["connect_args"]["connection_class"] is conn_cls


def test_postgres_engine_kwargs_disable_recycle_and_keepalive() -> None:
    """``pool_recycle_s: 0`` omits recycling; ``tcp_keepalive: false`` drops the hook."""
    kwargs = PostgresBackend().engine_kwargs(
        DatabaseConfig(name="reg", pool_recycle_s=0, tcp_keepalive=False)
    )
    assert "pool_recycle" not in kwargs
    assert "connection_class" not in kwargs["connect_args"]
    assert kwargs["connect_args"]["server_settings"]["search_path"] == "public,public"


def test_keepalive_connection_class_default_settings_reuse_base_class() -> None:
    assert keepalive_connection_class(TcpKeepalive()) is KeepaliveConnection


@pytest.mark.parametrize(
    ("idle_s", "interval_s", "count"),
    [(TCP_KEEPALIVE_IDLE_S, TCP_KEEPALIVE_INTERVAL_S, TCP_KEEPALIVE_COUNT), (17, 4, 3)],
)
def test_apply_tcp_keepalive_sets_socket_options(idle_s: int, interval_s: int, count: int) -> None:
    """The keepalive helper enables SO_KEEPALIVE and sets the given idle/interval/count."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        apply_tcp_keepalive(sock, idle_s=idle_s, interval_s=interval_s, count=count)
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0
        idle_opt = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
        if idle_opt is not None:
            assert sock.getsockopt(socket.IPPROTO_TCP, idle_opt) == idle_s
        if hasattr(socket, "TCP_KEEPINTVL"):
            assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL) == interval_s
        if hasattr(socket, "TCP_KEEPCNT"):
            assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT) == count


class _FakeSocket:
    """Records ``setsockopt`` calls; stands in for the transport's socket."""

    family = socket.AF_INET

    def __init__(self) -> None:
        self.options: dict[tuple[int, int], int] = {}

    def setsockopt(self, level: int, opt: int, value: int) -> None:
        self.options[(level, opt)] = value


class _FakeTransport:
    def __init__(self, sock: _FakeSocket) -> None:
        self._sock = sock

    def get_extra_info(self, name: str) -> _FakeSocket | None:
        return self._sock if name == "socket" else None


def test_configured_keepalive_class_applies_its_values_to_the_socket() -> None:
    """The per-database connection class sets that database's values on its socket."""
    config = DatabaseConfig(
        name="reg", tcp_keepalive_idle_s=45, tcp_keepalive_interval_s=7, tcp_keepalive_count=2
    )
    conn_cls = PostgresBackend().engine_kwargs(config)["connect_args"]["connection_class"]
    sock = _FakeSocket()
    conn_cls.configure_transport(_FakeTransport(sock))
    assert sock.options[(socket.SOL_SOCKET, socket.SO_KEEPALIVE)] == 1
    idle_opt = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
    if idle_opt is not None:
        assert sock.options[(socket.IPPROTO_TCP, idle_opt)] == 45
    if hasattr(socket, "TCP_KEEPINTVL"):
        assert sock.options[(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL)] == 7
    if hasattr(socket, "TCP_KEEPCNT"):
        assert sock.options[(socket.IPPROTO_TCP, socket.TCP_KEEPCNT)] == 2


def test_apply_tcp_keepalive_skips_unix_sockets() -> None:
    """Unix-domain sockets have no TCP layer; the helper leaves them untouched."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        apply_tcp_keepalive(sock, idle_s=60, interval_s=10, count=5)


def test_sqlite_make_url_uses_path() -> None:
    config = DatabaseConfig(backend="sqlite", path="/tmp/x.db")
    backend = SqliteBackend()
    url = backend.make_url(config)
    assert url.drivername == "sqlite+aiosqlite"
    assert url.database == "/tmp/x.db"


@pytest.mark.asyncio
async def test_sqlite_engine_round_trip() -> None:
    config = DatabaseConfig(backend="sqlite", path=":memory:")
    backend = SqliteBackend()
    engine = create_async_engine(backend.make_url(config), **backend.engine_kwargs(config))
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_connect_hook_sets_wal_and_busy_timeout(tmp_path: Path) -> None:
    """The connect hook applies journal_mode=WAL and the configured busy_timeout.

    Uses a file-backed DB (not ``:memory:``) because WAL is a no-op for in-memory
    databases.
    """
    db_file = tmp_path / "pragma_test.db"
    config = DatabaseConfig(backend="sqlite", path=str(db_file), busy_timeout_ms=7321)
    backend = SqliteBackend()
    engine = create_async_engine(backend.make_url(config), **backend.engine_kwargs(config))
    configure_sqlite_pragmas(
        engine,
        journal_mode=config.journal_mode,
        busy_timeout_ms=config.busy_timeout_ms,
    )
    try:
        async with engine.connect() as conn:
            journal_mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
            busy_timeout = (await conn.execute(text("PRAGMA busy_timeout"))).scalar_one()
            foreign_keys = (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()
        assert journal_mode == "wal"
        assert busy_timeout == 7321
        assert foreign_keys == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_idle_gated_pre_ping_skips_active_checkouts_and_pings_after_idle() -> None:
    """Checkouts within ``idle_s`` skip ``do_ping``; checkouts at/after ``idle_s`` ping (#1935)."""
    assert POOL_PRE_PING_IDLE_S == 1.0
    now = 100.0
    pings = 0
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", pool_pre_ping=True)
    raw_do_ping = engine.sync_engine.dialect.do_ping

    def _counting_do_ping(dbapi_connection: DBAPIConnection) -> bool:
        nonlocal pings
        pings += 1
        return bool(raw_do_ping(dbapi_connection))

    engine.sync_engine.dialect.do_ping = _counting_do_ping  # type: ignore[method-assign]
    configure_idle_gated_pre_ping(engine, idle_s=1.0, clock=lambda: now)
    try:
        # First checkout creates the connection (fresh -> no pre-ping); checkin at t=100.0.
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        assert pings == 0

        # Active checkout 200ms later (< 1.0s) skips the ping; checkin resets to t=100.2.
        now = 100.2
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        assert pings == 0

        # 700ms after the second checkin (t=100.9) is still < 1.0s since last checkin.
        now = 100.9
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        assert pings == 0

        # 1.0s after the third checkin (t=101.9) reaches the idle threshold -> pings.
        now = 101.9
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        assert pings == 1

        # Immediate checkout 50ms later skips the ping again.
        now = 101.95
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        assert pings == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("idle_s", [0.0, -1.0, float("inf"), float("-inf"), float("nan")])
async def test_idle_gated_pre_ping_non_positive_or_non_finite_leaves_do_ping_untouched(
    idle_s: float,
) -> None:
    """``idle_s <= 0`` or non-finite leaves SQLAlchemy's per-checkout pre-ping untouched."""
    pings = 0
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", pool_pre_ping=True)
    raw_do_ping = engine.sync_engine.dialect.do_ping

    def _counting_do_ping(dbapi_connection: DBAPIConnection) -> bool:
        nonlocal pings
        pings += 1
        return bool(raw_do_ping(dbapi_connection))

    engine.sync_engine.dialect.do_ping = _counting_do_ping  # type: ignore[method-assign]
    configure_idle_gated_pre_ping(engine, idle_s=idle_s)
    assert engine.sync_engine.dialect.do_ping is _counting_do_ping
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        assert pings == 0
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        assert pings == 1
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        assert pings == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_idle_gated_pre_ping_noop_when_pool_pre_ping_disabled() -> None:
    """When ``pool_pre_ping=False``, ``configure_idle_gated_pre_ping`` is a no-op."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", pool_pre_ping=False)
    before = engine.sync_engine.dialect.do_ping
    configure_idle_gated_pre_ping(engine, idle_s=1.0)
    try:
        assert engine.sync_engine.dialect.do_ping == before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_idle_gated_pre_ping_replaces_closed_driver_within_idle_window() -> None:
    """A driver reporting ``is_closed()`` within ``idle_s`` fails pre-ping and reconnects."""
    now = 100.0
    pings = 0
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", pool_pre_ping=True)
    raw_do_ping = engine.sync_engine.dialect.do_ping

    def _counting_do_ping(dbapi_connection: DBAPIConnection) -> bool:
        nonlocal pings
        pings += 1
        return bool(raw_do_ping(dbapi_connection))

    engine.sync_engine.dialect.do_ping = _counting_do_ping  # type: ignore[method-assign]
    configure_idle_gated_pre_ping(engine, idle_s=10.0, clock=lambda: now)
    try:
        async with engine.connect() as conn:
            raw_first = await conn.get_raw_connection()
            first_dbapi = raw_first.dbapi_connection
            first_driver = raw_first.driver_connection
            assert first_driver is not None
            first_driver.is_closed = lambda: True
            await conn.execute(text("SELECT 1"))

        # Check out within the 10s window: _do_ping_if_idle sees is_closed() == True
        # and returns False without calling raw_do_ping, so the pool reconnects.
        now = 100.1
        async with engine.connect() as conn:
            raw_second = await conn.get_raw_connection()
            second_dbapi = raw_second.dbapi_connection
            await conn.execute(text("SELECT 1"))

        assert pings == 0
        assert second_dbapi is not first_dbapi
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_idle_gated_pre_ping_handles_non_weakrefable_slotted_adapter() -> None:
    """Slotted DBAPI wrappers with ``driver_connection=None`` do not raise ``TypeError``."""

    class _SlottedAdapter:
        __slots__ = ("driver_connection",)

        def __init__(self) -> None:
            self.driver_connection = None

    now = 50.0
    pings = 0
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", pool_pre_ping=True)

    def _counting_do_ping(dbapi_connection: DBAPIConnection) -> bool:
        nonlocal pings
        del dbapi_connection
        pings += 1
        return True

    engine.sync_engine.dialect.do_ping = _counting_do_ping  # type: ignore[method-assign]
    configure_idle_gated_pre_ping(engine, idle_s=1.0, clock=lambda: now)
    try:
        adapter = _SlottedAdapter()
        sync_pool = engine.sync_engine.pool
        sync_pool.dispatch.checkin(adapter, None)
        now = 50.2
        assert engine.sync_engine.dialect.do_ping(adapter) is True  # type: ignore[arg-type]
        assert pings == 0

        # Popped on ping/checkout, so a second ping without checkin runs raw_do_ping.
        assert engine.sync_engine.dialect.do_ping(adapter) is True  # type: ignore[arg-type]
        assert pings == 1

        # close and close_detached also clear any recorded checkin timestamp.
        sync_pool.dispatch.checkin(adapter, None)
        sync_pool.dispatch.close(adapter, None)
        assert engine.sync_engine.dialect.do_ping(adapter) is True  # type: ignore[arg-type]
        assert pings == 2

        sync_pool.dispatch.checkin(adapter, None)
        sync_pool.dispatch.close_detached(adapter)
        assert engine.sync_engine.dialect.do_ping(adapter) is True  # type: ignore[arg-type]
        assert pings == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_session_connect_wires_idle_gated_pre_ping_for_postgres() -> None:
    """``DatabaseSession.connect`` installs the idle-gated hook when ``pool_pre_ping`` is on."""
    gated_db = DatabaseSession(
        DatabaseConfig(name="reg", pool_pre_ping=True, pool_pre_ping_idle_s=2.5)
    )
    ungated_db = DatabaseSession(
        DatabaseConfig(name="reg", pool_pre_ping=True, pool_pre_ping_idle_s=0)
    )
    await gated_db.connect()
    await ungated_db.connect()
    try:
        assert "do_ping" in gated_db.engine.sync_engine.dialect.__dict__
        assert "do_ping" not in ungated_db.engine.sync_engine.dialect.__dict__
    finally:
        await gated_db.close()
        await ungated_db.close()


def test_is_driver_closed_checks_driver_connection() -> None:
    """A driver connection reporting ``is_closed() == True`` is flagged closed."""

    class _Driver:
        def __init__(self, closed: bool) -> None:
            self._closed = closed

        def is_closed(self) -> bool:
            return self._closed

    class _Adapter:
        def __init__(self, driver: _Driver | None) -> None:
            self.driver_connection = driver

    assert _is_driver_closed(_Adapter(_Driver(closed=True))) is True
    assert _is_driver_closed(_Adapter(_Driver(closed=False))) is False
    assert _is_driver_closed(_Adapter(None)) is False
