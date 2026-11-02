"""PostgreSQL database backend (asyncpg)."""

from __future__ import annotations

import math
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from typing import Any, ClassVar

import asyncpg
import structlog
from sqlalchemy import event
from sqlalchemy.engine import URL
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import ConnectionPoolEntry

from jentic_one.shared.config import (
    POOL_PRE_PING_IDLE_S,
    TCP_KEEPALIVE_COUNT,
    TCP_KEEPALIVE_IDLE_S,
    TCP_KEEPALIVE_INTERVAL_S,
    DatabaseConfig,
)
from jentic_one.shared.db.backends.base import DatabaseBackend

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class TcpKeepalive:
    """Client-side TCP keepalive tuning for one database's connections.

    asyncpg has no keepalive option (libpq's ``keepalives*`` parameters are not
    supported and ``server_settings`` only sets server GUCs such as
    ``tcp_keepalives_idle``, which tune the *server's* socket), so the options
    are set on the client socket by :class:`KeepaliveConnection`. Values come
    from ``databases.*.tcp_keepalive_*``.
    """

    idle_s: int = TCP_KEEPALIVE_IDLE_S
    interval_s: int = TCP_KEEPALIVE_INTERVAL_S
    count: int = TCP_KEEPALIVE_COUNT

    @classmethod
    def from_config(cls, config: DatabaseConfig) -> TcpKeepalive:
        return cls(
            idle_s=config.tcp_keepalive_idle_s,
            interval_s=config.tcp_keepalive_interval_s,
            count=config.tcp_keepalive_count,
        )


def apply_tcp_keepalive(sock: Any, *, idle_s: int, interval_s: int, count: int) -> None:
    """Enable TCP keepalive on ``sock`` with the given idle/interval/count.

    No-op for non-TCP (Unix-domain) sockets. The idle option is
    ``TCP_KEEPIDLE`` on Linux and ``TCP_KEEPALIVE`` on macOS; options the
    platform does not expose are skipped, leaving the OS default.
    """
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    idle_opt = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
    tuning = (
        (idle_opt, idle_s),
        (getattr(socket, "TCP_KEEPINTVL", None), interval_s),
        (getattr(socket, "TCP_KEEPCNT", None), count),
    )
    for opt, value in tuning:
        if opt is not None:
            sock.setsockopt(socket.IPPROTO_TCP, opt, value)


class KeepaliveConnection(asyncpg.Connection):  # type: ignore[misc]
    """asyncpg connection that enables TCP keepalive on its socket.

    Passed to ``asyncpg.connect`` as ``connection_class`` (via SQLAlchemy
    ``connect_args``). asyncpg hands the connected transport to the
    connection's constructor, which is the earliest point the socket is
    reachable, and only accepts a class there — so per-database tuning is a
    class attribute on a subclass built by :func:`keepalive_connection_class`.
    Setting the options is best-effort: a failure is logged and the
    connection is still usable (``pool_pre_ping``, when enabled, and
    ``pool_recycle`` cover stale connections).
    """

    keepalive: ClassVar[TcpKeepalive] = TcpKeepalive()

    def __init__(self, protocol: Any, transport: Any, *args: Any, **kwargs: Any) -> None:
        super().__init__(protocol, transport, *args, **kwargs)
        self.configure_transport(transport)

    @classmethod
    def configure_transport(cls, transport: Any) -> None:
        """Apply this class's keepalive settings to ``transport``'s socket."""
        sock = transport.get_extra_info("socket")
        if sock is None:
            return
        settings = cls.keepalive
        try:
            apply_tcp_keepalive(
                sock,
                idle_s=settings.idle_s,
                interval_s=settings.interval_s,
                count=settings.count,
            )
        except OSError as exc:
            logger.debug("db_tcp_keepalive_unavailable", error=str(exc))


@cache
def keepalive_connection_class(settings: TcpKeepalive) -> type[KeepaliveConnection]:
    """Return a :class:`KeepaliveConnection` subclass bound to ``settings``.

    Cached so databases configured alike share one immutable class.
    """
    if settings == KeepaliveConnection.keepalive:
        return KeepaliveConnection
    return type(
        "KeepaliveConnection",
        (KeepaliveConnection,),
        {"keepalive": settings, "__module__": __name__},
    )


