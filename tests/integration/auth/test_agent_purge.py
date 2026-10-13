"""Integration tests for the agent hard delete (``AgentService.purge``).

``DELETE /agents/{id}?purge=true`` removes an agent outright, from any status,
together with every row keyed to it across the admin, control and registry
databases. Audit entries are history and survive (plus one ``delete`` entry
for the purge itself); credentials belong to their owner and are untouched.
The control and registry sweeps are best effort: a failure there is logged and
the agent is still deleted.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, func, select

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.authorization_codes import AuthorizationCode
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.repos import (
    AgentCredentialBindingRepository,
    AgentRepository,
    EventRepository,
    ExecutionRecordRepository,
    JobRepository,
)
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.errors import ActorNotFoundError
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.connect_session_outcomes import ConnectSessionOutcome
from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos import AgentPermissionRuleRepository
from jentic_one.registry.core.schema.notes import Note
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, JobKind, JobStatus, StoredCredentialType
from tests.integration.auth import seeds

pytestmark = pytest.mark.integration

_RULES: list[dict[str, object]] = [{"effect": "allow", "methods": ["GET"], "path": ".*"}]


def _admin_identity(admin_id: str) -> Identity:
    return Identity(
        sub=admin_id,
        email="admin@agent-purge.test",
        permissions=["agents:write", "agents:read", "org:admin"],
    )


def _owner_identity(owner_id: str) -> Identity:
    return Identity(
        sub=owner_id,
        email="owner@agent-purge.test",
        permissions=["agents:write", "agents:read"],
    )


@dataclass
class _Fixture:
    owner_id: str
    admin_id: str
    credential_id: str


@pytest.fixture()
async def purge_env(
    integration_context: Context, clean_grants: None
) -> AsyncGenerator[_Fixture, None]:
    """Owner + admin users and one control credential; sweeps every seeded row after."""
    ctx = integration_context
    owner = await seeds.seed_user(ctx, "usr_purge_owner")
    admin_id = await seeds.seed_user(ctx, "usr_purge_admin")
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"])
    async with ctx.control_db.transaction() as session:
        credential = Credential(
            type=StoredCredentialType.API_KEY,
            name="Purge Test Credential",
            api_vendor="stripe",
            api_name="payments",
            api_version="v1",
            created_by=owner,
        )
        session.add(credential)
        await session.flush()
        credential_id = credential.id
    yield _Fixture(owner_id=owner, admin_id=admin_id, credential_id=credential_id)

    async with ctx.control_db.transaction() as session:
        await session.execute(
            delete(AgentPermissionRule).where(AgentPermissionRule.credential_id == credential_id)
        )
        await session.execute(
            delete(ConnectSessionOutcome).where(
                ConnectSessionOutcome.credential_id == credential_id
            )
        )
        # Cascades to the credential's connect sessions.
        await session.execute(delete(Credential).where(Credential.id == credential_id))
    async with ctx.registry_db.transaction() as session:
        await session.execute(delete(Note).where(Note.created_by.like("agnt_%")))
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            delete(AgentCredentialBinding).where(
                AgentCredentialBinding.credential_id == credential_id
            )
        )
        await session.execute(delete(Event).where(Event.created_by == seeds.SEED_MARKER))
        await session.execute(
            delete(ExecutionRecord).where(ExecutionRecord.created_by == seeds.SEED_MARKER)
        )
        await session.execute(delete(Job).where(Job.created_by.like("agnt_%")))
        await session.execute(delete(AuditEntry).where(AuditEntry.actor_id == admin_id))
        await session.execute(delete(AuditEntry).where(AuditEntry.actor_id == owner))


async def _seed_agent_footprint(ctx: Context, env: _Fixture, agent_id: str) -> None:
    """Give one agent a row in every store the purge sweeps."""
    await seeds.mint_grant_channel_tokens(
        ctx, user_id=env.owner_id, agent_id=agent_id, grant_scopes=["apis:read"]
    )
    async with ctx.admin_db.transaction() as session:
        await AgentCredentialBindingRepository.bind(
            session,
            agent_id=agent_id,
            credential_id=env.credential_id,
            created_by=seeds.SEED_MARKER,
        )
        # One event per match arm: actor, visibility subject, data subject.
        await EventRepository.create(
            session,
            type="test.purge",
            severity="info",
            summary="by agent",
            created_by=seeds.SEED_MARKER,
            actor_id=agent_id,
            actor_type="agent",
        )
        await EventRepository.create(
            session,
            type="test.purge",
            severity="info",
            summary="about agent",
            created_by=agent_id,
        )
        await EventRepository.create(
            session,
            type="test.purge",
            severity="info",
            summary="agent in data",
            data={"agent_id": agent_id},
            created_by=seeds.SEED_MARKER,
        )
        await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_none",
            trace_id="0" * 32,
            started_at=datetime.now(UTC),
            status="completed",
            created_by=seeds.SEED_MARKER,
            actor_id=agent_id,
            actor_type="agent",
        )
        for status in (JobStatus.COMPLETED, JobStatus.RUNNING):
            job = await JobRepository.create(
                session, kind=JobKind.EXECUTION, status=status, created_by=agent_id
            )
            job.actor_type = "agent"
        await session.flush()
    async with ctx.control_db.transaction() as session:
        await AgentPermissionRuleRepository.replace_user_rules(
            session, agent_id, env.credential_id, _RULES, created_by=seeds.SEED_MARKER
        )
        for state in ("failed", "polling"):
            session.add(
                ConnectSession(
                    credential_id=env.credential_id,
                    vendor="stripe",
                    agent_id=agent_id,
                    initiator_actor_id=agent_id,
                    state=state,
                    resolved_flow="manual",
                    poll_token_hash=f"{agent_id}-{state}"[:64],
                )
            )
        session.add(
            ConnectSessionOutcome(
                session_id=f"cs_{agent_id}"[:30],
                agent_id=agent_id,
                created_by=agent_id,
                target_kind="vendor",
                vendor="stripe",
                resolved_flow="manual",
                outcome="failed",
                credential_id=env.credential_id,
                poll_token_hash=f"{agent_id}-outcome"[:64],
            )
        )
    async with ctx.registry_db.transaction() as session:
        session.add(Note(body="agent-authored note", created_by=agent_id))


async def _footprint(ctx: Context, agent_id: str) -> dict[str, int]:
    """Count the agent's rows in every swept store."""

    async def _count(session: object, stmt: object) -> int:
        return int((await session.execute(stmt)).scalar_one())  # type: ignore[attr-defined]

    counts: dict[str, int] = {}
    async with ctx.admin_db.session() as session:
        grant_ids = list(
            (
                await session.execute(
                    select(OAuthClientGrant.id).where(OAuthClientGrant.agent_id == agent_id)
                )
            ).scalars()
        )
        counts["agents"] = await _count(
            session, select(func.count()).select_from(Agent).where(Agent.id == agent_id)
        )
        counts["grants"] = len(grant_ids)
        counts["codes"] = await _count(
            session,
            select(func.count())
            .select_from(AuthorizationCode)
            .where(AuthorizationCode.grant_id.in_(grant_ids or ["-"])),
        )
        counts["access_tokens"] = await _count(
            session,
            select(func.count()).select_from(AccessToken).where(AccessToken.actor_id == agent_id),
        )
        counts["refresh_tokens"] = await _count(
            session,
            select(func.count()).select_from(RefreshToken).where(RefreshToken.actor_id == agent_id),
        )
        counts["bindings"] = await _count(
            session,
            select(func.count())
            .select_from(AgentCredentialBinding)
            .where(AgentCredentialBinding.agent_id == agent_id),
        )
        events = (await session.execute(select(Event).where(Event.type == "test.purge"))).scalars()
        counts["events"] = sum(
            1
            for e in events
            if agent_id in (e.actor_id, e.created_by) or (e.data or {}).get("agent_id") == agent_id
        )
        counts["executions"] = await _count(
            session,
            select(func.count())
            .select_from(ExecutionRecord)
            .where(ExecutionRecord.actor_id == agent_id),
        )
        counts["jobs_finished"] = await _count(
            session,
            select(func.count())
            .select_from(Job)
            .where(Job.created_by == agent_id, Job.status != JobStatus.RUNNING.value),
        )
    async with ctx.control_db.session() as session:
        counts["rules"] = await _count(
            session,
            select(func.count())
            .select_from(AgentPermissionRule)
            .where(AgentPermissionRule.agent_id == agent_id),
        )
        counts["sessions_finished"] = await _count(
            session,
            select(func.count())
            .select_from(ConnectSession)
            .where(ConnectSession.agent_id == agent_id, ConnectSession.state == "failed"),
        )
        counts["outcomes"] = await _count(
            session,
            select(func.count())
            .select_from(ConnectSessionOutcome)
            .where(ConnectSessionOutcome.agent_id == agent_id),
        )
    async with ctx.registry_db.session() as session:
        counts["notes"] = await _count(
            session, select(func.count()).select_from(Note).where(Note.created_by == agent_id)
        )
    return counts


