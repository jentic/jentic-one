"""Migrations bootstrap their own Postgres schema (#992).

Schema creation used to live in a ``docker-entrypoint-initdb.d`` script, which
postgres runs exactly once, on an empty data dir — a mid-init failure left a
volume that silently never got its schemas, and every later migration failed
with ``schema "registry" does not exist``. The migration env now creates the
active target's schema idempotently before migrating, so a migrate against a
schema-less database is self-sufficient and a half-initialized volume heals on
the next run. These tests pin that contract against a real Postgres.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import get_database_url

from .conftest import _alembic_config_for, _test_backend

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        _test_backend() != "postgres",
        reason="schema bootstrap is Postgres-only (SQLite has no schemas)",
    ),
]


def _fresh_schema_config(
    integration_config: AppConfig, schema: str, target: str = "registry"
) -> AlembicConfig:
    """Alembic config for a migration target pointed at an arbitrary schema.

    Connects as the bootstrap superuser: creating a schema needs CREATE on the
    database, which the managed install has (the app connects as the configured
    superuser) but the harness's per-surface users deliberately lack.
    """
    db_config = getattr(integration_config.databases, target).model_copy(
        update={"schema_name": schema, "user": "postgres", "password": SecretStr("postgres")}
    )
    return _alembic_config_for(target, db_config)


def _superuser_url(integration_config: AppConfig) -> URL | str:
    """Bootstrap-superuser URL for the harness database (see conftest._ensure_schema)."""
    db_config = integration_config.databases.registry.model_copy(
        update={"user": "postgres", "password": SecretStr("postgres")}
    )
    return get_database_url(db_config)


async def _schema_tables(integration_config: AppConfig, schema: str) -> set[str]:
    engine = create_async_engine(_superuser_url(integration_config))
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"),
                {"s": schema},
            )
            return {row[0] for row in rows}
    finally:
        await engine.dispose()


async def _drop_schema(integration_config: AppConfig, schema: str) -> None:
    engine = create_async_engine(_superuser_url(integration_config))
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    finally:
        await engine.dispose()


def test_upgrade_creates_missing_schema(integration_config: AppConfig) -> None:
    """``alembic upgrade`` against a database with NO schema must succeed.

    This is the #992 poisoned-volume scenario: the schema bootstrap never ran,
    and before this fix the first migration died with ``schema ... does not
    exist``, unrecoverable without destroying the volume.

    Sync on purpose: the migration env calls ``asyncio.run`` itself, so
    ``command.upgrade`` cannot run inside an async test's event loop.
    """
    schema = f"bootstrap_992_{uuid.uuid4().hex[:8]}"
    try:
        command.upgrade(_fresh_schema_config(integration_config, schema), "head")
        tables = asyncio.run(_schema_tables(integration_config, schema))
        assert "alembic_version" in tables, (
            f"migrations did not stamp the freshly created schema {schema}: {tables}"
        )
        # More than the version table: the registry migrations actually ran.
        assert len(tables) > 1, f"no registry tables created in {schema}: {tables}"
    finally:
        asyncio.run(_drop_schema(integration_config, schema))


def test_upgrade_rejects_hostile_schema_name(integration_config: AppConfig) -> None:
    """A schema name with SQL metacharacters must die at the sink, pre-DDL (SEC-2).

    ``DatabaseConfig`` rejects such names at config-parse time, but the
    Alembic-ini override path (``schema_name`` in the section, exactly what
    ``_fresh_schema_config`` sets) bypasses pydantic — and ``model_copy`` does
    not re-validate either. The migration env's own identifier check is the
    last line of defence before the name is interpolated into a quoted
    ``CREATE SCHEMA`` statement, so it must refuse before any DDL runs.
    """
    hostile = 'evil"; DROP SCHEMA public CASCADE; --'
    with pytest.raises(ValueError, match="invalid schema_name"):
        command.upgrade(_fresh_schema_config(integration_config, hostile), "head")


def test_upgrade_is_idempotent_over_existing_schema(integration_config: AppConfig) -> None:
    """A second upgrade over an already-bootstrapped schema is a clean no-op."""
    schema = f"bootstrap_992_{uuid.uuid4().hex[:8]}"
    try:
        cfg = _fresh_schema_config(integration_config, schema)
        command.upgrade(cfg, "head")
        before = asyncio.run(_schema_tables(integration_config, schema))
        command.upgrade(cfg, "head")
        after = asyncio.run(_schema_tables(integration_config, schema))
        assert before == after
    finally:
        asyncio.run(_drop_schema(integration_config, schema))


async def _create_schema(integration_config: AppConfig, schema: str) -> None:
    engine = create_async_engine(_superuser_url(integration_config))
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    finally:
        await engine.dispose()


def test_upgrade_over_preexisting_schema_supports_autocommit_block(
    integration_config: AppConfig,
) -> None:
    """Full upgrade over a PRE-PROVISIONED (empty) schema must succeed.

    Regression: the env's schema-existence probe autobegins a transaction on
    the connection. When the schema already existed nothing committed it, so
    Alembic saw an externally-managed transaction and any migration using
    ``op.get_context().autocommit_block()`` died on ``assert
    self._transaction is not None``. The control target is used deliberately —
    it is one of the two targets with such a migration (registry has none,
    which is how the original tests missed this).
    """
    schema = f"bootstrap_992_{uuid.uuid4().hex[:8]}"
    try:
        asyncio.run(_create_schema(integration_config, schema))
        command.upgrade(_fresh_schema_config(integration_config, schema, target="control"), "head")
        tables = asyncio.run(_schema_tables(integration_config, schema))
        assert "alembic_version" in tables
        assert len(tables) > 1, f"no control tables created in {schema}: {tables}"
    finally:
        asyncio.run(_drop_schema(integration_config, schema))
