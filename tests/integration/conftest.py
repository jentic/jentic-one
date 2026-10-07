"""Integration test fixtures using real databases (PostgreSQL or SQLite).

The session-scoped ``_apply_migrations`` autouse fixture runs Alembic
migrations to ``head`` for every database once per test session, so individual
tests can assume schemas exist and never need to perform DDL themselves.

The backend is selected via the ``JENTIC_TEST_BACKEND`` environment variable
(``postgres`` by default, or ``sqlite``). The PostgreSQL path targets the local
Docker fixture; the SQLite path uses temporary single-file databases (one per
surface) that are created and migrated per session. The same test suite runs
against both backends so we catch dialect drift early.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from jentic_one.shared.config import AppConfig, DatabaseConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession, get_database_url

_DB_NAMES: tuple[str, ...] = ("registry", "admin", "control")
_TEST_ENCRYPTION_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _test_backend() -> str:
    """Backend selected for this test session (``postgres`` or ``sqlite``)."""
    backend = os.environ.get("JENTIC_TEST_BACKEND", "postgres").lower()
    if backend not in ("postgres", "sqlite"):
        raise ValueError(f"JENTIC_TEST_BACKEND must be 'postgres' or 'sqlite', got {backend!r}")
    return backend


def _pg_port() -> int:
    """Fixture Postgres host port: ``JENTIC_PG_PORT``, as docker/local-setup publishes it."""
    return int(os.environ.get("JENTIC_PG_PORT", "5432"))


def _postgres_config() -> AppConfig:
    """AppConfig pointing at the local Docker PostgreSQL instance (single DB, multiple schemas)."""
    return AppConfig.model_validate(
        {
            "databases": {
                "registry": {
                    "host": "localhost",
                    "port": _pg_port(),
                    "name": "jentic",
                    "user": "registry_user",
                    "password": "registry_pass",
                    "pool_max": 2,
                    "schema_name": "registry",
                },
                "admin": {
                    "host": "localhost",
                    "port": _pg_port(),
                    "name": "jentic",
                    "user": "admin_user",
                    "password": "admin_pass",
                    "pool_max": 2,
                    "schema_name": "admin",
                },
                "control": {
                    "host": "localhost",
                    "port": _pg_port(),
                    "name": "jentic",
                    "user": "control_user",
                    "password": "control_pass",
                    "pool_max": 2,
                    "schema_name": "control",
                },
            },
            "credentials": {
                "encryption": {
                    "active_id": "v1",
                    "entries": [{"id": "v1", "material": _TEST_ENCRYPTION_KEY}],
                },
            },
        }
    )


def _sqlite_config(base_dir: Path) -> AppConfig:
    """AppConfig pointing at per-surface SQLite files under ``base_dir``."""
    return AppConfig.model_validate(
        {
            "databases": {
                name: {
                    "backend": "sqlite",
                    "path": str(base_dir / f"{name}.db"),
                    "schema_name": name,
                }
                for name in _DB_NAMES
            },
            # Keep ingest-time search_text projection on, but disable query-time
            # search in the integration harness.
            "search": {"enabled": True, "search_enabled": False},
            "credentials": {
                "encryption": {
                    "active_id": "v1",
                    "entries": [{"id": "v1", "material": _TEST_ENCRYPTION_KEY}],
                },
            },
        }
    )


@pytest.fixture(scope="session")
def integration_config() -> Iterator[AppConfig]:
    """AppConfig for the selected backend (Postgres Docker fixture or temp SQLite files)."""
    if _test_backend() == "sqlite":
        with tempfile.TemporaryDirectory(prefix="jentic-itest-sqlite-") as tmp:
            yield _sqlite_config(Path(tmp))
    else:
        yield _postgres_config()


async def _ensure_schema(db_config: DatabaseConfig) -> None:
    """Create the target schema if it doesn't already exist.

    Alembic itself does not create schemas; the migration env assumes one
    is already present. Tests using ephemeral Docker volumes need this on
    first boot. Connects as the bootstrap superuser because CREATE SCHEMA
    requires the CREATE privilege on the database.
    """
    superuser_config = db_config.model_copy(
        update={"user": "postgres", "password": SecretStr("postgres")}
    )
    url = get_database_url(superuser_config)
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{db_config.schema_name}"'))
    finally:
        await engine.dispose()


def _alembic_config_for(db_name: str, db_config: DatabaseConfig) -> AlembicConfig:
    """Build an in-memory Alembic config bound to ``db_name``.

    Uses the on-disk ``alembic.ini`` for script/version locations and points
    Alembic at the integration database via ``sqlalchemy.url``. The migration
    env reads the URL from app config too, but setting it here keeps the
    Alembic config self-contained.
    """
    cfg = AlembicConfig("alembic.ini", ini_section=db_name)
    cfg.set_section_option(
        db_name, "sqlalchemy.url", get_database_url(db_config).render_as_string(hide_password=False)
    )
    cfg.set_section_option(db_name, "schema_name", db_config.schema_name)
    return cfg


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations(integration_config: AppConfig) -> Iterator[None]:
    """Bring every integration database up to ``head`` once per session.

    On Postgres we first ensure the per-surface schema exists (Docker volumes
    start empty). On SQLite there are no schemas, so we go straight to applying
    migrations against the per-surface database files.
    """
    is_postgres = _test_backend() == "postgres"

    if is_postgres:

        async def _ensure_all_schemas() -> None:
            for name in _DB_NAMES:
                await _ensure_schema(getattr(integration_config.databases, name))

        asyncio.run(_ensure_all_schemas())

    for name in _DB_NAMES:
        cfg = _alembic_config_for(name, getattr(integration_config.databases, name))
        command.upgrade(cfg, "head")
    yield


@pytest.fixture()
async def integration_context(
    integration_config: AppConfig,
) -> AsyncGenerator[Context, None]:
    """Connected ``Context`` shared by integration tests."""
    async with Context(integration_config) as ctx:
        yield ctx


@pytest.fixture()
def registry_db(integration_context: Context) -> DatabaseSession:
    """Connected ``DatabaseSession`` for the registry database."""
    return integration_context.registry_db


@pytest.fixture()
def admin_db(integration_context: Context) -> DatabaseSession:
    """Connected ``DatabaseSession`` for the admin database."""
    return integration_context.admin_db


@pytest.fixture()
def control_db(integration_context: Context) -> DatabaseSession:
    """Connected ``DatabaseSession`` for the control database."""
    return integration_context.control_db
