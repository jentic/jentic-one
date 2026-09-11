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
        # Rule grouping (Q-04): defaults to inline rules (no shared set)…
        assert loaded.rule_set_id is None
        # …and accepts a cross-DB rule-set pointer (plain string, no FK).
        loaded.rule_set_id = "prs_shared01"
        await session.commit()
    async with admin_db.session() as session:
        reloaded = await session.get(AgentCredentialBinding, binding_id)
        assert reloaded is not None
        assert reloaded.rule_set_id == "prs_shared01"


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


async def test_agent_delete_leaves_bindings_for_service_layer(
    admin_db: DatabaseSession, clean_bindings: None
) -> None:
    """Deleting the agent row no longer cascades to its bindings.

    Theme-5 Phase 4 widened ``agent_id`` to hold any broker-executing actor
    (``agnt_`` or ``sva_``), dropping the FK to ``agents.id`` — so cleanup is
    the service layer's job (``AgentService.archive`` deletes bindings
    explicitly), not the database's.
    """
    agent_id = await _create_agent(admin_db)
    async with admin_db.session() as session:
        session.add(AgentCredentialBinding(agent_id=agent_id, credential_id="cred_cascade"))
        await session.commit()

    async with admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.id == agent_id))
        await session.commit()

    async with admin_db.session() as session:
        remaining = (await session.execute(select(AgentCredentialBinding))).scalars().all()
        assert [r.agent_id for r in remaining] == [agent_id]
