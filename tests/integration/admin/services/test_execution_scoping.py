"""Integration tests for owner scoping of execution-record reads.

An execution is visible to the actor that ran it, to the human owner of the
agent that ran it, and to ``org:admin``. Everyone else gets nothing from the
list and a 404-equivalent ``ExecutionNotFoundError`` from a direct read.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentRepository, ExecutionRecordRepository, UserRepository
from jentic_one.admin.services.errors import ExecutionNotFoundError
from jentic_one.admin.services.execution_service import ExecutionService
from jentic_one.admin.services.schemas.executions import ExecutionFilter
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType, InviteState

pytestmark = pytest.mark.integration

_OTHER_SUB = "usr_execscope_other"
_OTHER_AGENT_SUB = "agnt_execscope_other"


@dataclass(frozen=True)
class _Seed:
    owner_id: str
    agent_id: str
    owner_exec_id: str
    agent_exec_id: str


def _user(sub: str, *permissions: str) -> Identity:
    return Identity(sub=sub, email=f"{sub}@test.local", permissions=list(permissions))


def _agent(sub: str, *, parent: str | None) -> Identity:
    return Identity(
        sub=sub,
        permissions=["executions:read", "owner:agents:read"],
        actor_type=ActorType.AGENT,
        parent_actor_id=parent,
    )


@pytest.fixture()
async def seed(integration_context: Context) -> AsyncGenerator[_Seed, None]:
    """One execution per actor: the owner's own and the owner's agent's."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        await session.execute(delete(ExecutionRecord))
        owner = await UserRepository.create(
            session,
            email="execscope-owner@test.local",
            first_name="Exec",
            last_name="Owner",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        agent = await AgentRepository.create(
            session,
            name="execscope-agent",
            owner_id=owner.id,
            registered_by=owner.id,
            created_by=owner.id,
            status=ActorStatus.ACTIVE,
        )
        records = []
        for actor_id, actor_type in ((owner.id, "user"), (agent.id, "agent")):
            records.append(
                await ExecutionRecordRepository.create(
                    session,
                    toolkit_id="tk_execscope",
                    trace_id=f"trace_{actor_id}"[:32],
                    started_at=datetime.now(UTC),
                    status="success",
                    created_by=actor_id,
                    actor_id=actor_id,
                    actor_type=actor_type,
                )
            )
        await session.commit()
        seeded = _Seed(
            owner_id=owner.id,
            agent_id=agent.id,
            owner_exec_id=records[0].id,
            agent_exec_id=records[1].id,
        )

    yield seeded

    async with ctx.admin_db.session() as session:
        await session.execute(delete(ExecutionRecord))
        await session.execute(delete(Agent).where(Agent.id == seeded.agent_id))
        await session.execute(delete(User).where(User.id == seeded.owner_id))
        await session.commit()


async def _listed_ids(ctx: Context, identity: Identity) -> set[str]:
    page = await ExecutionService(ctx).list_all(ExecutionFilter(), identity=identity, limit=100)
    return {e.id for e in page.data}


async def test_other_actors_cannot_list_or_get(integration_context: Context, seed: _Seed) -> None:
    ctx = integration_context
    outsiders = [
        _user(_OTHER_SUB, "executions:read"),
        _agent(_OTHER_AGENT_SUB, parent=_OTHER_SUB),
        # An agent never inherits its owner's (or a sibling agent's) executions.
        _agent(_OTHER_AGENT_SUB, parent=seed.owner_id),
    ]
    for identity in outsiders:
        assert await _listed_ids(ctx, identity) == set()
        # Filtering by someone else's actor id still returns nothing.
        page = await ExecutionService(ctx).list_all(
            ExecutionFilter(actor_id=seed.owner_id), identity=identity
        )
        assert page.data == []
        for exec_id in (seed.owner_exec_id, seed.agent_exec_id):
            with pytest.raises(ExecutionNotFoundError):
                await ExecutionService(ctx).get_by_id(exec_id, identity=identity)


async def test_actor_owner_and_admin_visibility(integration_context: Context, seed: _Seed) -> None:
    ctx = integration_context
    both = {seed.owner_exec_id, seed.agent_exec_id}

    # The running agent sees only its own execution.
    agent = _agent(seed.agent_id, parent=seed.owner_id)
    assert await _listed_ids(ctx, agent) == {seed.agent_exec_id}
    assert (await ExecutionService(ctx).get_by_id(seed.agent_exec_id, identity=agent)).id == (
        seed.agent_exec_id
    )
    with pytest.raises(ExecutionNotFoundError):
        await ExecutionService(ctx).get_by_id(seed.owner_exec_id, identity=agent)

    # The human owner sees their own execution and their agent's.
    owner = _user(seed.owner_id, "executions:read")
    assert await _listed_ids(ctx, owner) == both
    for exec_id in both:
        assert (await ExecutionService(ctx).get_by_id(exec_id, identity=owner)).id == exec_id

    # org:admin is unrestricted.
    admin = _user("usr_execscope_admin", "org:admin")
    assert await _listed_ids(ctx, admin) == both
