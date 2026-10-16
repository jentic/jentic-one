"""Connection settings and bootstrap helpers for the local Docker Postgres fixture.

Shared by ``tests/integration`` and ``tests/web`` so both harnesses target the
same database. The fixture is the one ``make start-fixtures`` brings up from
``docker/local-setup`` (single ``jentic`` database, one schema and role per
surface). Its host port is read from ``JENTIC_PG_PORT``, the variable that
compose file publishes Postgres on, defaulting to ``5432``.
"""

from __future__ import annotations

import os
from typing import Any

from alembic.config import Config as AlembicConfig
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from jentic_one.shared.config import AppConfig, DatabaseConfig
from jentic_one.shared.db.session import get_database_url

DB_NAMES: tuple[str, ...] = ("registry", "admin", "control")

# A deterministic 32-byte AES key (base64). Tests that create credentials need
# a non-empty encryption keyset or the Context boot fails with
# "EncryptionConfig.entries must not be empty".
TEST_ENCRYPTION_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

_PG_HOST = "localhost"
_PG_DATABASE = "jentic"
_PG_SUPERUSER = "postgres"
_PG_SUPERUSER_PASSWORD = "postgres"  # pragma: allowlist secret


def encryption_config() -> dict[str, Any]:
    """``credentials`` config section holding the single deterministic test key."""
    return {
        "encryption": {
            "active_id": "v1",
            "entries": [{"id": "v1", "material": TEST_ENCRYPTION_KEY}],
        },
    }


def pg_port() -> int:
    """Fixture Postgres host port: ``JENTIC_PG_PORT``, as docker/local-setup publishes it."""
    return int(os.environ.get("JENTIC_PG_PORT", "5432"))


def postgres_config() -> AppConfig:
    """AppConfig for the local Docker Postgres fixture (single DB, one schema per surface)."""
    port = pg_port()
    return AppConfig.model_validate(
        {
            "databases": {
                name: {
                    "host": _PG_HOST,
                    "port": port,
                    "name": _PG_DATABASE,
                    "user": f"{name}_user",
                    "password": f"{name}_pass",
                    "pool_max": 2,
                    "schema_name": name,
                }
                for name in DB_NAMES
            },
            "credentials": encryption_config(),
        }
    )


async def ensure_schema(db_config: DatabaseConfig) -> None:
    """Create the target schema if it doesn't already exist.

    Alembic itself does not create schemas; the migration env assumes one
    is already present. Tests using ephemeral Docker volumes need this on
    first boot. Connects as the bootstrap superuser because CREATE SCHEMA
    requires the CREATE privilege on the database.
    """
    superuser_config = db_config.model_copy(
        update={"user": _PG_SUPERUSER, "password": SecretStr(_PG_SUPERUSER_PASSWORD)}
    )
    engine = create_async_engine(get_database_url(superuser_config))
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{db_config.schema_name}"'))
    finally:
        await engine.dispose()


def alembic_config_for(db_name: str, db_config: DatabaseConfig) -> AlembicConfig:
    """Build an in-memory Alembic config bound to ``db_name``.

    Uses the on-disk ``alembic.ini`` for script/version locations and points
    Alembic at the test database via ``sqlalchemy.url``. The migration env
    reads the URL from app config too, but setting it here keeps the Alembic
    config self-contained.
    """
    cfg = AlembicConfig("alembic.ini", ini_section=db_name)
    cfg.set_section_option(
        db_name, "sqlalchemy.url", get_database_url(db_config).render_as_string(hide_password=False)
    )
    cfg.set_section_option(db_name, "schema_name", db_config.schema_name)
    return cfg
