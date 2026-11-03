"""Integration tests for AgentRepository against real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, func, update
from sqlalchemy.exc import IntegrityError

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentRepository, UserRepository
from jentic_one.admin.services.errors import AgentNotFoundError
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorStatus

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_agents(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.session() as session:
        await session.execute(delete(Agent))
        await session.execute(delete(User))
        await session.commit()
    yield
    async with admin_db.session() as session:
        await session.execute(delete(Agent))
        await session.execute(delete(User))
        await session.commit()


@pytest.fixture()
async def test_owner(admin_db: DatabaseSession, clean_agents: None) -> str:
    async with admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="agent-owner@example.com",
            first_name="Agent",
            last_name="Owner",
            created_by="usr_test",
        )
        await session.commit()
        return user.id


async def test_create_agent(admin_db: DatabaseSession, test_owner: str) -> None:
    async with admin_db.session() as session:
        agent = await AgentRepository.create(
            session,
            name="test-agent",
            owner_id=test_owner,
            registered_by=test_owner,
            description="A test agent",
            created_by="usr_test",
        )
        await session.commit()
        assert agent.id.startswith("agnt_")
        assert agent.name == "test-agent"
        assert agent.description == "A test agent"
        assert agent.owner_id == test_owner
        assert agent.registered_by == test_owner
        assert agent.status == "pending"


async def test_get_by_id(admin_db: DatabaseSession, test_owner: str) -> None:
    async with admin_db.session() as session:
        agent = await AgentRepository.create(
            session,
            name="lookup-agent",
            owner_id=test_owner,
            registered_by=test_owner,
            created_by="usr_test",
        )
        await session.commit()
        agent_id = agent.id

    async with admin_db.session() as session:
        loaded = await AgentRepository.get_by_id(session, agent_id)
        assert loaded is not None
        assert loaded.name == "lookup-agent"


async def test_get_by_id_not_found(admin_db: DatabaseSession, test_owner: str) -> None:
    async with admin_db.session() as session:
        loaded = await AgentRepository.get_by_id(session, "agnt_nonexistent000000000000")
        assert loaded is None


async def test_list_by_owner(admin_db: DatabaseSession, test_owner: str) -> None:
    async with admin_db.session() as session:
        user2 = await UserRepository.create(
            session,
            email="agent-owner2@example.com",
            first_name="Agent2",
            last_name="Owner2",
            created_by="usr_test",
        )
        await session.commit()
        user2_id = user2.id

    async with admin_db.session() as session:
        await AgentRepository.create(
            session,
            name="agent-a",
            owner_id=test_owner,
            registered_by=test_owner,
            created_by="usr_test",
        )
        await AgentRepository.create(
            session,
            name="agent-b",
            owner_id=test_owner,
            registered_by=test_owner,
            created_by="usr_test",
        )
        await AgentRepository.create(
            session,
            name="agent-c",
            owner_id=user2_id,
            registered_by=user2_id,
            created_by="usr_test",
        )
        await session.commit()

    async with admin_db.session() as session:
        owner1_agents = await AgentRepository.list_by_owner(session, test_owner)
        assert len(owner1_agents) == 2
        assert all(a.owner_id == test_owner for a in owner1_agents)

        owner2_agents = await AgentRepository.list_by_owner(session, user2_id)
        assert len(owner2_agents) == 1
        assert owner2_agents[0].name == "agent-c"


async def test_list_all_with_status_filter(admin_db: DatabaseSession, test_owner: str) -> None:
    async with admin_db.session() as session:
        await AgentRepository.create(
            session,
            name="pending-agent",
            owner_id=test_owner,
            registered_by=test_owner,
            created_by="usr_test",
        )
        a2 = await AgentRepository.create(
            session,
            name="active-agent",
            owner_id=test_owner,
            registered_by=test_owner,
            created_by="usr_test",
        )
        await session.flush()
        a2.status = "active"
        await session.flush()
        await session.commit()

    async with admin_db.session() as session:
        all_agents = await AgentRepository.list_all(session)
        assert len(all_agents) == 2

        pending = await AgentRepository.list_all(session, status="pending")
        assert len(pending) == 1
        assert pending[0].name == "pending-agent"

        active = await AgentRepository.list_all(session, status="active")
        assert len(active) == 1
        assert active[0].name == "active-agent"


async def test_set_approval(admin_db: DatabaseSession, test_owner: str) -> None:
    async with admin_db.session() as session:
        agent = await AgentRepository.create(
            session,
            name="approval-agent",
            owner_id=test_owner,
            registered_by=test_owner,
            created_by="usr_test",
        )
        await session.commit()
        agent_id = agent.id

    async with admin_db.session() as session:
        approved = await AgentRepository.set_approval(session, agent_id, approved_by=test_owner)
        await session.commit()
        assert approved.status == "active"
        assert approved.approved_by == test_owner
        assert approved.approved_at is not None


async def test_set_denial(admin_db: DatabaseSession, test_owner: str) -> None:
    async with admin_db.session() as session:
        agent = await AgentRepository.create(
            session,
            name="denied-agent",
            owner_id=test_owner,
            registered_by=test_owner,
            created_by="usr_test",
        )
        await session.commit()
        agent_id = agent.id

    async with admin_db.session() as session:
        denied = await AgentRepository.set_denial(
            session, agent_id, reason="Policy violation", denied_by=test_owner
        )
        await session.commit()
        assert denied.status == "rejected"
        assert denied.denial_reason == "Policy violation"
        assert denied.denied_by == test_owner


async def test_update_status_not_found(admin_db: DatabaseSession, test_owner: str) -> None:
    async with admin_db.session() as session:
        with pytest.raises(AgentNotFoundError):
            await AgentRepository.update_status(
                session, "agnt_nonexistent000000000000", ActorStatus.ACTIVE
            )


async def test_parent_agent_self_ref(admin_db: DatabaseSession, test_owner: str) -> None:
    async with admin_db.session() as session:
        parent = await AgentRepository.create(
            session,
            name="parent-agent",
            owner_id=test_owner,
            registered_by=test_owner,
            created_by="usr_test",
        )
        await session.flush()
        child = await AgentRepository.create(
            session,
            name="child-agent",
            owner_id=test_owner,
            registered_by=test_owner,
            parent_agent_id=parent.id,
            created_by="usr_test",
        )
        await session.commit()
        assert child.parent_agent_id == parent.id

    async with admin_db.session() as session:
        loaded = await AgentRepository.get_by_id(session, child.id)
        assert loaded is not None
        assert loaded.parent_agent_id == parent.id


async def test_owner_fk_enforcement(admin_db: DatabaseSession, test_owner: str) -> None:
    async with admin_db.session() as session:
        with pytest.raises(IntegrityError):
            await AgentRepository.create(
                session,
                name="orphan-agent",
                owner_id="usr_nonexistent000000000000",
                registered_by=test_owner,
                created_by="usr_test",
            )


async def test_get_with_owner_active_refuses_deleted_owner_even_when_active(
    admin_db: DatabaseSession, test_owner: str
) -> None:
    async with admin_db.session() as session:
        agent = await AgentRepository.create(
            session,
            name="deleted-owner-agent",
            owner_id=test_owner,
            registered_by=test_owner,
            created_by="usr_test",
        )
        await session.commit()
        agent_id = agent.id

    async with admin_db.session() as session:
        loaded = await AgentRepository.get_with_owner_active(session, agent_id)
        assert loaded is not None
        assert loaded[1] is True

    # Mark owner deleted while leaving active=True.
    async with admin_db.transaction() as session:
        await session.execute(
            update(User).where(User.id == test_owner).values(deleted_at=func.now(), active=True)
        )

    async with admin_db.session() as session:
        loaded = await AgentRepository.get_with_owner_active(session, agent_id)
        assert loaded is not None
        assert loaded[0].id == agent_id
        assert loaded[1] is False
