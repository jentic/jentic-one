"""Integration tests for caller scoping of audit log reads.

An audit entry is visible to the actor that performed it, to the human owner of
the agent that performed it, and to ``org:admin``. Everyone else sees a
404-equivalent ``AuditEntryNotFoundError``. System entries (NULL actor) are
admin-only.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentRepository, AuditRepository, UserRepository
from jentic_one.admin.services.audit_service import AuditService
from jentic_one.admin.services.errors import AuditEntryNotFoundError
from jentic_one.admin.services.schemas.audit import AuditFilter
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType, InviteState
from jentic_one.shared.models.audit import AuditAction, AuditTargetType

pytestmark = pytest.mark.integration

_OTHER_SUB = "usr_auditscope_other"


@dataclass(frozen=True)
class _Seed:
    owner_id: str
    agent_id: str
    owner_entry_id: str
    agent_entry_id: str
    other_entry_id: str
    system_entry_id: str


def _user(sub: str, *permissions: str) -> Identity:
    return Identity(sub=sub, email=f"{sub}@test.local", permissions=list(permissions))


@pytest.fixture()
async def seed(integration_context: Context) -> AsyncGenerator[_Seed, None]:
    """One entry each by the owner, the owner's agent, another user and the system."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        await session.execute(delete(AuditEntry))
        owner = await UserRepository.create(
            session,
            email="auditscope-owner@test.local",
            first_name="Audit",
            last_name="Owner",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        agent = await AgentRepository.create(
            session,
            name="auditscope-agent",
            owner_id=owner.id,
            registered_by=owner.id,
            created_by=owner.id,
            status=ActorStatus.ACTIVE,
        )

        async def _record(actor_type: str, actor_id: str | None) -> str:
            entry = await AuditRepository.record(
                session,
                action=AuditAction.UPDATE,
                target_type=AuditTargetType.USER,
                target_id=owner.id,
                actor_type=actor_type,
                actor_id=actor_id,
            )
            return entry.id

        seeded = _Seed(
            owner_id=owner.id,
            agent_id=agent.id,
            owner_entry_id=await _record("user", owner.id),
            agent_entry_id=await _record("agent", agent.id),
            other_entry_id=await _record("user", _OTHER_SUB),
            system_entry_id=await _record("system", None),
        )
        await session.commit()

    yield seeded

    async with ctx.admin_db.session() as session:
        await session.execute(delete(AuditEntry))
        await session.execute(delete(Agent).where(Agent.id == seeded.agent_id))
        await session.execute(delete(User).where(User.id == seeded.owner_id))
        await session.commit()


async def _listed_ids(
    ctx: Context, identity: Identity, filter: AuditFilter | None = None
) -> set[str]:
    page = await AuditService(ctx).list_all(filter or AuditFilter(), identity=identity, limit=100)
    return {e.id for e in page.data}


async def test_owner_sees_own_and_agents_entries(integration_context: Context, seed: _Seed) -> None:
    ctx = integration_context
    owner = _user(seed.owner_id, "audit:read")
    mine = {seed.owner_entry_id, seed.agent_entry_id}

    assert await _listed_ids(ctx, owner) == mine
    # Scoping still applies on the target and actor branches.
    by_target = AuditFilter(target_type=AuditTargetType.USER, target_id=seed.owner_id)
    assert await _listed_ids(ctx, owner, by_target) == mine
    assert await _listed_ids(ctx, owner, AuditFilter(actor_id=_OTHER_SUB)) == set()
    assert await _listed_ids(ctx, owner, AuditFilter(actor_id=seed.agent_id)) == {
        seed.agent_entry_id
    }

    for entry_id in mine:
        assert (await AuditService(ctx).get_by_id(entry_id, identity=owner)).id == entry_id
    for entry_id in (seed.other_entry_id, seed.system_entry_id):
        with pytest.raises(AuditEntryNotFoundError):
            await AuditService(ctx).get_by_id(entry_id, identity=owner)


async def test_agent_sees_only_its_own_entries(integration_context: Context, seed: _Seed) -> None:
    ctx = integration_context
    agent = Identity(
        sub=seed.agent_id,
        permissions=["audit:read", "owner:agents:read"],
        actor_type=ActorType.AGENT,
        parent_actor_id=seed.owner_id,
    )
    assert await _listed_ids(ctx, agent) == {seed.agent_entry_id}
    with pytest.raises(AuditEntryNotFoundError):
        await AuditService(ctx).get_by_id(seed.owner_entry_id, identity=agent)


async def test_other_user_sees_only_their_entries(
    integration_context: Context, seed: _Seed
) -> None:
    ctx = integration_context
    other = _user(_OTHER_SUB, "audit:read")
    assert await _listed_ids(ctx, other) == {seed.other_entry_id}
    with pytest.raises(AuditEntryNotFoundError):
        await AuditService(ctx).get_by_id(seed.agent_entry_id, identity=other)


async def test_admin_sees_everything(integration_context: Context, seed: _Seed) -> None:
    ctx = integration_context
    admin = _user("usr_auditscope_admin", "org:admin")
    assert await _listed_ids(ctx, admin) == {
        seed.owner_entry_id,
        seed.agent_entry_id,
        seed.other_entry_id,
        seed.system_entry_id,
    }
    view = await AuditService(ctx).get_by_id(seed.system_entry_id, identity=admin)
    assert view.id == seed.system_entry_id
