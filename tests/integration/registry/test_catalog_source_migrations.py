"""Upgrade/downgrade round trip of the multi-source catalog migrations.

Runs against the integration databases (PostgreSQL by default, SQLite under
``JENTIC_TEST_BACKEND=sqlite``): downgrading below the catalog-source revisions
drops the four tables and ``apis.catalog_entry_id``; upgrading again restores
them, and ``catalog_snapshots`` is untouched either way.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, inspect

from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession
from tests.harness.postgres import alembic_config_for

pytestmark = pytest.mark.integration

_PARENT = "e8f9a0b1c2d3"  # pragma: allowlist secret
_TABLES_REV = "f9a0b1c2d3e4"  # pragma: allowlist secret
_ENTRY_ID_REV = "a0b1c2d3e4f6"  # pragma: allowlist secret
_NEW_TABLES = {"catalog_sources", "catalog_entries", "catalog_staged_entries", "catalog_sync_runs"}


@pytest.fixture()
async def restore_head(integration_config: AppConfig) -> AsyncGenerator[None, None]:
    yield
    cfg = alembic_config_for("registry", integration_config.databases.registry)
    await asyncio.to_thread(command.upgrade, cfg, "head")


async def _shape(db: DatabaseSession) -> tuple[set[str], set[str]]:
    async with db.session() as session:
        conn = await session.connection()

        def _inspect(sync: Connection) -> tuple[set[str], set[str]]:
            insp = inspect(sync)
            tables = set(insp.get_table_names())
            cols = {c["name"] for c in insp.get_columns("apis")}
            return tables, cols

        return await conn.run_sync(_inspect)


def test_registry_tree_has_one_head_with_catalog_sources_in_lineage(
    integration_config: AppConfig,
) -> None:
    cfg = alembic_config_for("registry", integration_config.databases.registry)
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1
    lineage = {rev.revision for rev in script.iterate_revisions(heads[0], "base")}
    assert {_TABLES_REV, _ENTRY_ID_REV} <= lineage
    assert script.get_revision(_TABLES_REV).down_revision == _PARENT
    assert script.get_revision(_ENTRY_ID_REV).down_revision == _TABLES_REV


async def test_catalog_source_migrations_round_trip(
    integration_config: AppConfig, registry_db: DatabaseSession, restore_head: None
) -> None:
    cfg = alembic_config_for("registry", integration_config.databases.registry)
    tables, cols = await _shape(registry_db)
    assert tables >= _NEW_TABLES
    assert "catalog_entry_id" in cols

    await asyncio.to_thread(command.downgrade, cfg, _PARENT)
    tables, cols = await _shape(registry_db)
    assert not (_NEW_TABLES & tables)
    assert "catalog_entry_id" not in cols
    assert "catalog_snapshots" in tables

    await asyncio.to_thread(command.upgrade, cfg, "head")
    tables, cols = await _shape(registry_db)
    assert tables >= _NEW_TABLES
    assert "catalog_entry_id" in cols
    assert "catalog_snapshots" in tables
