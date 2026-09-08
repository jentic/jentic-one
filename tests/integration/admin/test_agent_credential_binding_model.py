"""Integration tests for the AgentCredentialBinding model."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_bindings(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Empty ``agent_credential_bindings`` and ``agents`` before and after each test."""
    async with admin_db.session() as session:
        await session.execute(delete(AgentCredentialBinding))
        await session.execute(delete(Agent))
        await session.commit()
    yield
    async with admin_db.session() as session:
        await session.execute(delete(AgentCredentialBinding))
        await session.execute(delete(Agent))
        await session.commit()


async def _create_agent(admin_db: DatabaseSession, name: str = "binding-test-agent") -> str:
    async with admin_db.session() as session:
        agent = Agent(name=name, registered_by="usr_test", status="approved")
        session.add(agent)
        await session.commit()
        return agent.id


async def test_round_trip_and_id_prefix(admin_db: DatabaseSession, clean_bindings: None) -> None:
    """A binding persists, round-trips, and gets an ``acb_`` KSUID id."""
    agent_id = await _create_agent(admin_db)
    async with admin_db.session() as session:
        binding = AgentCredentialBinding(agent_id=agent_id, credential_id="cred_test001")
        session.add(binding)
        await session.commit()
        binding_id = binding.id

    assert binding_id.startswith("acb_")
    async with admin_db.session() as session:
        loaded = await session.get(AgentCredentialBinding, binding_id)
        assert loaded is not None
        assert loaded.agent_id == agent_id
        assert loaded.credential_id == "cred_test001"
        assert loaded.bound_at is not None
        assert loaded.created_at is not None


async def test_duplicate_pair_rejected(admin_db: DatabaseSession, clean_bindings: None) -> None:
    """The (agent_id, credential_id) pair is unique."""
    agent_id = await _create_agent(admin_db)
    async with admin_db.session() as session:
        session.add(AgentCredentialBinding(agent_id=agent_id, credential_id="cred_dup01"))
        await session.commit()

    async with admin_db.session() as session:
        session.add(AgentCredentialBinding(agent_id=agent_id, credential_id="cred_dup01"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_multiple_credentials_per_agent(
    admin_db: DatabaseSession, clean_bindings: None
) -> None:
    """One agent may bind several credentials (multi-account is allowed)."""
    agent_id = await _create_agent(admin_db)
    async with admin_db.session() as session:
        session.add(AgentCredentialBinding(agent_id=agent_id, credential_id="cred_acct_a"))
        session.add(AgentCredentialBinding(agent_id=agent_id, credential_id="cred_acct_b"))
        await session.commit()

    async with admin_db.session() as session:
        rows = (
            (
                await session.execute(
                    select(AgentCredentialBinding).where(
                        AgentCredentialBinding.agent_id == agent_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {r.credential_id for r in rows} == {"cred_acct_a", "cred_acct_b"}


async def test_cascade_on_agent_delete(admin_db: DatabaseSession, clean_bindings: None) -> None:
    """Deleting the agent cascades to its bindings."""
    agent_id = await _create_agent(admin_db)
    async with admin_db.session() as session:
        session.add(AgentCredentialBinding(agent_id=agent_id, credential_id="cred_cascade"))
        await session.commit()

    async with admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.id == agent_id))
        await session.commit()

    async with admin_db.session() as session:
        remaining = (await session.execute(select(AgentCredentialBinding))).scalars().all()
        assert remaining == []
