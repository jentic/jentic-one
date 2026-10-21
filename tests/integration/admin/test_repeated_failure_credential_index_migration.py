"""Upgrade/downgrade scenario for the credential-keyed repeated-failure index.

Drives revision ``c00de2f5d777`` against the real integration admin database
(PostgreSQL by default, SQLite under ``JENTIC_TEST_BACKEND=sqlite``): at head
the partial ``(actor_id, credential_id, operation_id, started_at) WHERE
status = 'failed'`` index exists, matches the ORM declaration, and replaces the
toolkit-keyed one; downgrading restores the toolkit-keyed index and removes the
new one; re-upgrading round-trips back.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import cast

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Table, text

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession
from tests.harness.postgres import alembic_config_for

pytestmark = pytest.mark.integration

_PARENT_REV = "3306fb9172f1"  # pragma: allowlist secret
_NEW_INDEX = "ix_execution_records_repeated_failure_credential_scan"
_OLD_INDEX = "ix_execution_records_repeated_failure_scan"
_NEW_COLUMNS = ("actor_id", "credential_id", "operation_id", "started_at")
_OLD_COLUMNS = ("actor_id", "toolkit_id", "operation_id", "started_at")


def _admin_cfg(integration_config: AppConfig) -> AlembicConfig:
    return alembic_config_for("admin", integration_config.databases.admin)


@pytest.fixture()
async def restore_admin_head(integration_config: AppConfig) -> AsyncGenerator[None, None]:
    """Leave the admin chain at head whatever happens."""
    yield
    await asyncio.to_thread(command.upgrade, _admin_cfg(integration_config), "head")


async def _index_defs(admin_db: DatabaseSession) -> dict[str, str]:
    """Map each ``execution_records`` index name to its normalised DDL."""
    async with admin_db.session() as session:
        if session.get_bind().dialect.name == "postgresql":
            rows = await session.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes"
                    " WHERE schemaname = current_schema() AND tablename = 'execution_records'"
                )
            )
        else:
            rows = await session.execute(
                text(
                    "SELECT name, sql FROM sqlite_master"
                    " WHERE type = 'index' AND tbl_name = 'execution_records' AND sql IS NOT NULL"
                )
            )
        return {name: " ".join(ddl.split()).replace('"', "") for name, ddl in rows}


def _column_list(ddl: str) -> tuple[str, ...]:
    inner = ddl.split("(", 1)[1].split(")", 1)[0]
    return tuple(part.strip() for part in inner.split(","))


def _is_partial_on_failed(ddl: str) -> bool:
    # Postgres renders ``WHERE ((status)::text = 'failed'::text)``; SQLite keeps the source text.
    tail = ddl.split(")", 1)[1].lower()
    return "where" in tail and "status" in tail and "'failed'" in tail


async def test_repeated_failure_credential_index_round_trips(
    integration_config: AppConfig,
    admin_db: DatabaseSession,
    restore_admin_head: None,
) -> None:
    cfg = _admin_cfg(integration_config)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    at_head = await _index_defs(admin_db)
    assert _OLD_INDEX not in at_head
    assert _column_list(at_head[_NEW_INDEX]) == _NEW_COLUMNS
    assert _is_partial_on_failed(at_head[_NEW_INDEX])

    # The ORM declaration agrees with what the migration built.
    orm_indexes = {str(i.name): i for i in cast(Table, ExecutionRecord.__table__).indexes}
    assert tuple(c.name for c in orm_indexes[_NEW_INDEX].columns) == _NEW_COLUMNS
    assert _OLD_INDEX not in orm_indexes

    await asyncio.to_thread(command.downgrade, cfg, _PARENT_REV)
    downgraded = await _index_defs(admin_db)
    assert _NEW_INDEX not in downgraded
    assert _column_list(downgraded[_OLD_INDEX]) == _OLD_COLUMNS
    # The sibling indexes on the table are untouched by the migration.
    assert set(at_head) - {_NEW_INDEX} == set(downgraded) - {_OLD_INDEX}

    await asyncio.to_thread(command.upgrade, cfg, "head")
    assert await _index_defs(admin_db) == at_head
