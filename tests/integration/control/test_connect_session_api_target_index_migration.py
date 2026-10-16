"""Upgrade/downgrade scenario for the narrowed open API-target dedupe index.

Drives revision ``dd4e5f6a7b8c`` against the real integration control database
(PostgreSQL by default, SQLite under ``JENTIC_TEST_BACKEND=sqlite``): after the
upgrade a user's open session naming an agent sits beside the agent's own
open ask for the same API, while two agent asks still collide. The downgrade
restores the wider index, keeping the agent's own session of each colliding
group and removing the others with their pending credentials.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text

from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.errors import DatabaseIntegrityError
from jentic_one.shared.db.session import DatabaseSession
from tests.harness.postgres import alembic_config_for

pytestmark = pytest.mark.integration

_PARENT_REV = "cc3d4e5f6a7b"  # pragma: allowlist secret
_AGENT = "agnt_mig_narrow"
_SESSIONS = ("cs_mig_agent", "cs_mig_user", "cs_mig_agent_dup")
_CREDENTIALS = ("cred_mig_agent", "cred_mig_user", "cred_mig_agent_dup")


def _control_cfg(integration_config: AppConfig) -> AlembicConfig:
    return alembic_config_for("control", integration_config.databases.control)


async def _execute(control_db: DatabaseSession, sql: str, **params: object) -> None:
    async with control_db.transaction() as session:
        await session.execute(text(sql), params)


async def _delete_rows(control_db: DatabaseSession) -> None:
    for sid in _SESSIONS:
        await _execute(control_db, "DELETE FROM connect_sessions WHERE id = :id", id=sid)
    for cid in _CREDENTIALS:
        await _execute(control_db, "DELETE FROM credentials WHERE id = :id", id=cid)


@pytest.fixture()
async def restore_control_head(
    integration_config: AppConfig, control_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Leave the control chain at head and the seeded rows gone, whatever happens."""
    await _delete_rows(control_db)
    yield
    await _delete_rows(control_db)
    await asyncio.to_thread(command.upgrade, _control_cfg(integration_config), "head")


async def _open_api_session(
    control_db: DatabaseSession, sid: str, cid: str, *, initiator: str
) -> None:
    await _execute(
        control_db,
        "INSERT INTO credentials (id, type, name, api_vendor, state)"
        " VALUES (:id, 'api_key', 'Example', 'example-com', 'pending')",
        id=cid,
    )
    await _execute(
        control_db,
        "INSERT INTO connect_sessions (id, credential_id, target_kind, vendor, api_name,"
        " api_version, agent_id, initiator_actor_id, state, resolved_flow, poll_token)"
        " VALUES (:id, :cid, 'api', 'example-com', 'example', '1.0.0', :agent,"
        " :initiator, 'created', 'manual_api_key', :token)",
        id=sid,
        cid=cid,
        agent=_AGENT,
        initiator=initiator,
        token=f"digest-{sid}",
    )


async def _ids(control_db: DatabaseSession, table: str, prefix: str) -> set[str]:
    async with control_db.session() as session:
        rows = await session.execute(
            text(f"SELECT id FROM {table} WHERE id LIKE :p"),
            {"p": f"{prefix}%"},
        )
        return {row[0] for row in rows}


async def test_narrowed_index_upgrade_and_downgrade(
    integration_config: AppConfig,
    control_db: DatabaseSession,
    restore_control_head: None,
) -> None:
    cfg = _control_cfg(integration_config)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    await _open_api_session(control_db, "cs_mig_agent", "cred_mig_agent", initiator=_AGENT)
    # A user's session naming the agent no longer collides with the agent's own ask.
    await _open_api_session(control_db, "cs_mig_user", "cred_mig_user", initiator="usr_mig")
    # A second open ask by the agent itself still does.
    with pytest.raises(DatabaseIntegrityError):
        await _open_api_session(
            control_db, "cs_mig_agent_dup", "cred_mig_agent_dup", initiator=_AGENT
        )
    await _execute(control_db, "DELETE FROM credentials WHERE id = 'cred_mig_agent_dup'")

    await asyncio.to_thread(command.downgrade, cfg, _PARENT_REV)
    # The agent's own session survives; the user's duplicate goes with its credential.
    assert await _ids(control_db, "connect_sessions", "cs_mig_") == {"cs_mig_agent"}
    assert await _ids(control_db, "credentials", "cred_mig_") == {"cred_mig_agent"}
    # The wider index is back: any second open session naming the agent collides.
    with pytest.raises(DatabaseIntegrityError):
        await _open_api_session(control_db, "cs_mig_user", "cred_mig_user", initiator="usr_mig")
