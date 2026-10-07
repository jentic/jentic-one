"""Integration tests for display names in server-composed event summaries.

The UI renders ``Event.summary`` as-is, so credential and agent events name
the credential (``Credential.name``) and the agent (``Agent.name``) instead of
their ids, and fall back to the id when no name is known. The ids always ride
in the event's ``data``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.users import User
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.schemas.agents import AgentCreatePayload
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.services.credentials.schemas.credentials import CredentialCreate
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.credentials import CredentialType
from jentic_one.shared.models.events import EventType

pytestmark = pytest.mark.integration

_ADMIN = Identity(sub="usr_evtnames_admin", email="admin@test.local", permissions=["org:admin"])
_AGENT_NAME = "evtnames research bot"
_CREDENTIAL_NAME = "evtnames GitHub token"


async def _wipe(ctx: Context) -> None:
    async with ctx.admin_db.session() as session:
        agent_ids = select(Agent.id).where(Agent.name == _AGENT_NAME)
        await session.execute(
            delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id.in_(agent_ids))
        )
        await session.execute(delete(Agent).where(Agent.name == _AGENT_NAME))
        await session.execute(delete(Event).where(Event.actor_id == _ADMIN.sub))
        await session.execute(delete(User).where(User.id == _ADMIN.sub))
        await session.commit()
    async with ctx.control_db.session() as session:
        await session.execute(delete(Credential).where(Credential.created_by == _ADMIN.sub))
        await session.commit()


@pytest.fixture()
async def ctx(integration_context: Context) -> AsyncGenerator[Context, None]:
    await _wipe(integration_context)
    async with integration_context.admin_db.transaction() as session:
        session.add(User(id=_ADMIN.sub, email=_ADMIN.email, first_name="E", last_name="N"))
    yield integration_context
    await _wipe(integration_context)


async def _events(ctx: Context, event_type: str) -> list[Event]:
    async with ctx.admin_db.session() as session:
        rows = await session.execute(
            select(Event).where(Event.type == event_type).where(Event.actor_id == _ADMIN.sub)
        )
        return list(rows.scalars())


async def _store_credential(ctx: Context) -> str:
    created = await CredentialService(ctx).create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name=_CREDENTIAL_NAME,
            api=APIReference(vendor="evtnames.example", name="api", version="v1"),
            token="sk-evtnames-secret",
        ),
        identity=_ADMIN,
    )
    return created.credential_id


async def test_credential_stored_names_the_credential(ctx: Context) -> None:
    credential_id = await _store_credential(ctx)

    [event] = await _events(ctx, EventType.CREDENTIAL_STORED)
    assert event.summary == f"Credential '{_CREDENTIAL_NAME}' stored"
    assert event.data == {"credential_id": credential_id}
    assert "sk-evtnames-secret" not in f"{event.summary} {event.data}"


async def test_agent_created_names_the_agent(ctx: Context) -> None:
    agent = await AgentService(ctx).create(
        AgentCreatePayload(name=_AGENT_NAME), owner_id=_ADMIN.sub, identity=_ADMIN
    )

    [event] = await _events(ctx, EventType.AGENT_CREATED)
    assert event.summary == f"Agent '{_AGENT_NAME}' created"
    assert event.data == {"agent_id": agent.id}


async def test_unbind_falls_back_to_the_credential_id_when_the_row_is_gone(
    ctx: Context,
) -> None:
    """A suspension whose credential row no longer exists names the credential by id."""
    credential_id = await _store_credential(ctx)
    agents = AgentService(ctx)
    agent = await agents.create(
        AgentCreatePayload(name=_AGENT_NAME), owner_id=_ADMIN.sub, identity=_ADMIN
    )
    await agents.bind_credential(agent.id, credential_id=credential_id, identity=_ADMIN)
    async with ctx.control_db.session() as session:
        await session.execute(delete(Credential).where(Credential.id == credential_id))
        await session.commit()

    await agents.unbind_credential(
        agent.id, credential_id=credential_id, purge=False, identity=_ADMIN
    )

    [bound] = await _events(ctx, EventType.CREDENTIAL_BOUND_TO_AGENT)
    assert bound.summary == f"Credential '{_CREDENTIAL_NAME}' bound to agent '{_AGENT_NAME}'"
    [unbound] = await _events(ctx, EventType.CREDENTIAL_UNBOUND_FROM_AGENT)
    assert unbound.summary == f"Credential {credential_id} suspended for agent '{_AGENT_NAME}'"
    assert unbound.data == {"agent_id": agent.id, "credential_id": credential_id}