async def test_purge_removes_agent_footprint_and_spares_others(
    integration_context: Context, purge_env: _Fixture
) -> None:
    """Purging an active agent clears every store for it, leaves a bystander
    agent's rows intact, keeps audit history and the credential, nulls a child's
    parent link, and records one ``delete`` audit entry."""
    ctx = integration_context
    target = await seeds.seed_agent(
        ctx, owner_id=purge_env.owner_id, scopes=["apis:read"], name="purge-target"
    )
    bystander = await seeds.seed_agent(
        ctx, owner_id=purge_env.owner_id, scopes=["apis:read"], name="purge-bystander"
    )
    async with ctx.admin_db.transaction() as session:
        child = await AgentRepository.create(
            session,
            name="purge-child",
            owner_id=purge_env.owner_id,
            registered_by=purge_env.owner_id,
            parent_agent_id=target,
            created_by=seeds.SEED_MARKER,
        )
        child_id = child.id
    await _seed_agent_footprint(ctx, purge_env, target)
    await _seed_agent_footprint(ctx, purge_env, bystander)

    before = await _footprint(ctx, target)
    assert all(count > 0 for count in before.values()), before
    bystander_before = await _footprint(ctx, bystander)

    await AgentService(ctx).purge(target, identity=_admin_identity(purge_env.admin_id))

    assert all(count == 0 for count in (await _footprint(ctx, target)).values())
    assert await _footprint(ctx, bystander) == bystander_before

    async with ctx.admin_db.session() as session:
        # A RUNNING job is left for its worker to finalise.
        running = (
            await session.execute(
                select(func.count())
                .select_from(Job)
                .where(Job.created_by == target, Job.status == JobStatus.RUNNING.value)
            )
        ).scalar_one()
        assert running == 1
        child_row = await AgentRepository.get_by_id(session, child_id)
        assert child_row is not None and child_row.parent_agent_id is None
        audits = list(
            (
                await session.execute(
                    select(AuditEntry).where(
                        AuditEntry.target_type == "agent", AuditEntry.target_id == target
                    )
                )
            ).scalars()
        )
        assert [a.action for a in audits] == ["delete"]
        assert audits[0].actor_id == purge_env.admin_id
        assert (audits[0].before or {}).get("name") == "purge-target"
        # The grant revocation from the inline archive step is kept as history.
        grant_revokes = (
            await session.execute(
                select(func.count())
                .select_from(AuditEntry)
                .where(
                    AuditEntry.target_type == "oauth_grant",
                    AuditEntry.reason == "oauth grant revoked: agent deleted",
                )
            )
        ).scalar_one()
        assert grant_revokes == 1
    async with ctx.control_db.session() as session:
        assert await session.get(Credential, purge_env.credential_id) is not None
        # The live connect session is left for the stale-session sweep.
        live = (
            await session.execute(
                select(func.count())
                .select_from(ConnectSession)
                .where(ConnectSession.agent_id == target, ConnectSession.state == "polling")
            )
        ).scalar_one()
        assert live == 1


