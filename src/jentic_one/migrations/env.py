"""Alembic environment configuration for async migrations.

Supports per-database migrations via named Alembic sections (registry, control, admin).
The active section name determines which database URL and metadata target to use.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from alembic import context
from alembic.runtime.environment import NameFilterParentNames, NameFilterType
from sqlalchemy import MetaData, pool
from sqlalchemy.engine import URL, Connection
from sqlalchemy.ext.asyncio import create_async_engine

from jentic_one.migrations.targets import DB_TARGETS
from jentic_one.shared.config import DatabaseConfig, load_config
from jentic_one.shared.db.backends import get_backend
from jentic_one.shared.db.session import get_database_url

config = context.config


def _on_version_apply(
    ctx: Any,
    step: Any,
    heads: Any,
    run_args: Any,
    **kwargs: Any,
) -> None:
    """Print a line per migration as it is applied.

    Alembic invokes this once for every revision step it runs (the ``step``
    argument is a :class:`~alembic.runtime.migration.MigrationInfo`). It fires
    for both the local ``start-fixtures`` flow (alembic CLI) and the deploy
    migration Job (``python -m jentic_one.migrations.run``), so each reports
    exactly which revisions were applied. When the database is already current
    the hook never fires, so the absence of lines means "nothing to do".
    """
    db = config.config_ini_section
    direction = "upgrade" if step.is_upgrade else "downgrade"
    script = step.up_revision
    revision = script.revision if script is not None else "base"
    filename = os.path.basename(script.path) if script is not None else "?"
    doc = (script.doc or "").strip().splitlines()[0] if script and script.doc else ""
    suffix = f" — {doc}" if doc else ""
    print(f"    [{db}] {direction} {revision} ({filename}){suffix}", flush=True)


def _resolve_db_name() -> str:
    """Determine the target database from the Alembic config section name."""
    section = config.config_ini_section
    if section in DB_TARGETS:
        return section
    return "registry"


def get_url() -> URL | str:
    """Resolve database URL for the active migration target.

    If the active Alembic section provides an explicit ``sqlalchemy.url``
    (used by tests/CI to point at ephemeral databases), it takes precedence
    over the application config file lookup.
    """
    explicit = config.get_section_option(config.config_ini_section, "sqlalchemy.url")
    if explicit:
        return explicit
    app_config = load_config()
    db_name = _resolve_db_name()
    db_config = getattr(app_config.databases, db_name)
    return get_database_url(db_config)


def get_schema() -> str:
    """Resolve the schema name for the active migration target.

    Honours an explicit ``schema_name`` in the active Alembic section
    (used by tests) before falling back to the application config.
    """
    explicit = config.get_section_option(config.config_ini_section, "schema_name")
    if explicit:
        return explicit
    app_config = load_config()
    db_name = _resolve_db_name()
    db_config: DatabaseConfig = getattr(app_config.databases, db_name)
    return db_config.schema_name


def get_dialect_name() -> str:
    """Resolve the SQLAlchemy dialect name for the active migration target.

    Infers the dialect from an explicit ``sqlalchemy.url`` when present
    (tests/CI), otherwise from the configured backend.
    """
    explicit = config.get_section_option(config.config_ini_section, "sqlalchemy.url")
    if explicit:
        return "sqlite" if explicit.startswith("sqlite") else "postgres"
    app_config = load_config()
    db_name = _resolve_db_name()
    db_config: DatabaseConfig = getattr(app_config.databases, db_name)
    return get_backend(db_config).dialect_name


def is_postgres() -> bool:
    """Return True when the active migration target is PostgreSQL."""
    return get_dialect_name() == "postgres"


def get_target_metadata() -> MetaData:
    """Return the metadata for the active migration target."""
    return DB_TARGETS[_resolve_db_name()].metadata


def get_version_table() -> str:
    """Return the ``alembic_version`` table name for the active migration target.

    The built-in targets share the default ``alembic_version`` (scoped per-schema
    by ``version_table_schema``); a target may use a distinct name to avoid a
    version-tracking collision when it shares a schema with another target.
    """
    return DB_TARGETS[_resolve_db_name()].version_table


def _include_name(
    name: str | None, type_: NameFilterType, parent_names: NameFilterParentNames
) -> bool:
    """Restrict reflection/autogenerate to the active migration target's schema.

    All three logical databases share one PostgreSQL instance separated by
    schema. Without this filter, autogenerate sees every schema's tables and
    tries to DROP the ones not present in the active metadata. Limiting the
    reflected schemas to the active one keeps each migration scoped to its own
    database.
    """
    if type_ == "schema":
        return name in (None, get_schema())
    return True


def _include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Default: include everything (each built-in target is single-schema).

    Overridable seam: a downstream ``env.py`` can replace this to treat certain
    schemas as strictly read-only — returning ``False`` for objects whose schema
    it does not own — so autogenerate never emits DROP/ALTER against tables it can
    legitimately see (for cross-schema FK validation) but does not own.
    """
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    postgres = is_postgres()
    context.configure(
        url=url,
        target_metadata=get_target_metadata(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=get_version_table(),
        version_table_schema=get_schema() if postgres else None,
        include_schemas=postgres,
        include_name=_include_name if postgres else None,
        include_object=_include_object,
        render_as_batch=not postgres,
        on_version_apply=_on_version_apply,
        # Each migration owns its own transaction so that migrations using
        # ``op.get_context().autocommit_block()`` (CREATE INDEX CONCURRENTLY,
        # etc.) only commit their own work, never a sibling's.
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    postgres = is_postgres()
    context.configure(
        connection=connection,
        target_metadata=get_target_metadata(),
        version_table=get_version_table(),
        version_table_schema=get_schema() if postgres else None,
        include_schemas=postgres,
        include_name=_include_name if postgres else None,
        include_object=_include_object,
        render_as_batch=not postgres,
        on_version_apply=_on_version_apply,
        # Each migration owns its own transaction so that migrations using
        # ``op.get_context().autocommit_block()`` (CREATE INDEX CONCURRENTLY,
        # etc.) only commit their own work, never a sibling's.
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    url = get_url()
    if is_postgres():
        schema = get_schema()
        connectable = create_async_engine(
            url,
            poolclass=pool.NullPool,
            connect_args={"server_settings": {"search_path": f"{schema},public"}},
        )
    else:
        connectable = create_async_engine(url, poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
