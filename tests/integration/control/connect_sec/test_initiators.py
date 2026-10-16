"""Initiators: a user's session for an agent and the agent's own ask never share a row.

Only an agent's repeat ask is folded into the open session it started itself.
A user naming the agent, and the agent asking after a user did, each get a
session of their own — so neither can rotate the other's poll token, swap the
other's request, or reach a session the other has to approve. And no agent
confirms any session, whoever started it.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.services.integrations.errors import (
    ConfirmationForbiddenError,
    InvalidPollTokenError,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.actors import Origin
from jentic_one.shared.models.events import EventType
from tests.integration.control.connect_sec.support import (
    AGENT,
    AGENT_ID,
    KEY_SCHEME,
    OWNER,
    OWNER_ID,
    RULES,
    TARGET,
    connect,
    digest,
    import_spec,
    key_variant,
    session_row,
    svc,
)

pytestmark = pytest.mark.integration

_USER_RULES: list[dict[str, object]] = [
    {"effect": "allow", "methods": ["POST"], "path": "/items", "match_mode": "exact"}
]
_DASHBOARD_OWNER = Identity(
    sub=OWNER_ID, permissions=list(OWNER.permissions), origin=Origin.DASHBOARD
)


async def _user_asks(identity: Identity, env: Context) -> Any:
    return await svc(env).create_session(
        vendor_key="",
        agent_id=AGENT_ID,
        identity=identity,
        api_target=TARGET,
        requested_permission_rules=_USER_RULES,
        reason="the owner sets it up",
        credential_name="Owner's vault key",
    )


async def _created_events(env: Context) -> set[str]:
    async with env.admin_db.session() as session:
        rows = (
            await session.execute(
                select(Event.data).where(Event.type == EventType.CONNECT_SESSION_CREATED)
            )
        ).scalars()
        return {data["session_id"] for data in rows}


async def _create_audit(env: Context, session_id: str) -> AuditEntry:
    async with env.admin_db.session() as session:
        return (
            await session.execute(
                select(AuditEntry).where(
                    AuditEntry.target_id == session_id, AuditEntry.action == "create"
                )
            )
        ).scalar_one()


async def _credential_name(env: Context, credential_id: str) -> str:
    async with env.control_db.session() as session:
        name = await session.scalar(select(Credential.name).where(Credential.id == credential_id))
    assert name is not None
    return name


async def test_agent_asking_after_its_owner_gets_its_own_session(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    owner_started = await _user_asks(_DASHBOARD_OWNER, env)
    asked = await connect(env)

    assert asked.session_id != owner_started.session_id
    # The owner's token still opens the owner's session; the agent's never does.
    status = await svc(env).get_status(
        owner_started.session_id, poll_token=owner_started.poll_token, identity=OWNER
    )
    assert status.status == "pending"
    with pytest.raises(InvalidPollTokenError):
        await svc(env).get_status(
            owner_started.session_id, poll_token=asked.poll_token, identity=AGENT
        )
    # The owner's session keeps its initiator and request.
    row = await session_row(env, owner_started.session_id)
    assert row is not None
    assert (row.initiator_actor_id, row.requested_permission_rules) == (OWNER_ID, _USER_RULES)
    # The agent's ask is announced on the rail; the owner's own session is not.
    assert await _created_events(env) == {asked.session_id}
    # Asking again still folds into the session the agent started.
    again = await connect(env)
    assert again.session_id == asked.session_id
    assert again.poll_token != asked.poll_token


async def test_user_asking_after_their_agent_gets_their_own_session(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    asked = await connect(env)
    owner_started = await _user_asks(OWNER, env)

    assert owner_started.session_id != asked.session_id
    # The agent keeps polling its own session with its own token.
    status = await svc(env).get_status(
        asked.session_id, poll_token=asked.poll_token, identity=AGENT
    )
    assert status.status == "pending"
    agent_row = await session_row(env, asked.session_id)
    assert agent_row is not None
    assert (agent_row.initiator_actor_id, agent_row.requested_permission_rules) == (
        AGENT_ID,
        RULES,
    )
    # The owner's rules, reason and credential name are theirs, not dropped.
    owner_row = await session_row(env, owner_started.session_id)
    assert owner_row is not None
    assert (owner_row.initiator_actor_id, owner_row.agent_id) == (OWNER_ID, AGENT_ID)
    assert owner_row.requested_permission_rules == _USER_RULES
    assert owner_row.reason == "the owner sets it up"
    assert await _credential_name(env, owner_row.credential_id) == "Owner's vault key"


async def test_create_audit_names_the_initiator_and_its_surface(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    owner_started = await _user_asks(_DASHBOARD_OWNER, env)
    asked = await connect(env)

    by_owner = await _create_audit(env, owner_started.session_id)
    by_agent = await _create_audit(env, asked.session_id)
    assert (by_owner.actor_type, by_owner.actor_id, by_owner.origin) == (
        ActorType.USER.value,
        OWNER_ID,
        Origin.DASHBOARD.value,
    )
    assert (by_agent.actor_type, by_agent.actor_id, by_agent.origin) == (
        ActorType.AGENT.value,
        AGENT_ID,
        AGENT.origin.value,
    )


async def test_an_agent_never_confirms_a_session_its_owner_started(env: Context) -> None:
    """Even holding the owner's poll token, the agent cannot approve its own access."""
    await import_spec(env, KEY_SCHEME)
    owner_started = await _user_asks(OWNER, env)
    reviewed = await digest(env, owner_started.session_id)
    service = svc(env)
    with pytest.raises(ConfirmationForbiddenError):
        await service.confirm_variant(
            owner_started.session_id,
            poll_token=owner_started.poll_token,
            variant=key_variant("sk_cross_" + "B" * 24, reviewed),
            identity=AGENT,
        )
    with pytest.raises(ConfirmationForbiddenError):
        await service.confirm(
            owner_started.session_id,
            poll_token=owner_started.poll_token,
            confirmed_scopes=[],
            permission_rules=_USER_RULES,
            identity=AGENT,
            expected_agent_id=AGENT_ID,
            digest=reviewed,
        )
    review = await service.get_review_data(
        owner_started.session_id, poll_token=owner_started.poll_token, identity=AGENT
    )
    assert review.can_confirm is False
    row = await session_row(env, owner_started.session_id)
    assert row is not None and row.state == "created"