@pytest.mark.parametrize(
    "status",
    [ActorStatus.PENDING, ActorStatus.ACTIVE, ActorStatus.DISABLED, ActorStatus.ARCHIVED],
)
async def test_purge_allowed_from_any_status_by_owner(
    integration_context: Context, purge_env: _Fixture, status: ActorStatus
) -> None:
    """The owner (agents:write, not org:admin) may purge from every status."""
    ctx = integration_context
    agent_id = await seeds.seed_agent(
        ctx, owner_id=purge_env.owner_id, scopes=["apis:read"], status=status
    )

    await AgentService(ctx).purge(agent_id, identity=_owner_identity(purge_env.owner_id))

    async with ctx.admin_db.session() as session:
        assert await AgentRepository.get_by_id(session, agent_id) is None


async def test_purge_hides_agents_outside_caller_ownership(
    integration_context: Context, purge_env: _Fixture
) -> None:
    """A non-owner without org:admin gets not-found and the agent survives;
    a second purge of an already-deleted agent is not-found too."""
    ctx = integration_context
    agent_id = await seeds.seed_agent(ctx, owner_id=purge_env.owner_id, scopes=[])
    stranger = await seeds.seed_user(ctx, "usr_purge_stranger")
    svc = AgentService(ctx)

    with pytest.raises(ActorNotFoundError):
        await svc.purge(agent_id, identity=_owner_identity(stranger))
    async with ctx.admin_db.session() as session:
        assert await AgentRepository.get_by_id(session, agent_id) is not None

    await svc.purge(agent_id, identity=_admin_identity(purge_env.admin_id))
    with pytest.raises(ActorNotFoundError):
        await svc.purge(agent_id, identity=_admin_identity(purge_env.admin_id))


