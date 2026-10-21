"""Upgrade/downgrade scenarios for the ``execution_records`` toolkit/credential indexes.

Drives revisions ``c00de2f5d777`` and ``d4b8e2f6a901`` (#1796) against the real
integration admin database (PostgreSQL by default, SQLite under
``JENTIC_TEST_BACKEND=sqlite``):
- ``c00de2f5d777``: creates the partial ``(actor_id, credential_id,
  operation_id, started_at) WHERE status = 'failed'`` index matching the ORM
  declaration and drops ``ix_execution_records_repeated_failure_scan``;
  downgrade restores the toolkit-keyed scan index and removes the new one.
- ``d4b8e2f6a901``: drops ``ix_execution_records_toolkit_started``; downgrade
  recreates it on ``(toolkit_id, started_at)`` (replacing any INVALID leftover
  from a failed concurrent build on PostgreSQL); re-upgrading round-trips back.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import cast

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Table, text
from sqlalchemy.ext.asyncio import create_async_engine

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession, get_database_url
from tests.harness.postgres import alembic_config_for, superuser_config

pytestmark = pytest.mark.integration

_REVISION = "c00de2f5d777"  # pragma: allowlist secret
_PARENT_REV = "3306fb9172f1"  # pragma: allowlist secret
_DROP_TOOLKIT_STARTED_REV = "d4b8e2f6a901"  # pragma: allowlist secret
_DROP_TOOLKIT_STARTED_PARENT_REV = "eb3688e956fc"  # pragma: allowlist secret
_NEW_INDEX = "ix_execution_records_repeated_failure_credential_scan"
_OLD_INDEX = "ix_execution_records_repeated_failure_scan"
_TOOLKIT_STARTED_INDEX = "ix_execution_records_toolkit_started"
_NEW_COLUMNS = ("actor_id", "credential_id", "operation_id", "started_at")
_OLD_COLUMNS = ("actor_id", "toolkit_id", "operation_id", "started_at")
_TOOLKIT_STARTED_COLUMNS = ("toolkit_id", "started_at")


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

    await asyncio.to_thread(command.downgrade, cfg, _REVISION)
    at_rev = await _index_defs(admin_db)

    await asyncio.to_thread(command.downgrade, cfg, _PARENT_REV)
    downgraded = await _index_defs(admin_db)
    assert _NEW_INDEX not in downgraded
    assert _column_list(downgraded[_OLD_INDEX]) == _OLD_COLUMNS
    # The sibling indexes on the table are untouched by the migration.
    assert set(at_rev) - {_NEW_INDEX} == set(downgraded) - {_OLD_INDEX}

    await asyncio.to_thread(command.upgrade, cfg, "head")
    assert await _index_defs(admin_db) == at_head


async def test_drop_toolkit_started_index_round_trips(
    integration_config: AppConfig,
    admin_db: DatabaseSession,
    restore_admin_head: None,
) -> None:
    cfg = _admin_cfg(integration_config)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    at_head = await _index_defs(admin_db)
    assert _TOOLKIT_STARTED_INDEX not in at_head
    orm_indexes = {str(i.name): i for i in cast(Table, ExecutionRecord.__table__).indexes}
    assert _TOOLKIT_STARTED_INDEX not in orm_indexes

    await asyncio.to_thread(command.downgrade, cfg, _DROP_TOOLKIT_STARTED_REV)
    at_drop_rev = await _index_defs(admin_db)
    assert _TOOLKIT_STARTED_INDEX not in at_drop_rev

    await asyncio.to_thread(command.downgrade, cfg, _DROP_TOOLKIT_STARTED_PARENT_REV)
    downgraded = await _index_defs(admin_db)
    assert _column_list(downgraded[_TOOLKIT_STARTED_INDEX]) == _TOOLKIT_STARTED_COLUMNS
    assert "unique" not in downgraded[_TOOLKIT_STARTED_INDEX].lower()
    assert "where" not in downgraded[_TOOLKIT_STARTED_INDEX].lower()
    assert set(downgraded) - {_TOOLKIT_STARTED_INDEX} == set(at_drop_rev)

    await asyncio.to_thread(command.upgrade, cfg, "head")
    assert await _index_defs(admin_db) == at_head


async def test_downgrade_rebuilds_an_invalid_leftover_toolkit_started_index(
    integration_config: AppConfig,
    admin_db: DatabaseSession,
    restore_admin_head: None,
) -> None:
    """A failed ``CONCURRENTLY`` downgrade build leaves an INVALID index; re-running replaces it."""
    async with admin_db.session() as session:
        if session.bind.dialect.name != "postgresql":
            pytest.skip("INVALID indexes exist only on PostgreSQL")
    cfg = _admin_cfg(integration_config)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    await asyncio.to_thread(command.downgrade, cfg, _DROP_TOOLKIT_STARTED_REV)

    # Stand in for a failed concurrent downgrade build: an index of the target
    # name on the wrong column, marked INVALID (only a superuser may flip indisvalid).
    db_config = integration_config.databases.admin
    engine = create_async_engine(get_database_url(superuser_config(db_config)))
    try:
        async with engine.begin() as conn:
            schema = db_config.schema_name
            await conn.execute(
                text(
                    f"CREATE INDEX {_TOOLKIT_STARTED_INDEX}"
                    f' ON "{schema}".execution_records (actor_id)'
                )
            )
            await conn.execute(
                text(
                    "UPDATE pg_index SET indisvalid = false WHERE indexrelid = "
                    f"'\"{schema}\".{_TOOLKIT_STARTED_INDEX}'::regclass"
                )
            )
    finally:
        await engine.dispose()

    await asyncio.to_thread(command.downgrade, cfg, _DROP_TOOLKIT_STARTED_PARENT_REV)

    downgraded = await _index_defs(admin_db)
    assert _column_list(downgraded[_TOOLKIT_STARTED_INDEX]) == _TOOLKIT_STARTED_COLUMNS
    async with admin_db.session() as session:
        valid = await session.scalar(
            text(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid"
                " WHERE c.relname = :name AND c.relnamespace = to_regnamespace(current_schema())"
            ),
            {"name": _TOOLKIT_STARTED_INDEX},
        )
    assert valid is True
