"""Integration tests for resolving actors by id (``ActorService.lookup``).

Agents resolve only when the caller may see them (the admin agent scoping
filter); users resolve for any caller.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentRepository, UserRepository
from jentic_one.admin.services.actor_service import ActorService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import OWNER_AGENTS_READ
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType, InviteState

pytestmark = pytest.mark.integration

_ADMIN = Identity(sub="usr_lookup_admin", email="a@test.local", permissions=["org:admin"])
_OUTSIDER = Identity(sub="usr_lookup_outsider", email="o@test.local", permissions=["agents:read"])


def _owner(user_id: str) -> Identity:
    return Identity(sub=user_id, email="ada@test.local", permissions=["agents:read"])


def _agent(agent_id: str, owner_id: str, *permissions: str) -> Identity:
    return Identity(
        sub=agent_id,
        permissions=list(permissions),
        actor_type=ActorType.AGENT,
        parent_actor_id=owner_id,
    )


@dataclass(frozen=True)
class _Seed:
    user_id: str
    active_agent_id: str
    pending_agent_id: str


@pytest.fixture()
async def seed(integration_context: Context) -> AsyncGenerator[_Seed, None]:
    """One user owning one active and one pending agent."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="actor-lookup@test.local",
            first_name="Ada",
            last_name="Lovelace",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        active = await AgentRepository.create(
            session,
            name="lookup-active",
            owner_id=user.id,
            registered_by=user.id,
            created_by=user.id,
            status=ActorStatus.ACTIVE,
        )
        pending = await AgentRepository.create(
            session,
            name="lookup-pending",
            owner_id=user.id,
            registered_by=user.id,
            created_by=user.id,
            status=ActorStatus.PENDING,
        )
        await session.commit()
        seeded = _Seed(user_id=user.id, active_agent_id=active.id, pending_agent_id=pending.id)

    yield seeded

    async with ctx.admin_db.session() as session:
        await session.execute(
            delete(Agent).where(Agent.id.in_([seeded.active_agent_id, seeded.pending_agent_id]))
        )
        await session.execute(delete(User).where(User.id == seeded.user_id))
        await session.commit()


async def test_admin_resolves_users_and_agents(integration_context: Context, seed: _Seed) -> None:
    views = await ActorService(integration_context).lookup(
        [seed.user_id, seed.active_agent_id, seed.pending_agent_id], identity=_ADMIN
    )
    by_id = {v.id: v for v in views}
    assert set(by_id) == {seed.user_id, seed.active_agent_id, seed.pending_agent_id}
    assert by_id[seed.user_id].actor_type == ActorType.USER
    assert by_id[seed.user_id].name == "Ada Lovelace"
    assert by_id[seed.user_id].active is True
    assert by_id[seed.active_agent_id].actor_type == ActorType.AGENT
    assert by_id[seed.active_agent_id].name == "lookup-active"
    assert by_id[seed.active_agent_id].active is True
    assert by_id[seed.pending_agent_id].active is False


async def test_owner_resolves_own_agents(integration_context: Context, seed: _Seed) -> None:
    views = await ActorService(integration_context).lookup(
        [seed.user_id, seed.active_agent_id, seed.pending_agent_id], identity=_owner(seed.user_id)
    )
    assert {v.id for v in views} == {seed.user_id, seed.active_agent_id, seed.pending_agent_id}


async def test_outsider_resolves_user_names_but_not_others_agents(
    integration_context: Context, seed: _Seed
) -> None:
    views = await ActorService(integration_context).lookup(
        [seed.user_id, seed.active_agent_id, seed.pending_agent_id], identity=_OUTSIDER
    )
    assert [(v.id, v.name) for v in views] == [(seed.user_id, "Ada Lovelace")]


async def test_agent_resolves_itself_and_users_only(
    integration_context: Context, seed: _Seed
) -> None:
    identity = _agent(seed.active_agent_id, seed.user_id, "agents:read")
    views = await ActorService(integration_context).lookup(
        [seed.user_id, seed.active_agent_id, seed.pending_agent_id], identity=identity
    )
    assert {v.id for v in views} == {seed.user_id, seed.active_agent_id}


async def test_agent_with_owner_scope_resolves_owners_agents(
    integration_context: Context, seed: _Seed
) -> None:
    identity = _agent(seed.active_agent_id, seed.user_id, "agents:read", OWNER_AGENTS_READ)
    views = await ActorService(integration_context).lookup(
        [seed.pending_agent_id], identity=identity
    )
    assert [v.id for v in views] == [seed.pending_agent_id]


async def test_lookup_omits_unknown_and_collapses_duplicates(
    integration_context: Context, seed: _Seed
) -> None:
    views = await ActorService(integration_context).lookup(
        [seed.user_id, "usr_does_not_exist", seed.user_id, "cred_not_an_actor"], identity=_ADMIN
    )
    assert [v.id for v in views] == [seed.user_id]


async def test_lookup_of_no_ids_is_empty(integration_context: Context) -> None:
    assert await ActorService(integration_context).lookup([], identity=_ADMIN) == []