async def test_purge_survives_control_sweep_failure(
    integration_context: Context, purge_env: _Fixture
) -> None:
    """The control sweep is best effort: when it fails the agent is still
    deleted, and the control rows it would have removed are left behind."""
    ctx = integration_context
    agent_id = await seeds.seed_agent(ctx, owner_id=purge_env.owner_id, scopes=["apis:read"])
    async with ctx.control_db.transaction() as session:
        await AgentPermissionRuleRepository.replace_user_rules(
            session, agent_id, purge_env.credential_id, _RULES, created_by=seeds.SEED_MARKER
        )

    with patch(
        "jentic_one.auth.services.agent_service.AgentControlPurgeRepository.delete_for_agent",
        new=AsyncMock(side_effect=RuntimeError("control unavailable")),
    ):
        await AgentService(ctx).purge(agent_id, identity=_admin_identity(purge_env.admin_id))

    async with ctx.admin_db.session() as session:
        assert await AgentRepository.get_by_id(session, agent_id) is None
    async with ctx.control_db.session() as session:
        rules = await AgentPermissionRuleRepository.list_rules(
            session, agent_id, purge_env.credential_id
        )
    assert len(rules) == 1


async def test_purge_skips_registry_when_not_allowed(
    integration_context: Context, integration_config: object, purge_env: _Fixture
) -> None:
    """A standalone auth surface has no registry DB: the purge still succeeds."""
    agent_id = await seeds.seed_agent(
        integration_context, owner_id=purge_env.owner_id, scopes=["apis:read"]
    )
    async with Context(integration_config, allowed_dbs={"admin", "control"}) as auth_ctx:  # type: ignore[arg-type]
        await AgentService(auth_ctx).purge(agent_id, identity=_admin_identity(purge_env.admin_id))

    async with integration_context.admin_db.session() as session:
        assert await AgentRepository.get_by_id(session, agent_id) is None
