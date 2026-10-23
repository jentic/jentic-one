"""Database connection, pooling, and keepalive configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator

# Default ``databases.*.pool_pre_ping_idle_s``: minimum time a connection must
# sit idle in the pool before checkout runs the ``pool_pre_ping`` wire check.
# Active and burst checkouts reuse connections within milliseconds, so gating
# the wire ping on a short idle window eliminates redundant ``BEGIN``/``;``/
# ``ROLLBACK`` round trips while still probing connections that have been idle.
POOL_PRE_PING_IDLE_S = 1.0

# Default ``databases.*.pool_recycle_s``: a pooled connection older than this is
# replaced at checkout. Bounds connection age so a connection silently dropped by
# a middlebox (Docker Desktop's VM network, a NAT gateway, a cloud load balancer)
# or left behind by a server restart is replaced proactively instead of failing a
# request. 30 minutes sits well inside the usual idle cutoffs of proxies that do
# not honour keepalives while costing one reconnect per connection per half hour.
POOL_RECYCLE_S = 1800

# Defaults for ``databases.*.tcp_keepalive_*`` (client-side TCP keepalive on each
# asyncpg socket). An idle connection is probed after 60s and a dead peer is
# detected after ~110s (60 + 5 x 10) rather than the OS default of hours.
TCP_KEEPALIVE_IDLE_S = 60
TCP_KEEPALIVE_INTERVAL_S = 10
TCP_KEEPALIVE_COUNT = 5
# Upper bounds the Linux kernel accepts for ``TCP_KEEPIDLE``/``TCP_KEEPINTVL``
# (seconds) and ``TCP_KEEPCNT`` (probes); larger values fail ``setsockopt``.
_TCP_KEEPALIVE_MAX_S = 32767
_TCP_KEEPALIVE_MAX_COUNT = 127


class DatabaseConfig(BaseModel):
    """Connection parameters for a single database.

    Supports two backends:

    - ``postgres`` (default): uses host/port/user/password/name + schema_name.
    - ``sqlite``: uses ``path`` (a single-file database). The Postgres
      connection fields are ignored.
    """

    backend: Literal["postgres", "sqlite"] = Field(
        default="postgres",
        description=(
            "Database engine for this connection: ``postgres`` uses the server "
            "connection fields; ``sqlite`` uses ``path`` and ignores them."
        ),
    )
    host: str = Field(default="localhost", description="PostgreSQL server hostname.")
    port: int = Field(default=5432, description="PostgreSQL server port.")
    name: str = Field(
        default="",
        description="PostgreSQL database name. Required for the ``postgres`` backend.",
    )
    user: str = Field(default="postgres", description="PostgreSQL role to connect as.")
    password: SecretStr = Field(
        default=SecretStr(""), description="Password for the PostgreSQL role."
    )
    pool_max: int = Field(
        default=10,
        description="Maximum connections in the PostgreSQL connection pool (ignored for SQLite).",
    )
    pool_pre_ping: bool = Field(
        default=True,
        description=(
            "PostgreSQL: test pooled connections on checkout and transparently "
            "replace a dead one. By default the wire ping (three round trips on "
            "asyncpg: ``BEGIN``, ``;``, ``ROLLBACK``) runs only when the connection "
            "has sat idle in the pool for at least ``pool_pre_ping_idle_s`` seconds; "
            "active checkouts within that window skip the wire ping unless the driver "
            "connection is already closed. With ``false``, ``pool_recycle_s`` and TCP "
            "keepalive do not cover a Postgres restart or failover, a pooler/proxy "
            "restart, or a terminated backend: after such an event the first request "
            "on each pool per process can fail with an HTTP 503 (work run through the "
            "retrying transaction helper is re-run on a fresh connection instead). "
            "Only disable it where those events are rare and that error is "
            "acceptable. Ignored for SQLite."
        ),
    )
    pool_pre_ping_idle_s: float = Field(
        default=POOL_PRE_PING_IDLE_S,
        ge=0,
        allow_inf_nan=False,
        description=(
            "PostgreSQL: seconds a connection must sit idle in the pool before "
            "checkout runs the ``pool_pre_ping`` wire check. Active checkouts "
            "returning within this window skip the wire ping unless the driver "
            "connection is already closed. ``0`` pings on every checkout. Only "
            "applies when ``pool_pre_ping`` is ``true``. Ignored for SQLite."
        ),
    )
    pool_recycle_s: int = Field(
        default=POOL_RECYCLE_S,
        ge=0,
        description=(
            "PostgreSQL: replace a pooled connection once it is this many seconds "
            "old, on its next checkout, so a connection silently dropped by a "
            "middlebox or left behind by a server restart is retired before use. "
            "Keep it below any server, proxy, or NAT idle timeout on the path. "
            "``0`` disables recycling. Ignored for SQLite."
        ),
    )
    tcp_keepalive: bool = Field(
        default=True,
        description=(
            "PostgreSQL: enable TCP keepalive on each pooled connection's client "
            "socket so a peer that vanished while the connection sat idle is "
            "detected in about ``tcp_keepalive_idle_s + tcp_keepalive_count * "
            "tcp_keepalive_interval_s`` seconds instead of the OS default of hours. "
            "``false`` leaves the socket at the OS defaults. Ignored for SQLite."
        ),
    )
    tcp_keepalive_idle_s: int = Field(
        default=TCP_KEEPALIVE_IDLE_S,
        ge=1,
        le=_TCP_KEEPALIVE_MAX_S,
        description=(
            "PostgreSQL: seconds a connection may sit idle before the first TCP "
            "keepalive probe is sent (``TCP_KEEPIDLE``; ``TCP_KEEPALIVE`` on "
            "macOS). Only applies when ``tcp_keepalive`` is ``true``. Ignored for SQLite."
        ),
    )
    tcp_keepalive_interval_s: int = Field(
        default=TCP_KEEPALIVE_INTERVAL_S,
        ge=1,
        le=_TCP_KEEPALIVE_MAX_S,
        description=(
            "PostgreSQL: seconds between unanswered TCP keepalive probes "
            "(``TCP_KEEPINTVL``). Only applies when ``tcp_keepalive`` is ``true``. "
            "Ignored for SQLite."
        ),
    )
    tcp_keepalive_count: int = Field(
        default=TCP_KEEPALIVE_COUNT,
        ge=1,
        le=_TCP_KEEPALIVE_MAX_COUNT,
        description=(
            "PostgreSQL: unanswered TCP keepalive probes before the connection is "
            "declared dead (``TCP_KEEPCNT``). Only applies when ``tcp_keepalive`` is "
            "``true``. Ignored for SQLite."
        ),
    )
    # The identifier pattern is defense-in-depth so a hostile config value
    # cannot escape the quoted identifier (SEC-2).
    schema_name: str = Field(
        default="public",
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        description=(
            "PostgreSQL schema for this connection: created if missing by the "
            "migration runner (``CREATE SCHEMA IF NOT EXISTS``) and put first on "
            "``search_path``, so several connections can share one database."
        ),
    )
    path: str | None = Field(
        default=None,
        description=(
            "SQLite: filesystem path to the database file (``:memory:`` for "
            "in-memory). Required for the ``sqlite`` backend."
        ),
    )
    busy_timeout_ms: int = Field(
        default=5000,
        description=(
            "SQLite: per-connection wait for a held write lock, in milliseconds, "
            "before failing with ``database is locked``."
        ),
    )
    journal_mode: str = Field(
        default="WAL",
        description=(
            "SQLite journal mode (persistent per database file). ``WAL`` lets a "
            "writer and readers proceed concurrently instead of blocking each other."
        ),
    )

    @model_validator(mode="after")
    def _validate_backend(self) -> DatabaseConfig:
        if self.backend == "postgres":
            if not self.name:
                raise ValueError("postgres backend requires a database 'name'")
        elif self.backend == "sqlite" and not self.path:
            raise ValueError("sqlite backend requires a 'path'")
        return self


class DatabasesConfig(BaseModel):
    """Named database connections."""

    registry: DatabaseConfig
    admin: DatabaseConfig
    control: DatabaseConfig
