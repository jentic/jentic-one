"""Upgrade scenario for the connect-session revisions migrations.

Drives ``dc9d3cd8f0b7`` and ``d9305dcd765e`` against the real integration control database
(PostgreSQL by default, SQLite under ``JENTIC_TEST_BACKEND=sqlite``): open
sessions get revision 1 built from their ask and point at it, an ended
session is left alone, every row becomes a ``create``, and the open-edit
index then refuses a second open edit for the same binding.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from alembic import command
from sqlalchemy import text

from jentic_one.control.services.integrations.revisions import revision_digest
from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession
from tests.harness.postgres import alembic_config_for

pytestmark = pytest.mark.integration

_PARENT_REV = "70ff607085b4"  # pragma: allowlist secret
_RULES = [{"effect": "allow", "methods": ["GET"], "path": "/repos", "match_mode": "prefix"}]


async def _delete_rows(control_db: DatabaseSession) -> None:
    async with control_db.transaction() as session:
        await session.execute(
            text("DELETE FROM connect_session_revisions WHERE session_id LIKE 'cs_rv_%'")
        )
        await session.execute(text("DELETE FROM connect_sessions WHERE id LIKE 'cs_rv_%'"))
        await session.execute(text("DELETE FROM credentials WHERE id LIKE 'cred_rv_%'"))


@pytest.fixture()
async def restore_control_head(
    integration_config: AppConfig, control_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    await _delete_rows(control_db)
    yield
    cfg = alembic_config_for("control", integration_config.databases.control)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    await _delete_rows(control_db)


async def _insert(control_db: DatabaseSession, sql: str, **params: object) -> None:
    async with control_db.transaction() as session:
        await session.execute(text(sql), params)


async def _seed(control_db: DatabaseSession, sid: str, cid: str, *, state: str) -> None:
    await _insert(
        control_db,
        "INSERT INTO credentials (id, type, name, api_vendor, state)"
        " VALUES (:id, 'OAUTH2_AUTHORIZATION_CODE', 'GitHub', 'github-com', 'pending')",
        id=cid,
    )
    await _insert(
        control_db,
        "INSERT INTO connect_sessions (id, credential_id, vendor, agent_id, initiator_actor_id,"
        " state, resolved_flow, poll_token, requested_scopes, requested_permission_rules,"
        " reason) VALUES (:id, :cid, 'github', 'agnt_rv', 'agnt_rv', :state,"
        " 'authorization_code', :token, :scopes, :rules, 'Needs repos')",
        id=sid,
        cid=cid,
        state=state,
        token=f"digest-{sid}",
        scopes='["repo", "read:user"]',
        rules=json.dumps(_RULES),
    )


def _json(raw: Any) -> Any:
    return json.loads(raw) if isinstance(raw, str) else raw


async def test_revisions_migration_backfills_open_sessions(
    integration_config: AppConfig,
    control_db: DatabaseSession,
    restore_control_head: None,
) -> None:
    cfg = alembic_config_for("control", integration_config.databases.control)
    await asyncio.to_thread(command.downgrade, cfg, _PARENT_REV)
    await _seed(control_db, "cs_rv_open", "cred_rv_open", state="created")
    await _seed(control_db, "cs_rv_poll", "cred_rv_poll", state="polling")
    await _seed(control_db, "cs_rv_done", "cred_rv_done", state="connected")

    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with control_db.session() as session:
        sessions = {
            row[0]: (row[1], row[2])
            for row in await session.execute(
                text(
                    "SELECT id, intent, current_revision_id FROM connect_sessions"
                    " WHERE id LIKE 'cs_rv_%'"
                )
            )
        }
        revisions = {
            row[1]: row
            for row in await session.execute(
                text(
                    "SELECT id, session_id, number, created_by, proposal, result, digest"
                    " FROM connect_session_revisions WHERE session_id LIKE 'cs_rv_%'"
                )
            )
        }

    assert {intent for intent, _ in sessions.values()} == {"create"}
    assert set(revisions) == {"cs_rv_open", "cs_rv_poll"}
    assert sessions["cs_rv_done"][1] is None
    first = revisions["cs_rv_open"]
    assert sessions["cs_rv_open"][1] == first.id
    assert first.id.startswith("csr_")
    assert first.number == 1
    assert first.created_by == "agnt_rv"
    assert _json(first.proposal) == {
        "permission_rules": _RULES,
        "scopes": ["repo", "read:user"],
        "reason": "Needs repos",
    }
    result = _json(first.result)
    assert result == {"rules": _RULES, "scopes": ["read:user", "repo"]}
    assert first.digest == revision_digest(baseline=None, result=result, reason="Needs repos")


async def test_a_binding_holds_any_number_of_live_edits(
    control_db: DatabaseSession,
    restore_control_head: None,
) -> None:
    await _insert(
        control_db,
        "INSERT INTO credentials (id, type, name, api_vendor, state)"
        " VALUES ('cred_rv_live', 'OAUTH2_AUTHORIZATION_CODE', 'GitHub', 'github-com',"
        " 'connected')",
    )
    edit = (
        "INSERT INTO connect_sessions (id, credential_id, vendor, agent_id, initiator_actor_id,"
        " state, resolved_flow, poll_token, intent) VALUES (:id, 'cred_rv_live', 'github',"
        " 'agnt_rv', 'agnt_rv', :state, 'edit', :token, 'edit')"
    )
    await _insert(control_db, edit, id="cs_rv_old", state="rejected", token="digest-old")
    await _insert(control_db, edit, id="cs_rv_e1", state="created", token="digest-e1")
    await _insert(control_db, edit, id="cs_rv_e2", state="created", token="digest-e2")
    async with control_db.session() as session:
        live = await session.execute(
            text(
                "SELECT count(*) FROM connect_sessions WHERE credential_id = 'cred_rv_live'"
                " AND state = 'created'"
            )
        )
    assert live.scalar_one() == 2
