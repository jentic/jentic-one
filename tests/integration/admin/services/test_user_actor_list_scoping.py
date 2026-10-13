"""Integration tests for caller scoping of the user and actor-directory listings.

``GET /users``, ``GET /users/{id}`` and ``GET /actors`` enumerate the org, so a
non-admin reads only themselves (and, for ``/actors``, the agents it may see).
``org:admin`` reads everyone. An out-of-scope single user read is a 404.
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
from jentic_one.admin.services.errors import UserNotFoundError
from jentic_one.admin.services.user_service import UserService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import OWNER_AGENTS_READ
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType, InviteState

pytestmark = pytest.mark.integration

_ADMIN = Identity(sub="usr_listscope_admin", email="a@test.local", permissions=["org:admin"])


@dataclass(frozen=True)
class _Seed:
    owner_id: str
    other_id: str
    agent_id: str
    other_agent_id: str


@pytest.fixture()
async def seed(integration_context: Context) -> AsyncGenerator[_Seed, None]:
    """Two users, each owning one agent."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        owner = await UserRepository.create(
            session,
            email="listscope-owner@test.local",
            first_name="List",
            last_name="Owner",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        other = await UserRepository.create(
            session,
            email="listscope-other@test.local",
            first_name="List",
            last_name="Other",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        agent = await AgentRepository.create(
            session,
            name="listscope-agent",
            owner_id=owner.id,
            registered_by=owner.id,
            created_by=owner.id,
            status=ActorStatus.ACTIVE,
        )
        other_agent = await AgentRepository.create(
            session,
            name="listscope-other-agent",
            owner_id=other.id,
            registered_by=other.id,
            created_by=other.id,
            status=ActorStatus.ACTIVE,
        )
        await session.commit()
        seeded = _Seed(
            owner_id=owner.id,
            other_id=other.id,
            agent_id=agent.id,
            other_agent_id=other_agent.id,
        )

    yield seeded

    async with ctx.admin_db.session() as session:
        await session.execute(
            delete(Agent).where(Agent.id.in_([seeded.agent_id, seeded.other_agent_id]))
        )
        await session.execute(delete(User).where(User.id.in_([seeded.owner_id, seeded.other_id])))
        await session.commit()


def _member(user_id: str) -> Identity:
    return Identity(sub=user_id, email="m@test.local", permissions=["users:read"])


def _agent(agent_id: str, owner_id: str, *permissions: str) -> Identity:
    return Identity(
        sub=agent_id,
        permissions=["users:read", *permissions],
        actor_type=ActorType.AGENT,
        parent_actor_id=owner_id,
    )


async def _listed_user_ids(ctx: Context, identity: Identity) -> set[str]:
    page = await UserService(ctx).list_all(limit=200, identity=identity)
    return {u.id for u in page.data}


async def _listed_actor_ids(ctx: Context, identity: Identity) -> set[str]:
    page = await ActorService(ctx).list_all(limit=5000, identity=identity)
    return {a.id for a in page.data}


# --- users ---------------------------------------------------------------------


async def test_member_lists_and_reads_only_themselves(
    integration_context: Context, seed: _Seed
) -> None:
    ctx = integration_context
    member = _member(seed.owner_id)

    assert await _listed_user_ids(ctx, member) == {seed.owner_id}
    assert (await UserService(ctx).get(seed.owner_id, identity=member)).id == seed.owner_id
    with pytest.raises(UserNotFoundError):
        await UserService(ctx).get(seed.other_id, identity=member)


async def test_agent_lists_no_users(integration_context: Context, seed: _Seed) -> None:
    """User scoping is self-only with no delegation: an agent is never a user row."""
    agent = _agent(seed.agent_id, seed.owner_id, OWNER_AGENTS_READ)
    assert await _listed_user_ids(integration_context, agent) == set()
    with pytest.raises(UserNotFoundError):
        await UserService(integration_context).get(seed.owner_id, identity=agent)


async def test_admin_lists_and_reads_every_user(integration_context: Context, seed: _Seed) -> None:
    ctx = integration_context
    assert {seed.owner_id, seed.other_id} <= await _listed_user_ids(ctx, _ADMIN)
    assert (await UserService(ctx).get(seed.other_id, identity=_ADMIN)).id == seed.other_id


# --- actor directory -----------------------------------------------------------


async def test_member_lists_self_and_own_agents(integration_context: Context, seed: _Seed) -> None:
    listed = await _listed_actor_ids(integration_context, _member(seed.owner_id))
    assert {seed.owner_id, seed.agent_id} <= listed
    assert seed.other_id not in listed
    assert seed.other_agent_id not in listed


async def test_delegated_agent_lists_itself_and_owners_agents(
    integration_context: Context, seed: _Seed
) -> None:
    sibling_view = _agent(seed.agent_id, seed.owner_id, OWNER_AGENTS_READ)
    listed = await _listed_actor_ids(integration_context, sibling_view)
    assert seed.agent_id in listed
    assert seed.owner_id not in listed  # users arm is self-only; an agent is not a user
    assert seed.other_id not in listed
    assert seed.other_agent_id not in listed


async def test_admin_lists_every_actor(integration_context: Context, seed: _Seed) -> None:
    listed = await _listed_actor_ids(integration_context, _ADMIN)
    assert {seed.owner_id, seed.other_id, seed.agent_id, seed.other_agent_id} <= listed
