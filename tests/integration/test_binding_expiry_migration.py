"""Upgrade/downgrade of the ``agent_credential_bindings.expires_at`` migration.

Downgrading drops the column, so it first suspends every binding with an end
date: a code rollback must fail closed. A passed one reads ``binding_expired``
(relabelling a binding already suspended), a future one
``binding_policy_removed``. Bindings with no end date are left alone, and
re-upgrading restores the column with no policy backfilled.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import inspect, text

from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession
from tests.harness.postgres import alembic_config_for

pytestmark = pytest.mark.integration

_REVISION = "a3f7c1e9d5b2"  # pragma: allowlist secret
_PARENT = "3ff5fd639357"  # pragma: allowlist secret
_IDS = ("acb_bex_past", "acb_bex_future", "acb_bex_none")


async def _cleanup(db: DatabaseSession) -> None:
    async with db.transaction() as session:
        await session.execute(
            text("DELETE FROM agent_credential_bindings WHERE id LIKE 'acb_bex_%'")
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


async def _has_expires_at(db: DatabaseSession) -> bool:
    async with db.session() as session:
        conn = await session.connection()
        columns = await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("agent_credential_bindings")}
        )
    return "expires_at" in columns


async def test_downgrade_suspends_every_binding_with_an_end_date_and_reupgrade_restores(
    integration_config: AppConfig, admin_db: DatabaseSession, restore_head: None
) -> None:
    cfg = alembic_config_for("admin", integration_config.databases.admin)
    now = datetime.now(UTC)
    rows = {
        "acb_bex_past": (now - timedelta(days=1), False),
        # Still running, but the older code would never end it.
        "acb_bex_future": (now + timedelta(days=1), False),
        "acb_bex_none": (None, False),
        # Suspended for another reason and ended: relabelled, so a resume is knowing.
        "acb_bex_held_past": (now - timedelta(days=1), True),
    }
    async with admin_db.transaction() as session:
        for binding_id, (expires_at, suspended) in rows.items():
            await session.execute(
                text(
                    "INSERT INTO agent_credential_bindings "
                    "(id, agent_id, credential_id, suspended, expires_at) "
                    "VALUES (:id, :agent, :cred, :suspended, :expires_at)"
                ),
                {
                    "id": binding_id,
                    "agent": f"agnt_{binding_id}",
                    "cred": f"cred_{binding_id}",
                    "suspended": suspended,
                    "expires_at": expires_at,
                },
            )

    await asyncio.to_thread(command.downgrade, cfg, _PARENT)
    assert not await _has_expires_at(admin_db)
    async with admin_db.session() as session:
        result = await session.execute(
            text(
                "SELECT id, suspended, suspended_reason FROM agent_credential_bindings "
                "WHERE id LIKE 'acb_bex_%'"
            )
        )
        state = {r[0]: (bool(r[1]), r[2]) for r in result.all()}
    assert state["acb_bex_past"] == (True, "binding_expired")
    assert state["acb_bex_future"] == (True, "binding_policy_removed")
    assert state["acb_bex_none"] == (False, None)
    assert state["acb_bex_held_past"] == (True, "binding_expired")

    await asyncio.to_thread(command.upgrade, cfg, _REVISION)
    assert await _has_expires_at(admin_db)
    async with admin_db.session() as session:
        remaining = (
            await session.execute(
                text(
                    "SELECT count(*) FROM agent_credential_bindings "
                    "WHERE id LIKE 'acb_bex_%' AND expires_at IS NOT NULL"
                )
            )
        ).scalar_one()
    assert remaining == 0
