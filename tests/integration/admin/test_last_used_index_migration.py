"""Upgrade/downgrade of ``ix_execution_records_actor_credential_last_used``.

Revision ``f6a2c8d4e1b7`` builds the index that answers "last used" per agent,
credential and API from the index alone; the downgrade drops it and
re-upgrading builds it again with the ORM's column order.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from alembic import command
from sqlalchemy import text

from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession
from tests.harness.postgres import alembic_config_for

pytestmark = pytest.mark.integration

_REVISION = "a3f7c1e9d5b2"  # pragma: allowlist secret
_INDEX = "ix_execution_records_actor_credential_last_used"
_COLUMNS = ("actor_id", "credential_id", "api_vendor", "api_name", "started_at")


@pytest.fixture()
async def restore_admin_head(integration_config: AppConfig) -> AsyncGenerator[None, None]:
    yield
    cfg = alembic_config_for("admin", integration_config.databases.admin)
    await asyncio.to_thread(command.upgrade, cfg, "head")


async def _index_columns(admin_db: DatabaseSession) -> tuple[str, ...] | None:
    async with admin_db.session() as session:
        if session.get_bind().dialect.name == "postgresql":
            ddl = (
                await session.execute(
                    text(
                        "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema()"
                        " AND tablename = 'execution_records' AND indexname = :name"
                    ),
                    {"name": _INDEX},
                )
            ).scalar()
        else:
            ddl = (
                await session.execute(
                    text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :name"),
                    {"name": _INDEX},
                )
            ).scalar()
    if ddl is None:
        return None
    inner = ddl.replace('"', "").split("(", 1)[1].rsplit(")", 1)[0]
    return tuple(part.strip() for part in inner.split(","))


async def test_last_used_index_round_trips(
    integration_config: AppConfig, admin_db: DatabaseSession, restore_admin_head: None
) -> None:
    cfg = alembic_config_for("admin", integration_config.databases.admin)
    assert await _index_columns(admin_db) == _COLUMNS

    await asyncio.to_thread(command.downgrade, cfg, f"{_REVISION}-1")
    assert await _index_columns(admin_db) is None

    await asyncio.to_thread(command.upgrade, cfg, _REVISION)
    assert await _index_columns(admin_db) == _COLUMNS
