"""PostgreSQL ``LISTEN``-backed ``InvalidationBus``.

Holds **one dedicated connection per database server** (admin and control
sharing a database share the connection), opened directly with asyncpg and
kept outside the SQLAlchemy pool: a ``LISTEN`` is session state, so it cannot
live on a pooled connection that is handed to other work between checkouts.

Each connection is watched by a supervisor task owned by the bus (started in
:meth:`PgListenInvalidationBus.start`, cancelled and awaited in
:meth:`~PgListenInvalidationBus.stop`, so it never outlives the app
lifespan). A connection is considered lost when asyncpg reports it terminated
or a periodic ``SELECT 1`` probe fails or stalls; the supervisor then reconnects
with capped exponential backoff and, once ``LISTEN`` is re-established, sends a
``flush`` to every topic of that connection's databases — notifications sent
while it was down are not queued for it, so the affected keys are unknown.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

import asyncpg
import structlog

from jentic_one.shared.config import DatabaseConfig, InvalidationConfig
from jentic_one.shared.db.backends.postgres import TcpKeepalive, keepalive_connection_class
from jentic_one.shared.invalidation.memory import SubscriberRegistry
from jentic_one.shared.invalidation.protocol import (
    PG_INVALIDATION_CHANNEL,
    InvalidationHandler,
    InvalidationMessage,
    parse_trigger_payload,
    topic_for,
)

logger = structlog.get_logger(__name__)

#: ``application_name`` the LISTEN connection reports, so operators can tell it
#: apart from pooled connections in ``pg_stat_activity``.
LISTEN_APPLICATION_NAME = "jentic-one-invalidation"

#: Bound on the first connection attempt awaited inside :meth:`start`; a slower
#: database does not hold up boot — the supervisor keeps retrying behind it.
_START_WAIT_S = 10.0

#: Bound on one connection attempt. asyncpg's own default (60 s) would let a
#: black-holed server stall the supervisor far past the reconnect backoff.
_CONNECT_TIMEOUT_S = 10.0

#: An async factory returning a connected asyncpg connection.
ConnectFactory = Callable[[], Awaitable[Any]]


@dataclass(frozen=True)
class PgListenTarget:
    """One database to ``LISTEN`` on and the logical databases it carries.

    ``schemas`` maps a schema name (what the trigger reports as
    ``TG_TABLE_SCHEMA``) to the logical databases (``admin``/``control``) whose
    topics it feeds — more than one when several logical databases share the
    schema; a message then goes to each database's topic for the table, and
    only the one that owns the table has subscribers. Notifications from any
    other schema — another install sharing the database — are ignored.
    """

    name: str
    connect: ConnectFactory
    schemas: Mapping[str, tuple[str, ...]]

    @property
    def databases(self) -> frozenset[str]:
        return frozenset(db for dbs in self.schemas.values() for db in dbs)


def _asyncpg_connect_factory(config: DatabaseConfig) -> ConnectFactory:
    """A factory opening a LISTEN connection with the pool's keepalive tuning."""
    kwargs: dict[str, Any] = {
        "host": config.host,
        "port": config.port,
        "user": config.user,
        "password": config.password.get_secret_value(),
        "database": config.name,
        "server_settings": {"application_name": LISTEN_APPLICATION_NAME},
        "timeout": _CONNECT_TIMEOUT_S,
    }
    if config.tcp_keepalive:
        kwargs["connection_class"] = keepalive_connection_class(TcpKeepalive.from_config(config))

    async def _connect() -> Any:
        return await asyncpg.connect(**kwargs)

    return _connect


def listen_targets(databases: Mapping[str, DatabaseConfig]) -> list[PgListenTarget]:
    """Group logical databases by server so each server gets one connection.

    ``databases`` maps a logical database name to its (PostgreSQL) config.
    Databases on the same ``host:port/name`` share a target; the first one's
    credentials open the connection (``LISTEN`` needs no table privileges).
    """
    grouped: dict[tuple[str, int, str], list[tuple[str, DatabaseConfig]]] = {}
    for db_name, cfg in databases.items():
        grouped.setdefault((cfg.host, cfg.port, cfg.name), []).append((db_name, cfg))
    targets: list[PgListenTarget] = []
    for (host, port, name), members in grouped.items():
        schemas: dict[str, list[str]] = {}
        for db_name, cfg in members:
            schemas.setdefault(cfg.schema_name, []).append(db_name)
        targets.append(
            PgListenTarget(
                name=f"{host}:{port}/{name}",
                connect=_asyncpg_connect_factory(members[0][1]),
                schemas={schema: tuple(dbs) for schema, dbs in schemas.items()},
            )
        )
    return targets


def _set_on_terminate(lost: asyncio.Event, _conn: Any) -> None:
    lost.set()


