"""PostgreSQL database backend (asyncpg)."""

from __future__ import annotations

import socket
from typing import Any

import asyncpg
import structlog
from sqlalchemy.engine import URL

from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db.backends.base import DatabaseBackend

logger = structlog.get_logger(__name__)

# Recycle a pooled connection once it is older than this (checked at checkout).
# Bounds connection age so a connection silently dropped by a middlebox (Docker
# Desktop's VM network, a NAT gateway, a cloud load balancer) or left behind by a
# server restart is replaced proactively instead of failing a request. 30 minutes
# sits well inside the usual idle cutoffs of proxies that do not honour keepalives
# while costing one reconnect per connection per half hour.
POOL_RECYCLE_S = 1800

# Client-side TCP keepalive. asyncpg has no keepalive option (libpq's
# ``keepalives*`` parameters are not supported and ``server_settings`` only sets
# server GUCs such as ``tcp_keepalives_idle``, which tune the *server's* socket),
# so the options are set on the client socket by ``KeepaliveConnection``. With
# these values an idle connection is probed after 60s and a dead peer is
# detected after ~110s (60 + 5 x 10) rather than the OS default of hours.
TCP_KEEPALIVE_IDLE_S = 60
TCP_KEEPALIVE_INTERVAL_S = 10
TCP_KEEPALIVE_COUNT = 5


def apply_tcp_keepalive(sock: Any) -> None:
    """Enable TCP keepalive with the module's idle/interval/count on ``sock``.

    No-op for non-TCP (Unix-domain) sockets. The idle option is
    ``TCP_KEEPIDLE`` on Linux and ``TCP_KEEPALIVE`` on macOS; options the
    platform does not expose are skipped, leaving the OS default.
    """
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    idle_opt = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
    tuning = (
        (idle_opt, TCP_KEEPALIVE_IDLE_S),
        (getattr(socket, "TCP_KEEPINTVL", None), TCP_KEEPALIVE_INTERVAL_S),
        (getattr(socket, "TCP_KEEPCNT", None), TCP_KEEPALIVE_COUNT),
    )
    for opt, value in tuning:
        if opt is not None:
            sock.setsockopt(socket.IPPROTO_TCP, opt, value)


class KeepaliveConnection(asyncpg.Connection):  # type: ignore[misc]
    """asyncpg connection that enables TCP keepalive on its socket.

    Passed to ``asyncpg.connect`` as ``connection_class`` (via SQLAlchemy
    ``connect_args``). asyncpg hands the connected transport to the
    connection's constructor, which is the earliest point the socket is
    reachable. Setting the options is best-effort: a failure is logged and the
    connection is still usable (``pool_pre_ping`` and ``pool_recycle`` keep
    covering stale connections).
    """

    def __init__(self, protocol: Any, transport: Any, *args: Any, **kwargs: Any) -> None:
        super().__init__(protocol, transport, *args, **kwargs)
        sock = transport.get_extra_info("socket")
        if sock is None:
            return
        try:
            apply_tcp_keepalive(sock)
        except OSError as exc:
            logger.debug("db_tcp_keepalive_unavailable", error=str(exc))


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
        return {
            "pool_size": config.pool_max,
            "pool_pre_ping": True,
            "pool_recycle": POOL_RECYCLE_S,
            "connect_args": {
                "server_settings": {"search_path": f"{config.schema_name},public"},
                "connection_class": KeepaliveConnection,
            },
        }
