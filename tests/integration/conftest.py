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
import sqlite3
import tempfile
import uuid
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from alembic import command
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession, get_database_url
from tests.harness.event_stream import StreamProbe, install_fast_stream
from tests.harness.postgres import (
    DB_NAMES,
    alembic_config_for,
    encryption_config,
    migrate_to_head,
    postgres_config,
    prepare_xdist_database,
)


def _test_backend() -> str:
    """Backend selected for this test session (``postgres`` or ``sqlite``)."""
    backend = os.environ.get("JENTIC_TEST_BACKEND", "postgres").lower()
    if backend not in ("postgres", "sqlite"):
        raise ValueError(f"JENTIC_TEST_BACKEND must be 'postgres' or 'sqlite', got {backend!r}")
    return backend


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
                for name in DB_NAMES
            },
            # Keep ingest-time search_text projection on, but disable query-time
            # search in the integration harness.
            "search": {"enabled": True, "search_enabled": False},
            "credentials": encryption_config(),
        }
    )


@pytest.fixture(scope="session")
def integration_config() -> Iterator[AppConfig]:
    """AppConfig for the selected backend (Postgres Docker fixture or temp SQLite files)."""
    if _test_backend() == "sqlite":
        with tempfile.TemporaryDirectory(prefix="jentic-itest-sqlite-") as tmp:
            yield _sqlite_config(Path(tmp))
    else:
        yield postgres_config()


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations(
    integration_config: AppConfig, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """Bring every integration database up to ``head`` once per session.

    On Postgres we first give an xdist worker its own database (see
    ``tests/harness/postgres.py``), then ensure the per-surface schemas exist
    (Docker volumes start empty). On SQLite there are no schemas, so we go
    straight to applying migrations against the per-surface database files.
    """
    if _test_backend() == "postgres":
        prepare_xdist_database(tmp_path_factory.getbasetemp().parent)
        migrate_to_head(integration_config)
    else:
        for name in DB_NAMES:
            cfg = alembic_config_for(name, getattr(integration_config.databases, name))
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


@pytest.fixture()
def fast_stream(integration_context: Context, monkeypatch: pytest.MonkeyPatch) -> StreamProbe:
    """``/events/stream`` re-verifying on every page and polling every 50 ms, with a probe."""
    return install_fast_stream(integration_context, monkeypatch)


async def _drop_pg_schemas(superuser_url: Any, schemas: dict[str, str]) -> None:
    engine = create_async_engine(superuser_url)
    try:
        async with engine.begin() as conn:
            for schema in schemas.values():
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    finally:
        await engine.dispose()


async def _pg_current_rev(superuser_url: Any, schema: str) -> str | None:
    engine = create_async_engine(superuser_url)
    try:
        async with engine.connect() as conn:
            has_table = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = :s AND table_name = 'alembic_version'"
                    ),
                    {"s": schema},
                )
            ).first()
            if has_table is None:
                return None
            row = (
                await conn.execute(text(f'SELECT version_num FROM "{schema}".alembic_version'))
            ).first()
            return str(row[0]) if row is not None else None
    finally:
        await engine.dispose()


def _sqlite_current_rev(db_file: Path) -> str | None:
    if not db_file.exists():
        return None
    with sqlite3.connect(db_file) as conn:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        if has_table is None:
            return None
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return str(row[0]) if row is not None else None


@pytest.fixture()
def disposable_migration_stack(
    integration_config: AppConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Any]:
    """Configure ``JENTIC_CONFIG_FILE`` against fresh disposable schemas/files per test."""
    backend = _test_backend()
    cfg_path = tmp_path / "disposable-jentic-one.yaml"

    if backend == "postgres":
        tag = uuid.uuid4().hex[:8]
        schemas = {surface: f"dg1304_{surface}_{tag}" for surface in DB_NAMES}
        base_pg = integration_config.databases.registry
        superuser_cfg = base_pg.model_copy(
            update={"user": "postgres", "password": SecretStr("postgres")}
        )
        superuser_url = get_database_url(superuser_cfg)
        raw_cfg: dict[str, Any] = {
            "databases": {
                surface: {
                    "backend": "postgres",
                    "host": base_pg.host,
                    "port": base_pg.port,
                    "name": base_pg.name,
                    "user": "postgres",
                    "password": "postgres",  # pragma: allowlist secret
                    "schema_name": schemas[surface],
                }
                for surface in DB_NAMES
            },
            "credentials": encryption_config(),
        }
        cfg_path.write_text(yaml.safe_dump(raw_cfg), encoding="utf-8")
        monkeypatch.setenv("JENTIC_CONFIG_FILE", str(cfg_path))
        try:
            yield lambda surface: asyncio.run(_pg_current_rev(superuser_url, schemas[surface]))
        finally:
            asyncio.run(_drop_pg_schemas(superuser_url, schemas))
    else:
        files = {surface: tmp_path / f"{surface}.db" for surface in DB_NAMES}
        raw_cfg = {
            "databases": {
                surface: {
                    "backend": "sqlite",
                    "path": str(files[surface]),
                    "schema_name": surface,
                }
                for surface in DB_NAMES
            },
            "credentials": encryption_config(),
        }
        cfg_path.write_text(yaml.safe_dump(raw_cfg), encoding="utf-8")
        monkeypatch.setenv("JENTIC_CONFIG_FILE", str(cfg_path))
        yield lambda surface: _sqlite_current_rev(files[surface])
