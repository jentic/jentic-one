"""Build the process's default ``InvalidationBus`` from its ``Context``."""

from __future__ import annotations

from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.backends import get_backend
from jentic_one.shared.invalidation.memory import NoopInvalidationBus
from jentic_one.shared.invalidation.postgres import PgListenInvalidationBus, listen_targets
from jentic_one.shared.invalidation.protocol import InvalidationBus

#: Logical databases whose tables carry invalidation triggers.
INVALIDATION_DATABASES: tuple[str, ...] = ("admin", "control")


def build_invalidation_bus(ctx: Context) -> InvalidationBus:
    """The default bus: ``LISTEN`` on PostgreSQL, a no-op otherwise.

    Only the databases this process may open (``Context.allowed_dbs``) and that
    run on PostgreSQL are listened on. The bus opens nothing until it has a
    subscriber, so building it on a shape that caches nothing is free.
    """
    if not ctx.config.invalidation.enabled:
        return NoopInvalidationBus()
    pg: dict[str, DatabaseConfig] = {}
    for name in INVALIDATION_DATABASES:
        if not ctx.is_db_allowed(name):
            continue
        cfg: DatabaseConfig = getattr(ctx.config.databases, name)
        if get_backend(cfg).dialect_name == "postgres":
            pg[name] = cfg
    if not pg:
        return NoopInvalidationBus()
    return PgListenInvalidationBus(listen_targets(pg), config=ctx.config.invalidation)
