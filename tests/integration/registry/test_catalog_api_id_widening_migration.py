"""Upgrade/downgrade round trip of the ``catalog_api_id`` widening migrations.

Runs against the integration databases (PostgreSQL by default, SQLite under
``JENTIC_TEST_BACKEND=sqlite``): at head the registry ``apis`` column and the
control ``credentials`` / ``oauth_app_registrations`` columns are 320 wide and
hold a 320-character id; a downgrade narrows them back to 255 and an upgrade
widens them again.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from alembic import command
from sqlalchemy import Connection, inspect, text

from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession
from tests.harness.postgres import alembic_config_for

pytestmark = pytest.mark.integration

_REGISTRY_PARENT = "a0b1c2d3e4f6"  # pragma: allowlist secret
_CONTROL_PARENT = "dd4e5f6a7b8c"  # pragma: allowlist secret
_LONG_ID = "a" * 300 + "/" + "b" * 19
_CREDENTIAL_ID = "cred_widen_mig"
_DELETE = text("DELETE FROM credentials WHERE id = :id")


@pytest.fixture()
async def restore_heads(
    integration_config: AppConfig, control_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    yield
    async with control_db.transaction() as session:
        await session.execute(_DELETE, {"id": _CREDENTIAL_ID})
    for tree, db in (
        ("registry", integration_config.databases.registry),
        ("control", integration_config.databases.control),
    ):
        await asyncio.to_thread(command.upgrade, alembic_config_for(tree, db), "head")


async def _width(db: DatabaseSession, table: str) -> int | None:
    async with db.session() as session:
        conn = await session.connection()

        def _inspect(sync: Connection) -> int | None:
            for col in inspect(sync).get_columns(table):
                if col["name"] == "catalog_api_id":
                    return getattr(col["type"], "length", None)
            raise AssertionError(f"{table}.catalog_api_id missing")

        return await conn.run_sync(_inspect)


async def test_catalog_api_id_widening_round_trip(
    integration_config: AppConfig,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    restore_heads: None,
) -> None:
    registry_cfg = alembic_config_for("registry", integration_config.databases.registry)
    control_cfg = alembic_config_for("control", integration_config.databases.control)

    assert await _width(registry_db, "apis") == 320
    assert await _width(control_db, "credentials") == 320
    assert await _width(control_db, "oauth_app_registrations") == 320

    async with control_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO credentials (id, type, name, api_vendor, state, catalog_api_id)"
                " VALUES (:id, 'api_key', 'Example', 'example-com', 'pending', :cat)"
            ),
            {"id": _CREDENTIAL_ID, "cat": _LONG_ID},
        )
    async with control_db.session() as session:
        stored = (
            await session.execute(
                text("SELECT catalog_api_id FROM credentials WHERE id = :id"),
                {"id": _CREDENTIAL_ID},
            )
        ).scalar_one()
    assert stored == _LONG_ID
    async with control_db.transaction() as session:
        await session.execute(_DELETE, {"id": _CREDENTIAL_ID})

    await asyncio.to_thread(command.downgrade, registry_cfg, _REGISTRY_PARENT)
    await asyncio.to_thread(command.downgrade, control_cfg, _CONTROL_PARENT)
    assert await _width(registry_db, "apis") == 255
    assert await _width(control_db, "credentials") == 255
    assert await _width(control_db, "oauth_app_registrations") == 255

    await asyncio.to_thread(command.upgrade, registry_cfg, "head")
    await asyncio.to_thread(command.upgrade, control_cfg, "head")
    assert await _width(registry_db, "apis") == 320
    assert await _width(control_db, "credentials") == 320
    assert await _width(control_db, "oauth_app_registrations") == 320
