"""Upgrade scenario for the connect-session dedupe-key migration (``cc3d4e5f6a7b``).

Drives the revision against the real integration control database (PostgreSQL
by default, SQLite under ``JENTIC_TEST_BACKEND=sqlite``) holding open duplicate
agent-started OAuth sessions: the newest keeps the key, an older ``created``
duplicate is expired with an outcome row, an older ``polling`` one keeps a NULL
key, and the unique index then refuses a second open session for the same ask.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import text

from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.errors import DatabaseIntegrityError
from jentic_one.shared.db.session import DatabaseSession
from tests.integration.conftest import _alembic_config_for

pytestmark = pytest.mark.integration

_PARENT_REV = "bb2c3d4e5f6a"  # pragma: allowlist secret
_SESSIONS = ("cs_dd_old", "cs_dd_mid", "cs_dd_new", "cs_dd_dup")
_CREDENTIALS = ("cred_dd_old", "cred_dd_mid", "cred_dd_new", "cred_dd_dup")


async def _delete_rows(control_db: DatabaseSession) -> None:
    async with control_db.transaction() as session:
        await session.execute(
            text("DELETE FROM connect_session_outcomes WHERE session_id LIKE 'cs_dd_%'")
        )
        await session.execute(text("DELETE FROM connect_sessions WHERE id LIKE 'cs_dd_%'"))
        await session.execute(text("DELETE FROM credentials WHERE id LIKE 'cred_dd_%'"))


@pytest.fixture()
async def restore_control_head(
    integration_config: AppConfig, control_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    await _delete_rows(control_db)
    yield
    cfg = _alembic_config_for("control", integration_config.databases.control)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    await _delete_rows(control_db)


async def _insert(control_db: DatabaseSession, sql: str, **params: object) -> None:
    async with control_db.transaction() as session:
        await session.execute(text(sql), params)


async def _seed(
    control_db: DatabaseSession, sid: str, cid: str, *, minute: int, state: str = "created"
) -> None:
    await _insert(
        control_db,
        "INSERT INTO credentials (id, type, name, api_vendor, state)"
        " VALUES (:id, 'OAUTH2_AUTHORIZATION_CODE', 'GitHub', 'github-com', 'pending')",
        id=cid,
    )
    await _insert(
        control_db,
        "INSERT INTO connect_sessions (id, credential_id, vendor, agent_id, initiator_actor_id,"
        " state, resolved_flow, poll_token, requested_scopes, created_at)"
        " VALUES (:id, :cid, 'github', 'agnt_dd', 'agnt_dd', :state, 'authorization_code',"
        " :token, :scopes, :created_at)",
        id=sid,
        cid=cid,
        state=state,
        token=f"digest-{sid}",
        scopes='["repo"]',
        created_at=datetime(2026, 10, 1, 10, minute, tzinfo=UTC),
    )


async def test_dedupe_migration_handles_preexisting_duplicates(
    integration_config: AppConfig,
    control_db: DatabaseSession,
    restore_control_head: None,
) -> None:
    cfg = _alembic_config_for("control", integration_config.databases.control)
    await asyncio.to_thread(command.downgrade, cfg, _PARENT_REV)
    await _seed(control_db, "cs_dd_old", "cred_dd_old", minute=0)
    await _seed(control_db, "cs_dd_mid", "cred_dd_mid", minute=5, state="polling")
    await _seed(control_db, "cs_dd_new", "cred_dd_new", minute=10)

    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with control_db.session() as session:
        keys = dict(
            (
                await session.execute(
                    text("SELECT id, dedupe_key FROM connect_sessions WHERE id LIKE 'cs_dd_%'")
                )
            ).all()
        )
        outcomes = (
            await session.execute(
                text(
                    "SELECT session_id, outcome FROM connect_session_outcomes"
                    " WHERE session_id LIKE 'cs_dd_%'"
                )
            )
        ).all()
        credentials = {
            row[0]
            for row in await session.execute(
                text("SELECT id FROM credentials WHERE id LIKE 'cred_dd_%'")
            )
        }
    assert set(keys) == {"cs_dd_mid", "cs_dd_new"}
    assert keys["cs_dd_new"] is not None and keys["cs_dd_mid"] is None
    assert [tuple(r) for r in outcomes] == [("cs_dd_old", "expired")]
    assert credentials == {"cred_dd_mid", "cred_dd_new"}

    await _insert(
        control_db,
        "INSERT INTO credentials (id, type, name, api_vendor, state)"
        " VALUES ('cred_dd_dup', 'OAUTH2_AUTHORIZATION_CODE', 'GitHub', 'github-com', 'pending')",
    )
    with pytest.raises(DatabaseIntegrityError):
        await _insert(
            control_db,
            "INSERT INTO connect_sessions (id, credential_id, vendor, agent_id,"
            " initiator_actor_id, state, resolved_flow, poll_token, dedupe_key)"
            " VALUES ('cs_dd_dup', 'cred_dd_dup', 'github', 'agnt_dd', 'agnt_dd', 'created',"
            " 'authorization_code', 'digest-cs_dd_dup', :key)",
            key=keys["cs_dd_new"],
        )