class PostgresBackend(DatabaseBackend):
    """Backend for PostgreSQL using the asyncpg driver."""

    @property
    def dialect_name(self) -> str:
        return "postgres"

    def make_url(self, config: DatabaseConfig) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=config.user,
            password=config.password.get_secret_value(),
            host=config.host,
            port=config.port,
            database=config.name,
        )

    def engine_kwargs(self, config: DatabaseConfig) -> dict[str, Any]:
        connect_args: dict[str, Any] = {
            "server_settings": {"search_path": f"{config.schema_name},public"},
        }
        if config.tcp_keepalive:
            connect_args["connection_class"] = keepalive_connection_class(
                TcpKeepalive.from_config(config)
            )
        kwargs: dict[str, Any] = {
            "pool_size": config.pool_max,
            "pool_pre_ping": config.pool_pre_ping,
            "connect_args": connect_args,
        }
        # 0 disables recycling; SQLAlchemy's own "never recycle" is -1, so the
        # key is omitted rather than passed through.
        if config.pool_recycle_s > 0:
            kwargs["pool_recycle"] = config.pool_recycle_s
        return kwargs

    async def open_listen_connection(self, config: DatabaseConfig) -> Any:
        """Open a dedicated autocommit ``asyncpg`` connection outside the pool."""
        connection_class = (
            keepalive_connection_class(TcpKeepalive.from_config(config))
            if config.tcp_keepalive
            else asyncpg.Connection
        )
        return await asyncpg.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password.get_secret_value(),
            database=config.name,
            server_settings={"search_path": f"{config.schema_name},public"},
            connection_class=connection_class,
        )


def _is_driver_closed(dbapi_connection: Any) -> bool:
    driver = getattr(dbapi_connection, "driver_connection", None)
    is_closed = getattr(driver, "is_closed", None)
    return bool(is_closed()) if callable(is_closed) else False


def configure_idle_gated_pre_ping(
    engine: AsyncEngine,
    *,
    idle_s: float = POOL_PRE_PING_IDLE_S,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Gate ``pool_pre_ping`` wire checks on how long a connection sat idle.

    Records each connection's monotonic checkin timestamp and skips the
    dialect's ``do_ping`` round trip when the connection is checked back out
    within ``idle_s`` seconds, unless the underlying driver connection already
    reports itself closed. Connections idle for at least ``idle_s`` seconds
    run the full dialect ``do_ping``. No-op when ``pool_pre_ping`` is disabled
    on ``engine``, ``idle_s <= 0``, or ``idle_s`` is non-finite.
    """
    sync_engine = engine.sync_engine
    if (
        not getattr(sync_engine.pool, "_pre_ping", False)
        or not math.isfinite(idle_s)
        or idle_s <= 0
    ):
        return

    dialect = sync_engine.dialect
    raw_do_ping = dialect.do_ping
    # Keyed by id(dbapi_connection): the pool's ConnectionPoolEntry holds a
    # strong reference to dbapi_connection for the entire time it sits checked
    # in, and checkout/close/close_detached pop the entry before the object can
    # be collected or its address reused. Avoids WeakKeyDictionary, which fails
    # with TypeError on slotted DBAPI wrappers (AdaptedConnection) and C
    # extensions (sqlite3.Connection) when driver_connection is None.
    last_checkin: dict[int, float] = {}

    @event.listens_for(sync_engine, "checkin")
    def _record_checkin(
        dbapi_connection: DBAPIConnection | None, _record: ConnectionPoolEntry
    ) -> None:
        if dbapi_connection is not None:
            last_checkin[id(dbapi_connection)] = clock()

    @event.listens_for(sync_engine, "checkout")
    def _clear_on_checkout(
        dbapi_connection: DBAPIConnection,
        _record: ConnectionPoolEntry,
        _proxy: Any,
    ) -> None:
        last_checkin.pop(id(dbapi_connection), None)

    @event.listens_for(sync_engine, "close")
    def _clear_on_close(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
        last_checkin.pop(id(dbapi_connection), None)

    @event.listens_for(sync_engine, "close_detached")
    def _clear_on_close_detached(dbapi_connection: DBAPIConnection) -> None:
        last_checkin.pop(id(dbapi_connection), None)

    def _do_ping_if_idle(dbapi_connection: DBAPIConnection) -> bool:
        checked_in_at = last_checkin.pop(id(dbapi_connection), None)
        if checked_in_at is not None and (clock() - checked_in_at) < idle_s:
            return not _is_driver_closed(dbapi_connection)
        return bool(raw_do_ping(dbapi_connection))

    dialect.do_ping = _do_ping_if_idle  # type: ignore[method-assign]
