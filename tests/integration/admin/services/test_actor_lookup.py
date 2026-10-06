"""Integration tests for resolving actors by id (``ActorService.lookup``)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentRepository, UserRepository
from jentic_one.admin.services.actor_service import ActorService
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType, InviteState

pytestmark = pytest.mark.integration


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


async def test_lookup_resolves_users_and_agents(integration_context: Context, seed: _Seed) -> None:
    views = await ActorService(integration_context).lookup(
        [seed.user_id, seed.active_agent_id, seed.pending_agent_id]
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


async def test_lookup_omits_unknown_and_collapses_duplicates(
    integration_context: Context, seed: _Seed
) -> None:
    views = await ActorService(integration_context).lookup(
        [seed.user_id, "usr_does_not_exist", seed.user_id, "cred_not_an_actor"]
    )
    assert [v.id for v in views] == [seed.user_id]


async def test_lookup_of_no_ids_is_empty(integration_context: Context) -> None:
    assert await ActorService(integration_context).lookup([]) == []
