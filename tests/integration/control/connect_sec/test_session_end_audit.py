"""Audit: the entry for a session ended on request names the caller who ended it.

``:cancel`` is open to the session's poll-token holder as well as the target
agent's owner and ``org:admin``; ``:reject`` to the approvers only. Either way
the REVOKE entry records who made the request (with its actor type and
origin), and the session's initiator rides in ``after``. A session the
platform ends on its own (TTL expiry) keeps the initiator as the actor, with
``origin=system``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import DEFAULT_AGENT_PERMISSIONS
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType, Origin
from tests.integration.control.connect_sec.support import (
    ADMIN,
    AGENT,
    AGENT_ID,
    FOREIGN_AGENT_ID,
    KEY_SCHEME,
    OTHER_OWNER,
    OTHER_OWNER_ID,
    OWNER,
    OWNER_ID,
    client,
    connect,
    import_spec,
    session_row,
    svc,
)

pytestmark = pytest.mark.integration

_DASHBOARD_OWNER = Identity(
    sub=OWNER_ID, permissions=list(OWNER.permissions), origin=Origin.DASHBOARD
)
_FOREIGN_AGENT = Identity(
    sub=FOREIGN_AGENT_ID,
    permissions=list(DEFAULT_AGENT_PERMISSIONS),
    actor_type=ActorType.AGENT,
    origin=Origin.MCP,
)


async def _revoke_entry(env: Context, session_id: str) -> AuditEntry:
    async with env.admin_db.session() as session:
        return (
            await session.execute(
                select(AuditEntry).where(
                    AuditEntry.target_id == session_id, AuditEntry.action == "revoke"
                )
            )
        ).scalar_one()


def _assert_names_caller(entry: AuditEntry, caller: Identity) -> None:
    assert entry.actor_id == caller.sub
    assert entry.actor_type == caller.actor_type.value
    assert entry.origin == caller.origin.value
    assert entry.after is not None
    assert entry.after["initiator_actor_id"] == AGENT_ID


@pytest.mark.parametrize(
    ("caller", "with_token"),
    [
        pytest.param(_DASHBOARD_OWNER, False, id="owner"),
        pytest.param(ADMIN, False, id="org-admin"),
        pytest.param(AGENT, True, id="initiator-with-token"),
        pytest.param(OTHER_OWNER, True, id="user-holding-the-token"),
        pytest.param(_FOREIGN_AGENT, True, id="agent-holding-the-token"),
    ],
)
async def test_cancel_audit_names_the_caller(
    env: Context, caller: Identity, with_token: bool
) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    params = {"poll_token": created.poll_token} if with_token else {}

    async with client(env, caller) as http:
        response = await http.post(f"/connect-sessions/{created.session_id}:cancel", params=params)

    assert response.status_code == 204, response.text
    assert await session_row(env, created.session_id) is None
    entry = await _revoke_entry(env, created.session_id)
    _assert_names_caller(entry, caller)
    assert entry.reason == "user cancelled"
    assert entry.after is not None and entry.after["error_code"] == "cancelled"


async def test_cancel_by_another_owner_holding_the_token_does_not_name_the_initiator(
    env: Context,
) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)

    await svc(env).cancel_session(
        created.session_id, poll_token=created.poll_token, identity=OTHER_OWNER
    )

    entry = await _revoke_entry(env, created.session_id)
    assert entry.actor_id == OTHER_OWNER_ID
    assert entry.actor_id != AGENT_ID
    assert entry.origin != Origin.SYSTEM.value


@pytest.mark.parametrize("caller", [_DASHBOARD_OWNER, ADMIN], ids=["owner", "org-admin"])
async def test_reject_audit_names_the_approver(env: Context, caller: Identity) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)

    await svc(env).reject_session(created.session_id, identity=caller)

    entry = await _revoke_entry(env, created.session_id)
    _assert_names_caller(entry, caller)
    assert entry.after is not None and entry.after["error_code"] == "rejected"


async def test_expiry_keeps_the_initiator_with_system_origin(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    stale = datetime.now(UTC) - timedelta(days=30)
    async with env.control_db.transaction() as session:
        await session.execute(
            update(ConnectSession)
            .where(ConnectSession.id == created.session_id)
            .values(created_at=stale)
        )

    assert await svc(env).expire_stale_sessions() >= 1

    entry = await _revoke_entry(env, created.session_id)
    assert entry.actor_id == AGENT_ID
    assert entry.actor_type == ActorType.AGENT.value
    assert entry.origin == Origin.SYSTEM.value
    assert entry.after is not None and "initiator_actor_id" not in entry.after
