"""Connection settings and bootstrap helpers for the local Docker Postgres fixture.

Shared by ``tests/integration`` and ``tests/web`` so both harnesses target the
same database. The fixture is the one ``make start-fixtures`` brings up from
``docker/local-setup`` (single ``jentic`` database, one schema and role per
surface). Its host port is read from ``JENTIC_PG_PORT``, the variable that
compose file publishes Postgres on, defaulting to ``5432``.

Under ``pytest-xdist`` every worker gets its own database, so tests that
truncate or count shared tables never race another worker. The first worker
to arrive migrates the ``jentic`` database to head (once per run, behind a
file lock) and each worker then clones it with
``CREATE DATABASE jentic_<worker> TEMPLATE jentic``. A template copy carries
the schemas, tables, grants and ``alembic_version`` rows, so a worker starts
at head without re-running migrations. Without xdist the suite uses ``jentic``
directly. Worker databases are left in place after the run for
debugging; the next run drops and re-clones them.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config as AlembicConfig
from filelock import FileLock
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

# Worker processes that already have their database cloned. Both the
# integration and the web ``_apply_migrations`` fixtures ask for it, and the
# second ask must not drop the database the first one is using.
_prepared_workers: set[str] = set()


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


def xdist_worker() -> str | None:
    """The ``pytest-xdist`` worker id (``gw0``, ``gw1``, …), or ``None`` when not distributed."""
    return os.environ.get("PYTEST_XDIST_WORKER") or None


def pg_database() -> str:
    """Database this process tests against: ``jentic``, or ``jentic_<worker>`` under xdist."""
    worker = xdist_worker()
    return f"{_PG_DATABASE}_{worker}" if worker else _PG_DATABASE


def postgres_config(database: str | None = None) -> AppConfig:
    """AppConfig for the local Docker Postgres fixture (single DB, one schema per surface).

    ``database`` defaults to :func:`pg_database` (this process's database).
    """
    port = pg_port()
    name = database or pg_database()
    return AppConfig.model_validate(
        {
            "databases": {
                surface: {
                    "host": _PG_HOST,
                    "port": port,
                    "name": name,
                    "user": f"{surface}_user",
                    "password": f"{surface}_pass",
                    "pool_max": 2,
                    "schema_name": surface,
                }
                for surface in DB_NAMES
            },
            "credentials": encryption_config(),
        }
    )


def superuser_config(db_config: DatabaseConfig) -> DatabaseConfig:
    """``db_config`` connecting as the fixture's bootstrap superuser."""
    return db_config.model_copy(
        update={"user": _PG_SUPERUSER, "password": SecretStr(_PG_SUPERUSER_PASSWORD)}
    )


def migrate_to_head(config: AppConfig) -> None:
    """Create every surface schema (if missing) and run its migrations to ``head``.

    Blocking: uses ``asyncio.run`` for the schema DDL, so call it from sync
    fixture code that runs before the pytest-asyncio loop exists.
    """

    async def _ensure_all_schemas() -> None:
        for name in DB_NAMES:
            await ensure_schema(getattr(config.databases, name))

    asyncio.run(_ensure_all_schemas())
    for name in DB_NAMES:
        command.upgrade(alembic_config_for(name, getattr(config.databases, name)), "head")


def prepare_xdist_database(lock_dir: Path) -> None:
    """Give this xdist worker its own database cloned from a migrated ``jentic``.

    A no-op outside xdist, and on a second call in the same worker.
    ``lock_dir`` must be shared by every worker of the run (the parent of
    ``tmp_path_factory.getbasetemp()``): it holds the lock that serializes the
    template migration and the clones, plus the marker that the template is
    already at head for this run. ``CREATE DATABASE … TEMPLATE`` refuses while
    any other session is connected to the template, which is why the clones
    are serialized too and why a locally running app on ``jentic`` blocks it.
    """
    worker = xdist_worker()
    if worker is None or worker in _prepared_workers:
        return
    with FileLock(str(lock_dir / "jentic-pg-template.lock")):
        marker = lock_dir / "jentic-pg-template.ready"
        if not marker.exists():
            migrate_to_head(postgres_config(_PG_DATABASE))
            marker.touch()
        asyncio.run(_clone_database(_PG_DATABASE, pg_database()))
    _prepared_workers.add(worker)


async def _clone_database(template: str, target: str) -> None:
    """Drop ``target`` if present and recreate it as a copy of ``template``."""
    maintenance = DatabaseConfig.model_validate(
        {
            "host": _PG_HOST,
            "port": pg_port(),
            "name": "postgres",
            "user": _PG_SUPERUSER,
            "password": _PG_SUPERUSER_PASSWORD,
        }
    )
    # CREATE/DROP DATABASE cannot run inside a transaction block.
    engine = create_async_engine(get_database_url(maintenance), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)'))
            try:
                await conn.execute(text(f'CREATE DATABASE "{target}" TEMPLATE "{template}"'))
            except Exception as exc:
                raise RuntimeError(
                    f"could not clone the {template!r} test database into {target!r}: {exc}. "
                    f"Another session is probably connected to {template!r} (a running app?); "
                    "stop it or run the suite without -n."
                ) from exc
    finally:
        await engine.dispose()


async def ensure_schema(db_config: DatabaseConfig) -> None:
    """Create the target schema if it doesn't already exist.

    Alembic itself does not create schemas; the migration env assumes one
    is already present. Tests using ephemeral Docker volumes need this on
    first boot. Connects as the bootstrap superuser because CREATE SCHEMA
    requires the CREATE privilege on the database.
    """
    engine = create_async_engine(get_database_url(superuser_config(db_config)))
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