class PgListenInvalidationBus:
    """``InvalidationBus`` over PostgreSQL ``LISTEN``/``NOTIFY``."""

    def __init__(
        self,
        targets: Sequence[PgListenTarget],
        *,
        config: InvalidationConfig | None = None,
    ) -> None:
        self._targets = tuple(targets)
        self._config = config or InvalidationConfig()
        self._subscribers = SubscriberRegistry()
        self._tasks: list[asyncio.Task[None]] = []
        self._connections: dict[str, Any] = {}
        self._running = False
        # Set once start() hands control back to the app: from then on caches
        # may fill, so a LISTEN established later (even a slow first one) must
        # flush what was cached while nothing was listening.
        self._serving = False

    def subscribe(self, topic: str, handler: InvalidationHandler) -> None:
        self._subscribers.add(topic, handler)

    async def start(self) -> None:
        if self._running or not self._subscribers or not self._targets:
            return
        self._running = True
        attempted: list[asyncio.Event] = []
        for target in self._targets:
            first_attempt = asyncio.Event()
            attempted.append(first_attempt)
            self._tasks.append(
                asyncio.create_task(
                    self._supervise(target, first_attempt),
                    name=f"invalidation-listen:{target.name}",
                )
            )
        # Wait (bounded) for each first attempt so a reachable database is
        # listening before the app serves; an unreachable one keeps retrying.
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(_START_WAIT_S):
                for event in attempted:
                    await event.wait()
        self._serving = True

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._serving = False
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def listener_pid(self, target_name: str) -> int | None:
        """Server PID of ``target_name``'s live LISTEN connection (diagnostics/tests)."""
        conn = self._connections.get(target_name)
        if conn is None or conn.is_closed():
            return None
        pid: int = conn.get_server_pid()
        return pid

    @property
    def target_names(self) -> tuple[str, ...]:
        return tuple(t.name for t in self._targets)

    def _backoff_s(self, attempt: int) -> float:
        cfg = self._config
        delay = min(
            cfg.reconnect_backoff_base_s * float(2 ** max(0, attempt - 1)),
            cfg.reconnect_backoff_max_s,
        )
        # Half-jitter: spread a fleet's reconnects after a database restart.
        return delay * (0.5 + random.random() / 2)  # nosec B311 - jitter, not crypto

    async def _supervise(self, target: PgListenTarget, first_attempt: asyncio.Event) -> None:
        attempt = 0
        while self._running:
            conn: Any = None
            try:
                conn = await target.connect()
                lost = asyncio.Event()
                conn.add_termination_listener(partial(_set_on_terminate, lost))
                await conn.add_listener(PG_INVALIDATION_CHANNEL, partial(self._on_notify, target))
                self._connections[target.name] = conn
                if attempt > 0 or self._serving:
                    # Anything committed while we were not listening is lost to
                    # us — after a lost connection, or a first LISTEN that only
                    # landed once the app was already serving: drop everything
                    # those databases' topics cached.
                    logger.info(
                        "invalidation_listen_reconnected", target=target.name, attempts=attempt
                    )
                    self._subscribers.flush_all(databases=target.databases)
                else:
                    logger.info("invalidation_listen_started", target=target.name)
                first_attempt.set()
                attempt = 0
                await self._watch(conn, lost)
                logger.warning(
                    "invalidation_listen_lost",
                    target=target.name,
                    actionable_step=(
                        "Reconnecting; broker caches fall back to their TTLs until "
                        "LISTEN is re-established."
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "invalidation_listen_connect_failed",
                    target=target.name,
                    attempt=attempt + 1,
                    error=type(exc).__name__,
                    actionable_step=(
                        "Check the database is reachable; cross-process cache "
                        "invalidation is paused and cache TTLs apply meanwhile."
                    ),
                )
            finally:
                first_attempt.set()
                self._connections.pop(target.name, None)
                if conn is not None and not conn.is_closed():
                    conn.terminate()
            attempt += 1
            await asyncio.sleep(self._backoff_s(attempt))

    async def _watch(self, conn: Any, lost: asyncio.Event) -> None:
        """Return once ``conn`` is lost: terminated, or a liveness probe fails."""
        interval = self._config.health_check_interval_s
        while not lost.is_set():
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(interval):
                    await lost.wait()
                return
            try:
                async with asyncio.timeout(interval):
                    await conn.fetchval("SELECT 1")
            except (TimeoutError, OSError, asyncpg.PostgresError, asyncpg.InterfaceError):
                return

    def _on_notify(
        self, target: PgListenTarget, _conn: Any, _pid: int, _channel: str, payload: str
    ) -> None:
        if not self._running:
            return
        decoded = parse_trigger_payload(payload)
        if decoded is None:
            logger.warning("invalidation_payload_unparseable", target=target.name)
            return
        databases = target.schemas.get(decoded.schema)
        if databases is None:
            return
        for database in databases:
            self._subscribers.dispatch(
                InvalidationMessage(
                    topic=topic_for(database, decoded.table),
                    operation=decoded.operation,
                    keys=decoded.keys,
                    flush=decoded.flush,
                )
            )
