"""Upgrade/downgrade of the ``agent_credential_bindings`` idle-limit migration.

Upgrading backfills ``last_used_at`` from the latest execution record for the
binding's agent and credential (so the idle clock starts from real history)
and ``renewed_at`` from ``bound_at``; no binding gets an idle limit. The
downgrade suspends every binding with an idle limit (``binding_expired`` once
reached, else ``binding_policy_removed``) before dropping the columns, so a
rollback fails closed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import inspect, text

from jentic_one.shared.auth.expiry import as_utc
from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession
from tests.harness.postgres import alembic_config_for

pytestmark = pytest.mark.integration

_REVISION = "a3f7c1e9d5b2"  # pragma: allowlist secret
_PARENT = "3ff5fd639357"  # pragma: allowlist secret


async def _cleanup(db: DatabaseSession) -> None:
    async with db.transaction() as session:
        await session.execute(
            text("DELETE FROM agent_credential_bindings WHERE id LIKE 'acb_bidle_%'")
        )
        await session.execute(text("DELETE FROM execution_records WHERE id LIKE 'exr_bidle_%'"))


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


async def test_upgrade_backfills_the_idle_clock_without_setting_a_limit(
    integration_config: AppConfig, admin_db: DatabaseSession, restore_head: None
) -> None:
    cfg = alembic_config_for("admin", integration_config.databases.admin)
    await asyncio.to_thread(command.downgrade, cfg, _PARENT)
    assert "max_idle_period" not in await _columns(admin_db)

    bound = datetime(2026, 1, 1, tzinfo=UTC)
    used = datetime(2026, 6, 1, tzinfo=UTC)
    async with admin_db.transaction() as session:
        for binding_id, agent in (
            ("acb_bidle_used", "agnt_bidle_u"),
            ("acb_bidle_never", "agnt_bidle_n"),
        ):
            await session.execute(
                text(
                    "INSERT INTO agent_credential_bindings "
                    "(id, agent_id, credential_id, suspended, bound_at) "
                    "VALUES (:id, :agent, 'cred_bidle', false, :bound)"
                ),
                {"id": binding_id, "agent": agent, "bound": bound},
            )
        for record_id, started in (
            ("exr_bidle_1", used - timedelta(days=9)),
            ("exr_bidle_2", used),
        ):
            await session.execute(
                text(
                    "INSERT INTO execution_records "
                    "(id, trace_id, started_at, status, actor_id, actor_type, credential_id) "
                    "VALUES (:id, 'trace', :started, 'success', 'agnt_bidle_u', 'agent', "
                    "'cred_bidle')"
                ),
                {"id": record_id, "started": started},
            )

    await asyncio.to_thread(command.upgrade, cfg, _REVISION)

    async with admin_db.session() as session:
        rows = {
            r[0]: r[1:]
            for r in (
                await session.execute(
                    text(
                        "SELECT id, max_idle_period, last_used_at, renewed_at "
                        "FROM agent_credential_bindings WHERE id LIKE 'acb_bidle_%'"
                    )
                )
            ).all()
        }
    limit, last_used, renewed = rows["acb_bidle_used"]
    assert limit is None
    assert as_utc(last_used) == used
    assert as_utc(renewed) == bound
    limit, last_used, renewed = rows["acb_bidle_never"]
    assert (limit, last_used) == (None, None)
    assert as_utc(renewed) == bound


async def test_downgrade_suspends_every_binding_with_an_idle_limit(
    integration_config: AppConfig, admin_db: DatabaseSession, restore_head: None
) -> None:
    cfg = alembic_config_for("admin", integration_config.databases.admin)
    now = datetime.now(UTC)
    rows = {
        # Unused for longer than its one-day limit: ended, so the rollback suspends it.
        "acb_bidle_idle": (86_400, now - timedelta(days=3)),
        # Used within its limit: still holds, but the older code would never end it.
        "acb_bidle_live": (86_400, now - timedelta(hours=2)),
        # No idle limit at all.
        "acb_bidle_none": (None, now - timedelta(days=90)),
    }
    async with admin_db.transaction() as session:
        for binding_id, (limit, last_used) in rows.items():
            await session.execute(
                text(
                    "INSERT INTO agent_credential_bindings "
                    "(id, agent_id, credential_id, suspended, max_idle_period, last_used_at, "
                    "renewed_at, bound_at) "
                    "VALUES (:id, :agent, 'cred_bidle', false, :limit, :used, :renewed, :bound)"
                ),
                {
                    "id": binding_id,
                    "agent": f"agnt_{binding_id}",
                    "limit": limit,
                    "used": last_used,
                    "renewed": now - timedelta(days=100),
                    "bound": now - timedelta(days=100),
                },
            )

    await asyncio.to_thread(command.downgrade, cfg, _PARENT)

    assert "max_idle_period" not in await _columns(admin_db)
    async with admin_db.session() as session:
        state = {
            r[0]: (bool(r[1]), r[2])
            for r in (
                await session.execute(
                    text(
                        "SELECT id, suspended, suspended_reason FROM agent_credential_bindings "
                        "WHERE id LIKE 'acb_bidle_%'"
                    )
                )
            ).all()
        }
    assert state["acb_bidle_idle"] == (True, "binding_expired")
    assert state["acb_bidle_live"] == (True, "binding_policy_removed")
    assert state["acb_bidle_none"] == (False, None)


async def test_warning_and_after_expiry_default_off_and_drop_on_downgrade(
    integration_config: AppConfig, admin_db: DatabaseSession, restore_head: None
) -> None:
    """Every existing binding gets the warning off and ``after_expiry`` ``hold``."""
    cfg = alembic_config_for("admin", integration_config.databases.admin)
    await asyncio.to_thread(command.downgrade, cfg, _PARENT)
    assert "idle_warning" not in await _columns(admin_db)
    async with admin_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings "
                "(id, agent_id, credential_id, suspended, bound_at) "
                "VALUES ('acb_bidle_warn', 'agnt_bidle_w', 'cred_bidle', false, :bound)"
            ),
            {"bound": datetime.now(UTC)},
        )

    await asyncio.to_thread(command.upgrade, cfg, _REVISION)
    columns = {"idle_warning", "idle_warned_at", "expiry_warned_at", "after_expiry"}
    assert columns <= await _columns(admin_db)
    async with admin_db.session() as session:
        warning, warned_at, after = (
            await session.execute(
                text(
                    "SELECT idle_warning, idle_warned_at, after_expiry "
                    "FROM agent_credential_bindings WHERE id = 'acb_bidle_warn'"
                )
            )
        ).one()
    assert (bool(warning), warned_at, after) == (False, None, "hold")

    await asyncio.to_thread(command.downgrade, cfg, _PARENT)
    assert not columns & await _columns(admin_db)
