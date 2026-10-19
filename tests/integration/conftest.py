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

import os
import tempfile
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path

import pytest
from alembic import command

from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
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
