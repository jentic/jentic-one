"""Upgrade/downgrade scenario for the events ``(created_by, actor_id)`` index.

Drives revision ``a1c3e5f7b9d2`` against the real integration admin database
(PostgreSQL by default, SQLite under ``JENTIC_TEST_BACKEND=sqlite``): at head
``ix_events_created_by_actor`` exists, matches the ORM declaration, and
replaces the single-column ``ix_events_created_by``; downgrading restores the
single-column index and removes the new one; re-upgrading round-trips back.
On PostgreSQL, an INVALID index left by a failed concurrent build is rebuilt
by a re-run instead of failing it or being kept.
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

from jentic_one.admin.core.schema.events import Event
from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession, get_database_url
from tests.harness.postgres import alembic_config_for, superuser_config

pytestmark = pytest.mark.integration

_PARENT_REV = "c00de2f5d777"  # pragma: allowlist secret
_NEW_INDEX = "ix_events_created_by_actor"
_OLD_INDEX = "ix_events_created_by"
_NEW_COLUMNS = ("created_by", "actor_id")
_OLD_COLUMNS = ("created_by",)


def _admin_cfg(integration_config: AppConfig) -> AlembicConfig:
    return alembic_config_for("admin", integration_config.databases.admin)


@pytest.fixture()
async def restore_admin_head(integration_config: AppConfig) -> AsyncGenerator[None, None]:
    """Leave the admin chain at head whatever happens."""
    yield
    await asyncio.to_thread(command.upgrade, _admin_cfg(integration_config), "head")


async def _index_defs(admin_db: DatabaseSession) -> dict[str, str]:
    """Map each ``events`` index name to its normalised DDL."""
    async with admin_db.session() as session:
        if session.bind.dialect.name == "postgresql":
            rows = await session.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes"
                    " WHERE schemaname = current_schema() AND tablename = 'events'"
                )
            )
        else:
            rows = await session.execute(
                text(
                    "SELECT name, sql FROM sqlite_master"
                    " WHERE type = 'index' AND tbl_name = 'events' AND sql IS NOT NULL"
                )
            )
        return {name: " ".join(ddl.split()).replace('"', "") for name, ddl in rows}


def _column_list(ddl: str) -> tuple[str, ...]:
    inner = ddl.split("(", 1)[1].split(")", 1)[0]
    return tuple(part.strip() for part in inner.split(","))


async def test_events_created_by_actor_index_round_trips(
    integration_config: AppConfig,
    admin_db: DatabaseSession,
    restore_admin_head: None,
) -> None:
    cfg = _admin_cfg(integration_config)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    at_head = await _index_defs(admin_db)
    assert _OLD_INDEX not in at_head
    assert _column_list(at_head[_NEW_INDEX]) == _NEW_COLUMNS

    # The ORM declaration agrees with what the migration built.
    orm_indexes = {str(i.name): i for i in cast(Table, Event.__table__).indexes}
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


async def test_upgrade_rebuilds_an_invalid_leftover_index(
    integration_config: AppConfig,
    admin_db: DatabaseSession,
    restore_admin_head: None,
) -> None:
    """A failed ``CONCURRENTLY`` build leaves an INVALID index; a re-run replaces it."""
    async with admin_db.session() as session:
        if session.bind.dialect.name != "postgresql":
            pytest.skip("INVALID indexes exist only on PostgreSQL")
    cfg = _admin_cfg(integration_config)
    await asyncio.to_thread(command.downgrade, cfg, _PARENT_REV)

    # Stand in for a failed build: an index of the new name, on the wrong
    # columns, marked INVALID (only a superuser may flip ``indisvalid``).
    db_config = integration_config.databases.admin
    engine = create_async_engine(get_database_url(superuser_config(db_config)))
    try:
        async with engine.begin() as conn:
            schema = db_config.schema_name
            await conn.execute(text(f'CREATE INDEX {_NEW_INDEX} ON "{schema}".events (actor_id)'))
            await conn.execute(
                text(
                    "UPDATE pg_index SET indisvalid = false WHERE indexrelid = "
                    f"'\"{schema}\".{_NEW_INDEX}'::regclass"
                )
            )
    finally:
        await engine.dispose()

    await asyncio.to_thread(command.upgrade, cfg, "head")

    at_head = await _index_defs(admin_db)
    assert _column_list(at_head[_NEW_INDEX]) == _NEW_COLUMNS
    assert _OLD_INDEX not in at_head
    async with admin_db.session() as session:
        valid = await session.scalar(
            text(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid"
                " WHERE c.relname = :name AND c.relnamespace = to_regnamespace(current_schema())"
            ),
            {"name": _NEW_INDEX},
        )
    assert valid is True
