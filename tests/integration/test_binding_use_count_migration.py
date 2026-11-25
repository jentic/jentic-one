"""Upgrade/downgrade of the ``agent_credential_bindings`` use-count migration.

Upgrading gives every existing binding no count (unlimited uses). The
downgrade fails closed: without the columns a binding with a count would allow
unlimited calls, so every one of them is suspended (``binding_expired`` once
used up, else ``binding_policy_removed``) before the columns are dropped.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import inspect, text

from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession
from tests.harness.postgres import alembic_config_for

pytestmark = pytest.mark.integration

_REVISION = "a3f7c1e9d5b2"  # pragma: allowlist secret
_PARENT = "3ff5fd639357"  # pragma: allowlist secret


async def _cleanup(db: DatabaseSession) -> None:
    async with db.transaction() as session:
        await session.execute(
            text("DELETE FROM agent_credential_bindings WHERE id LIKE 'acb_buse_%'")
        )


@pytest.fixture()
async def restore_head(
    integration_config: AppConfig, admin_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    await _cleanup(admin_db)
    yield
    cfg = alembic_config_for("admin", integration_config.databases.admin)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    await _cleanup(admin_db)


async def _columns(db: DatabaseSession) -> set[str]:
    async with db.session() as session:
        conn = await session.connection()
        return await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("agent_credential_bindings")}
        )


async def test_upgrade_gives_existing_bindings_no_count(
    integration_config: AppConfig, admin_db: DatabaseSession, restore_head: None
) -> None:
    cfg = alembic_config_for("admin", integration_config.databases.admin)
    await asyncio.to_thread(command.downgrade, cfg, _PARENT)
    assert "max_quota" not in await _columns(admin_db)
    async with admin_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings "
                "(id, agent_id, credential_id, suspended, bound_at) "
                "VALUES ('acb_buse_old', 'agnt_buse', 'cred_buse', false, :bound)"
            ),
            {"bound": datetime.now(UTC)},
        )

    await asyncio.to_thread(command.upgrade, cfg, _REVISION)

    async with admin_db.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT max_quota, quota_remaining FROM agent_credential_bindings "
                    "WHERE id = 'acb_buse_old'"
                )
            )
        ).one()
    assert tuple(row) == (None, None)


async def test_downgrade_suspends_every_binding_with_a_count(
    integration_config: AppConfig, admin_db: DatabaseSession, restore_head: None
) -> None:
    cfg = alembic_config_for("admin", integration_config.databases.admin)
    rows = {
        "acb_buse_unused": (1, 1),  # a use left: unlimited without the columns
        "acb_buse_used": (1, 0),
        "acb_buse_none": (None, None),
    }
    async with admin_db.transaction() as session:
        for binding_id, (count, left) in rows.items():
            await session.execute(
                text(
                    "INSERT INTO agent_credential_bindings "
                    "(id, agent_id, credential_id, suspended, max_quota, quota_remaining, "
                    "bound_at) VALUES (:id, :agent, 'cred_buse', false, :count, :left, :bound)"
                ),
                {
                    "id": binding_id,
                    "agent": f"agnt_{binding_id}",
                    "count": count,
                    "left": left,
                    "bound": datetime.now(UTC),
                },
            )

    await asyncio.to_thread(command.downgrade, cfg, _PARENT)

    assert not {"max_quota", "quota_remaining"} & await _columns(admin_db)
    async with admin_db.session() as session:
        state = {
            r[0]: (bool(r[1]), r[2])
            for r in (
                await session.execute(
                    text(
                        "SELECT id, suspended, suspended_reason FROM agent_credential_bindings "
                        "WHERE id LIKE 'acb_buse_%'"
                    )
                )
            ).all()
        }
    assert state["acb_buse_unused"] == (True, "binding_policy_removed")
    assert state["acb_buse_used"] == (True, "binding_expired")
    assert state["acb_buse_none"] == (False, None)
