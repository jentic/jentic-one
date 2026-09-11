"""Scenario tests for the theme-5 Phase 6b drop migrations (gate conditions).

The drop migrations are guard-and-raise: they must refuse to run — loudly,
with operator remediation — when the legacy tables still hold rows and the
Phase-6a runbook has not been completed, and proceed when either the tables
are empty (fresh installs) or the acknowledgement/proxy evidence is present.
These tests drive Alembic programmatically against the real integration
databases, downgrading below the drops and re-upgrading under each gate
condition. (The empty-DB path is exercised implicitly by the session-scoped
``_apply_migrations`` fixture on every run, and the downgrade+re-import drill
lives in ``test_toolkit_export.py``.)
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import AsyncGenerator

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect, text

from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession
from tests.integration.conftest import _alembic_config_for

pytestmark = pytest.mark.integration

#: Revisions just below the theme-5 Phase 6b drop migrations.
_CONTROL_PRE_DROP = "u2c3d4e5f6a7"  # pragma: allowlist secret
_ADMIN_PRE_DROP = "c0e1f2a3b4c5"  # pragma: allowlist secret

_CONTROL_LEGACY_TABLES = (
    "toolkit_permission_rules",
    "toolkit_keys",
    "toolkit_credential_bindings",
    "toolkits",
)


def _control_cfg(integration_config: AppConfig) -> AlembicConfig:
    return _alembic_config_for("control", integration_config.databases.control)


def _admin_cfg(integration_config: AppConfig) -> AlembicConfig:
    return _alembic_config_for("admin", integration_config.databases.admin)


async def _table_names(db: DatabaseSession) -> set[str]:
    async with db.session() as session:
        conn = await session.connection()
        return set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))


@pytest.fixture()
async def restore_control_head(
    integration_config: AppConfig, control_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Whatever a test does to the control chain, leave it at head and clean."""
    yield
    async with control_db.session() as session:
        names = await _table_names(control_db)
        for table in (*_CONTROL_LEGACY_TABLES, "toolkit_flattening_acks"):
            if table in names:
                await session.execute(text(f"DELETE FROM {table}"))
        await session.commit()
    await asyncio.to_thread(command.upgrade, _control_cfg(integration_config), "head")


@pytest.fixture()
async def restore_admin_head(
    integration_config: AppConfig, admin_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Whatever a test does to the admin chain, leave it at head and clean."""
    yield
    async with admin_db.session() as session:
        names = await _table_names(admin_db)
        if "agent_toolkit_bindings" in names:
            await session.execute(text("DELETE FROM agent_toolkit_bindings"))
        await session.execute(
            text("DELETE FROM agent_credential_bindings WHERE agent_id LIKE 'agnt_6btest%'")
        )
        await session.commit()
    await asyncio.to_thread(command.upgrade, _admin_cfg(integration_config), "head")


async def test_control_drop_blocks_rows_without_ack_then_accepts_ack(
    integration_config: AppConfig,
    control_db: DatabaseSession,
    restore_control_head: None,
) -> None:
    """Rows + no ack → raise with the runbook; ack row → the drop proceeds."""
    cfg = _control_cfg(integration_config)
    await asyncio.to_thread(command.downgrade, cfg, _CONTROL_PRE_DROP)
    async with control_db.session() as session:
        await session.execute(
            text("INSERT INTO toolkits (id, name) VALUES ('tk_6btest_1', '6b-gate-toolkit')")
        )
        await session.commit()

    with pytest.raises(Exception, match=r"flatten-toolkits --verify\s+--acknowledge|--acknowledge"):
        await asyncio.to_thread(command.upgrade, cfg, "head")

    # The failed run left the chain below the drop; the tables survive intact.
    names = await _table_names(control_db)
    assert set(_CONTROL_LEGACY_TABLES) <= names

    # Record the acknowledgement (what `flatten-toolkits --verify --acknowledge`
    # writes) and the same upgrade proceeds — rows and all.
    async with control_db.session() as session:
        acknowledged_at: object = dt.datetime(2026, 9, 11, tzinfo=dt.UTC)
        if control_db.backend.dialect_name == "sqlite":
            acknowledged_at = "2026-09-11 00:00:00.000000"
        await session.execute(
            text(
                "INSERT INTO toolkit_flattening_acks"
                " (id, acknowledged_at, legacy_pair_count, direct_binding_count,"
                "  report_finding_count, tool_version, created_by)"
                " VALUES ('tfa_6btest_1', :ts, 1, 1, 0, 'test', 'system:test')"
            ),
            {"ts": acknowledged_at},
        )
        await session.commit()
    await asyncio.to_thread(command.upgrade, cfg, "head")

    names = await _table_names(control_db)
    assert not (set(_CONTROL_LEGACY_TABLES) & names)
    # The sentinel table itself survives the drop (it is the audit trail).
    assert "toolkit_flattening_acks" in names


async def test_admin_drop_blocks_legacy_rows_without_direct_evidence(
    integration_config: AppConfig,
    admin_db: DatabaseSession,
    restore_admin_head: None,
) -> None:
    """Legacy bindings + zero direct bindings → raise; one direct binding → drop."""
    cfg = _admin_cfg(integration_config)
    await asyncio.to_thread(command.downgrade, cfg, _ADMIN_PRE_DROP)
    async with admin_db.session() as session:
        # Zero direct bindings is the "flattening clearly has not run" state —
        # empty the table (test isolation: other suites clean up after
        # themselves, so anything here is leakage anyway).
        await session.execute(text("DELETE FROM agent_credential_bindings"))
        await session.execute(
            text(
                "INSERT INTO agent_toolkit_bindings (id, agent_id, toolkit_id)"
                " VALUES ('atb_6btest_1', 'agnt_6btest_1', 'tk_6btest_1')"
            )
        )
        await session.commit()

    with pytest.raises(Exception, match="flatten-toolkits"):
        await asyncio.to_thread(command.upgrade, cfg, "head")
    names = await _table_names(admin_db)
    assert "agent_toolkit_bindings" in names

    # A single direct binding is the in-DB evidence the flattening ran.
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings (id, agent_id, credential_id)"
                " VALUES ('acb_6btest_1', 'agnt_6btest_1', 'cred_6btest_1')"
            )
        )
        await session.commit()
    await asyncio.to_thread(command.upgrade, cfg, "head")

    names = await _table_names(admin_db)
    assert "agent_toolkit_bindings" not in names
