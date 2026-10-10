"""Upgrade/downgrade scenario for the connect-session target-kind migration.

Drives revision ``bb2c3d4e5f6a`` against the real integration control database
(PostgreSQL by default, SQLite under ``JENTIC_TEST_BACKEND=sqlite``): existing
sessions become ``vendor`` targets, the partial unique index deduplicates only
live ``api`` sessions that name an agent, and the downgrade removes ``api``
targets — with the pending credentials of live ones, never a connected
credential — before dropping the columns and the outcomes table.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect, text

from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.errors import DatabaseIntegrityError
from jentic_one.shared.db.session import DatabaseSession
from tests.integration.conftest import _alembic_config_for

pytestmark = pytest.mark.integration

_PARENT_REV = "aa1b2c3d4e5f"  # pragma: allowlist secret
_SESSION_IDS = (
    "cs_mig_vendor",
    "cs_mig_live",
    "cs_mig_dup",
    "cs_mig_connected",
    "cs_mig_unbound",
)
_CREDENTIAL_IDS = ("cred_mig_vendor", "cred_mig_live", "cred_mig_dup", "cred_mig_connected")
_NEW_COLUMNS = {
    "target_kind",
    "api_name",
    "api_version",
    "scheme_type",
    "scheme_location",
    "scheme_field_name",
    "pinned_hosts",
}


def _control_cfg(integration_config: AppConfig) -> AlembicConfig:
    return _alembic_config_for("control", integration_config.databases.control)


async def _delete_rows(control_db: DatabaseSession) -> None:
    async with control_db.transaction() as session:
        for sid in _SESSION_IDS:
            await session.execute(text("DELETE FROM connect_sessions WHERE id = :id"), {"id": sid})
        for cid in _CREDENTIAL_IDS:
            await session.execute(text("DELETE FROM credentials WHERE id = :id"), {"id": cid})


@pytest.fixture()
async def restore_control_head(
    integration_config: AppConfig, control_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Leave the control chain at head and the seeded rows gone, whatever happens."""
    await _delete_rows(control_db)
    yield
    await asyncio.to_thread(command.upgrade, _control_cfg(integration_config), "head")
    await _delete_rows(control_db)


async def _session_columns(control_db: DatabaseSession) -> set[str]:
    async with control_db.session() as session:
        conn = await session.connection()
        return set(
            await conn.run_sync(
                lambda sync_conn: {
                    c["name"] for c in inspect(sync_conn).get_columns("connect_sessions")
                }
            )
        )


async def _table_names(control_db: DatabaseSession) -> set[str]:
    async with control_db.session() as session:
        conn = await session.connection()
        return set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))


async def _insert(control_db: DatabaseSession, sql: str, **params: object) -> None:
    async with control_db.transaction() as session:
        await session.execute(text(sql), params)


async def _credential(control_db: DatabaseSession, cid: str, state: str) -> None:
    await _insert(
        control_db,
        "INSERT INTO credentials (id, type, name, api_vendor, state)"
        " VALUES (:id, 'api_key', 'Example', 'example-com', :state)",
        id=cid,
        state=state,
    )


async def _api_session(
    control_db: DatabaseSession,
    sid: str,
    cid: str,
    *,
    state: str = "created",
    agent_id: str | None = "agnt_mig",
) -> None:
    await _insert(
        control_db,
        "INSERT INTO connect_sessions (id, credential_id, target_kind, vendor, api_name,"
        " api_version, agent_id, initiator_actor_id, state, resolved_flow, poll_token)"
        " VALUES (:id, :cid, 'api', 'example-com', 'example', '1.0.0', :agent_id,"
        " 'agnt_mig', :state, 'manual_api_key', :token)",
        id=sid,
        cid=cid,
        agent_id=agent_id,
        state=state,
        token=f"digest-{sid}",
    )


async def test_targets_migration_upgrade_and_downgrade(
    integration_config: AppConfig,
    control_db: DatabaseSession,
    restore_control_head: None,
) -> None:
    cfg = _control_cfg(integration_config)
    await asyncio.to_thread(command.downgrade, cfg, _PARENT_REV)
    assert not _NEW_COLUMNS & await _session_columns(control_db)

    # A session that exists before the upgrade.
    await _credential(control_db, "cred_mig_vendor", "pending")
    await _insert(
        control_db,
        "INSERT INTO connect_sessions (id, credential_id, vendor, agent_id,"
        " initiator_actor_id, state, resolved_flow, poll_token)"
        " VALUES ('cs_mig_vendor', 'cred_mig_vendor', 'example-com', 'agnt_mig',"
        " 'agnt_mig', 'created', 'authorization_code', 'digest-cs_mig_vendor')",
    )

    await asyncio.to_thread(command.upgrade, cfg, "head")
    assert await _session_columns(control_db) >= _NEW_COLUMNS
    assert "connect_session_outcomes" in await _table_names(control_db)
    async with control_db.session() as session:
        kind = (
            await session.execute(
                text("SELECT target_kind FROM connect_sessions WHERE id = 'cs_mig_vendor'")
            )
        ).scalar_one()
    assert kind == "vendor"

    # One live API session per agent and identity; ended or agent-less ones never collide.
    await _credential(control_db, "cred_mig_live", "pending")
    await _api_session(control_db, "cs_mig_live", "cred_mig_live")
    await _credential(control_db, "cred_mig_dup", "pending")
    with pytest.raises(DatabaseIntegrityError):
        await _api_session(control_db, "cs_mig_dup", "cred_mig_dup", state="awaiting_app")
    await _credential(control_db, "cred_mig_connected", "connected")
    await _api_session(control_db, "cs_mig_connected", "cred_mig_connected", state="connected")
    await _api_session(control_db, "cs_mig_unbound", "cred_mig_dup", agent_id=None)

    await asyncio.to_thread(command.downgrade, cfg, _PARENT_REV)
    assert not _NEW_COLUMNS & await _session_columns(control_db)
    assert "connect_session_outcomes" not in await _table_names(control_db)
    async with control_db.session() as session:
        sessions = {
            row[0]
            for row in await session.execute(
                text("SELECT id FROM connect_sessions WHERE id LIKE 'cs_mig_%'")
            )
        }
        credentials = {
            row[0]
            for row in await session.execute(
                text("SELECT id FROM credentials WHERE id LIKE 'cred_mig_%'")
            )
        }
    assert sessions == {"cs_mig_vendor"}
    assert credentials == {"cred_mig_vendor", "cred_mig_connected"}
