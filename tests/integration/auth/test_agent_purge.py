"""Integration tests for the agent hard delete (``AgentService.purge``).

``DELETE /agents/{id}?purge=true`` removes an agent outright, from any status,
with its authority and session rows: tokens, consent grants and codes,
permission grants and rules, credential bindings, connect sessions (and the
pending credential each live one holds), and it cancels the agent's active
jobs. History is kept: audit entries, events, execution records, finished jobs
and notes, as are the credentials the agent created. Every failure
propagates; a failed purge leaves the agent archived and a retry finishes it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, func, or_, select

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.authorization_codes import AuthorizationCode
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.held_executions import HeldExecution
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
from jentic_one.shared.config import AppConfig, DatabaseConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseUnavailableError
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.models import ActorStatus, JobKind, JobStatus, StoredCredentialType
from jentic_one.shared.models.held_executions import HeldExecutionState
from tests.integration.auth import seeds

pytestmark = pytest.mark.integration

_RULES: list[dict[str, object]] = [{"effect": "allow", "methods": ["GET"], "path": ".*"}]
_PURGE_REASON = "agent purged"


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
    ctx: Context
    owner_id: str
    admin_id: str
    credential_id: str
    #: Every agent a test seeds; the teardown sweeps exactly these.
    agent_ids: list[str] = field(default_factory=list)

    async def agent(self, *, status: ActorStatus = ActorStatus.ACTIVE, name: str = "purge") -> str:
        agent_id = await seeds.seed_agent(
            self.ctx, owner_id=self.owner_id, scopes=["apis:read"], status=status, name=name
        )
        self.agent_ids.append(agent_id)
        return agent_id


@pytest.fixture()
async def purge_env(
    integration_context: Context, clean_grants: None
) -> AsyncGenerator[_Fixture, None]:
    """Owner + admin users and one owner credential; sweeps the seeded agents' rows after."""
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
    env = _Fixture(ctx=ctx, owner_id=owner, admin_id=admin_id, credential_id=credential_id)
    yield env

    ids = env.agent_ids
    people = [owner, admin_id]
    async with ctx.control_db.transaction() as session:
        await session.execute(
            delete(AgentPermissionRule).where(AgentPermissionRule.agent_id.in_(ids))
        )
        await session.execute(
            delete(ConnectSessionOutcome).where(ConnectSessionOutcome.agent_id.in_(ids))
        )
        await session.execute(
            delete(ConnectSession).where(
                or_(ConnectSession.agent_id.in_(ids), ConnectSession.initiator_actor_id.in_(ids))
            )
        )
        await session.execute(
            delete(Credential).where(
                or_(Credential.id == credential_id, Credential.created_by.in_(ids))
            )
        )
    async with ctx.registry_db.transaction() as session:
        await session.execute(delete(Note).where(Note.created_by.in_(ids)))
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id.in_(ids))
        )
        await session.execute(
            delete(Event).where(
                or_(
                    Event.actor_id.in_(ids),
                    Event.created_by.in_(ids),
                    Event.created_by.in_(people),
                    Event.created_by == seeds.SEED_MARKER,
                )
            )
        )
        await session.execute(delete(ExecutionRecord).where(ExecutionRecord.actor_id.in_(ids)))
        await session.execute(delete(Job).where(Job.created_by.in_(ids)))
        await session.execute(
            delete(AuditEntry).where(
                or_(AuditEntry.actor_id.in_(people), AuditEntry.target_id.in_(ids))
            )
        )


@dataclass
class _Seeded:
    """The ids of the per-agent rows the footprint tracks across the purge."""

    grant_ids: list[str]
    pending_credential_id: str
    retained_credential_id: str
    jobs: dict[JobStatus, str]


async def _seed_agent_footprint(ctx: Context, env: _Fixture, agent_id: str) -> _Seeded:
    """Give one agent a row in every store the purge sweeps or keeps."""
    grant_id, *_ = await seeds.mint_grant_channel_tokens(
        ctx, user_id=env.owner_id, agent_id=agent_id, grant_scopes=["apis:read"]
    )
    async with ctx.admin_db.transaction() as session:
        await AgentCredentialBindingRepository.bind(
            session,
            agent_id=agent_id,
            credential_id=env.credential_id,
            created_by=seeds.SEED_MARKER,
        )
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
            session, type="test.purge", severity="info", summary="about agent", created_by=agent_id
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
        jobs = {}
        for status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.COMPLETED):
            job = await JobRepository.create(
                session, kind=JobKind.EXECUTION, status=status, created_by=agent_id
            )
            jobs[status] = job.id
    async with ctx.control_db.transaction() as session:
        await AgentPermissionRuleRepository.replace_user_rules(
            session, agent_id, env.credential_id, _RULES, created_by=seeds.SEED_MARKER
        )
        pending = Credential(
            type=StoredCredentialType.API_KEY,
            name="Pending Connect",
            api_vendor="stripe",
            api_name="payments",
            api_version="v1",
            state="pending",
            created_by=agent_id,
        )
        retained = Credential(
            type=StoredCredentialType.API_KEY,
            name="Agent-Created Credential",
            api_vendor="stripe",
            api_name="payments",
            api_version="v1",
            created_by=agent_id,
        )
        session.add_all([pending, retained])
        await session.flush()
        # A live session holding its own pending credential, and a finished one
        # over the owner's connected credential.
        for credential_id, state in ((pending.id, "polling"), (env.credential_id, "connected")):
            session.add(
                ConnectSession(
                    credential_id=credential_id,
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
                outcome="connected",
                credential_id=env.credential_id,
                poll_token_hash=f"{agent_id}-outcome"[:64],
            )
        )
        await session.flush()
        seeded = _Seeded(
            grant_ids=[grant_id],
            pending_credential_id=pending.id,
            retained_credential_id=retained.id,
            jobs=jobs,
        )
    async with ctx.registry_db.transaction() as session:
        session.add(Note(body="agent-authored note", created_by=agent_id))
    return seeded


async def _count(session: object, stmt: object) -> int:
    return int((await session.execute(stmt)).scalar_one())  # type: ignore[attr-defined]


async def _swept(ctx: Context, agent_id: str, seeded: _Seeded) -> dict[str, int]:
    """Count the agent's rows the purge deletes (or, for jobs, cancels)."""
    counts: dict[str, int] = {}
    async with ctx.admin_db.session() as session:
        counts["agents"] = await _count(
            session, select(func.count()).select_from(Agent).where(Agent.id == agent_id)
        )
        counts["grants"] = await _count(
            session,
            select(func.count())
            .select_from(OAuthClientGrant)
            .where(OAuthClientGrant.agent_id == agent_id),
        )
        # Keyed on the grant ids captured at seed time: once the grants are
        # gone a lookup through them would count nothing either way.
        counts["codes"] = await _count(
            session,
            select(func.count())
            .select_from(AuthorizationCode)
            .where(AuthorizationCode.grant_id.in_(seeded.grant_ids)),
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
        counts["active_jobs"] = await _count(
            session,
            select(func.count())
            .select_from(Job)
            .where(
                Job.created_by == agent_id,
                Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
            ),
        )
    async with ctx.control_db.session() as session:
        counts["rules"] = await _count(
            session,
            select(func.count())
            .select_from(AgentPermissionRule)
            .where(AgentPermissionRule.agent_id == agent_id),
        )
        counts["sessions"] = await _count(
            session,
            select(func.count())
            .select_from(ConnectSession)
            .where(ConnectSession.agent_id == agent_id),
        )
        counts["outcomes"] = await _count(
            session,
            select(func.count())
            .select_from(ConnectSessionOutcome)
            .where(ConnectSessionOutcome.agent_id == agent_id),
        )
        counts["pending_credential"] = await _count(
            session,
            select(func.count())
            .select_from(Credential)
            .where(Credential.id == seeded.pending_credential_id),
        )
    return counts


async def _kept(ctx: Context, agent_id: str, seeded: _Seeded) -> dict[str, int]:
    """Count the agent's history rows and credential the purge keeps."""
    counts: dict[str, int] = {}
    async with ctx.admin_db.session() as session:
        counts["events"] = await _count(
            session,
            select(func.count())
            .select_from(Event)
            .where(
                Event.type == "test.purge",
                or_(Event.actor_id == agent_id, Event.created_by == agent_id),
            ),
        )
        counts["executions"] = await _count(
            session,
            select(func.count())
            .select_from(ExecutionRecord)
            .where(ExecutionRecord.actor_id == agent_id),
        )
        counts["jobs"] = await _count(
            session, select(func.count()).select_from(Job).where(Job.created_by == agent_id)
        )
    async with ctx.control_db.session() as session:
        counts["retained_credential"] = await _count(
            session,
            select(func.count())
            .select_from(Credential)
            .where(Credential.id == seeded.retained_credential_id),
        )
    async with ctx.registry_db.session() as session:
        counts["notes"] = await _count(
            session, select(func.count()).select_from(Note).where(Note.created_by == agent_id)
        )
    return counts


async def _agent_audits(ctx: Context, agent_id: str) -> list[AuditEntry]:
    async with ctx.admin_db.session() as session:
        rows = await session.execute(
            select(AuditEntry).where(
                AuditEntry.target_type == "agent", AuditEntry.target_id == agent_id
            )
        )
        return sorted(rows.scalars(), key=lambda a: a.action)


async def test_purge_removes_authority_and_sessions_and_keeps_history(
    integration_context: Context, purge_env: _Fixture
) -> None:
    """Purging an active agent sweeps its authority and session rows, keeps its
    history and the credentials it created, leaves a bystander agent intact,
    nulls a child's parent link, and audits both the archive and the delete."""
    ctx = integration_context
    target = await purge_env.agent(name="purge-target")
    bystander = await purge_env.agent(name="purge-bystander")
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
    seeded = await _seed_agent_footprint(ctx, purge_env, target)
    bystander_seeded = await _seed_agent_footprint(ctx, purge_env, bystander)

    swept_before = await _swept(ctx, target, seeded)
    assert all(count > 0 for count in swept_before.values()), swept_before
    kept_before = await _kept(ctx, target, seeded)
    assert all(count > 0 for count in kept_before.values()), kept_before
    bystander_before = (
        await _swept(ctx, bystander, bystander_seeded),
        await _kept(ctx, bystander, bystander_seeded),
    )

    await AgentService(ctx).purge(target, identity=_admin_identity(purge_env.admin_id))

    assert all(count == 0 for count in (await _swept(ctx, target, seeded)).values())
    assert await _kept(ctx, target, seeded) == kept_before
    assert (
        await _swept(ctx, bystander, bystander_seeded),
        await _kept(ctx, bystander, bystander_seeded),
    ) == bystander_before

    async with ctx.admin_db.session() as session:
        statuses = {
            status: (await session.get(Job, job_id)).status  # type: ignore[union-attr]
            for status, job_id in seeded.jobs.items()
        }
        assert statuses == {
            JobStatus.QUEUED: JobStatus.CANCELLED,
            JobStatus.RUNNING: JobStatus.CANCELLED,
            JobStatus.COMPLETED: JobStatus.COMPLETED,
        }
        child_row = await AgentRepository.get_by_id(session, child_id)
        assert child_row is not None and child_row.parent_agent_id is None
        # The grant revocation from the inline archive step is kept as history.
        grant_revokes = await _count(
            session,
            select(func.count())
            .select_from(AuditEntry)
            .where(
                AuditEntry.target_type == "oauth_grant",
                AuditEntry.target_id.in_(seeded.grant_ids),
                AuditEntry.reason == "oauth grant revoked: agent deleted",
            ),
        )
        assert grant_revokes == 1
    archive, purge, revoke = await _agent_audits(ctx, target)
    assert (archive.action, purge.action, revoke.action) == ("archive", "delete", "revoke")
    assert {archive.reason, purge.reason} == {_PURGE_REASON}
    assert {archive.actor_id, purge.actor_id, revoke.actor_id} == {purge_env.admin_id}
    # The inline archive step audits the permission-grant wipe.
    assert revoke.before == {"permissions": ["apis:read"]}
    assert revoke.after == {"permissions": []}
    assert revoke.reason == "permissions revoked: agent was deleted"
    assert purge.before == {
        "name": "purge-target",
        "owner_id": purge_env.owner_id,
        "status": "active",
        "retained_credential_ids": [seeded.retained_credential_id],
    }
    async with ctx.control_db.session() as session:
        assert await session.get(Credential, purge_env.credential_id) is not None


@pytest.mark.parametrize(
    "status",
    [ActorStatus.PENDING, ActorStatus.ACTIVE, ActorStatus.DISABLED, ActorStatus.ARCHIVED],
)
async def test_purge_allowed_from_any_status_by_owner(
    integration_context: Context, purge_env: _Fixture, status: ActorStatus
) -> None:
    """The owner (agents:write, not org:admin) may purge from every status; an
    agent that is already archived gets no second ``archive`` entry."""
    ctx = integration_context
    agent_id = await purge_env.agent(status=status)

    await AgentService(ctx).purge(agent_id, identity=_owner_identity(purge_env.owner_id))

    async with ctx.admin_db.session() as session:
        assert await AgentRepository.get_by_id(session, agent_id) is None
    expected = ["delete"] if status == ActorStatus.ARCHIVED else ["archive", "delete", "revoke"]
    assert [a.action for a in await _agent_audits(ctx, agent_id)] == expected


async def test_purge_hides_agents_outside_caller_ownership(
    integration_context: Context, purge_env: _Fixture
) -> None:
    """A non-owner without org:admin gets not-found and the agent is untouched;
    a second purge of an already-deleted agent is not-found too."""
    ctx = integration_context
    agent_id = await purge_env.agent()
    stranger = await seeds.seed_user(ctx, "usr_purge_stranger")
    svc = AgentService(ctx)

    with pytest.raises(ActorNotFoundError):
        await svc.purge(agent_id, identity=_owner_identity(stranger))
    async with ctx.admin_db.session() as session:
        agent = await AgentRepository.get_by_id(session, agent_id)
        assert agent is not None and agent.status == ActorStatus.ACTIVE

    await svc.purge(agent_id, identity=_admin_identity(purge_env.admin_id))
    with pytest.raises(ActorNotFoundError):
        await svc.purge(agent_id, identity=_admin_identity(purge_env.admin_id))


def _with_unreachable_control(config: AppConfig, tmp_path: Path) -> AppConfig:
    """``config`` with the control DB pointed at a SQLite file that cannot be opened."""
    control = DatabaseConfig(
        backend="sqlite", path=str(tmp_path / "missing" / "control.db"), schema_name="control"
    )
    databases = config.databases.model_copy(update={"control": control})
    return config.model_copy(update={"databases": databases})


async def test_failed_control_sweep_leaves_agent_archived_and_retry_finishes(
    integration_context: Context,
    integration_config: AppConfig,
    purge_env: _Fixture,
    tmp_path: Path,
) -> None:
    """A control-DB failure propagates: the agent is left archived (step 1
    committed and audited), its control rows survive, and nothing else is
    deleted. A retry from a healthy process completes the purge."""
    ctx = integration_context
    agent_id = await purge_env.agent()
    seeded = await _seed_agent_footprint(ctx, purge_env, agent_id)

    broken = _with_unreachable_control(integration_config, tmp_path)
    async with Context(broken) as broken_ctx:
        with pytest.raises(DatabaseUnavailableError):
            await AgentService(broken_ctx).purge(
                agent_id, identity=_admin_identity(purge_env.admin_id)
            )

    async with ctx.admin_db.session() as session:
        agent = await AgentRepository.get_by_id(session, agent_id)
        assert agent is not None and agent.status == ActorStatus.ARCHIVED
    left = await _swept(ctx, agent_id, seeded)
    assert left["rules"] == 1 and left["sessions"] == 2 and left["active_jobs"] == 2
    assert [a.action for a in await _agent_audits(ctx, agent_id)] == ["archive", "revoke"]

    await AgentService(ctx).purge(agent_id, identity=_admin_identity(purge_env.admin_id))

    assert all(count == 0 for count in (await _swept(ctx, agent_id, seeded)).values())
    assert [a.action for a in await _agent_audits(ctx, agent_id)] == [
        "archive",
        "delete",
        "revoke",
    ]


async def test_purge_refuses_up_front_without_control_db(
    integration_config: AppConfig, integration_context: Context, purge_env: _Fixture
) -> None:
    """A process that cannot reach the control DB never starts a purge."""
    agent_id = await purge_env.agent()
    async with Context(integration_config, allowed_dbs={"admin"}) as admin_only:
        with pytest.raises(RuntimeError, match="control database"):
            await AgentService(admin_only).purge(
                agent_id, identity=_admin_identity(purge_env.admin_id)
            )

    async with integration_context.admin_db.session() as session:
        agent = await AgentRepository.get_by_id(session, agent_id)
        assert agent is not None and agent.status == ActorStatus.ACTIVE


async def test_purge_without_registry_db(
    integration_config: AppConfig, integration_context: Context, purge_env: _Fixture
) -> None:
    """The purge never touches the registry DB, so an auth process without it purges."""
    agent_id = await purge_env.agent()
    async with Context(integration_config, allowed_dbs={"admin", "control"}) as auth_ctx:
        await AgentService(auth_ctx).purge(agent_id, identity=_admin_identity(purge_env.admin_id))

    async with integration_context.admin_db.session() as session:
        assert await AgentRepository.get_by_id(session, agent_id) is None


async def test_concurrent_purges_delete_once(
    integration_context: Context, purge_env: _Fixture
) -> None:
    """Two purges racing on one agent: one deletes it, the other gets not-found."""
    ctx = integration_context
    agent_id = await purge_env.agent()
    await _seed_agent_footprint(ctx, purge_env, agent_id)
    identity = _admin_identity(purge_env.admin_id)

    results = await asyncio.gather(
        AgentService(ctx).purge(agent_id, identity=identity),
        AgentService(ctx).purge(agent_id, identity=identity),
        return_exceptions=True,
    )

    assert sorted(type(r).__name__ for r in results) == ["ActorNotFoundError", "NoneType"]
    assert [a.action for a in await _agent_audits(ctx, agent_id)] == [
        "archive",
        "delete",
        "revoke",
    ]


async def _seed_hold(ctx: Context, agent_id: str, *, state: HeldExecutionState) -> tuple[str, str]:
    """A held execution in ``state`` with its job (``held`` while pending); returns both ids."""
    job_status = JobStatus.HELD if state == HeldExecutionState.PENDING else JobStatus.COMPLETED
    async with ctx.admin_db.transaction() as session:
        job = Job(kind=JobKind.EXECUTION, status=job_status, created_by=agent_id)
        session.add(job)
        await session.flush()
        held = HeldExecution(
            job_id=job.id,
            agent_id=agent_id,
            credential_id="cred_purge_hold",
            api_vendor="stripe",
            api_name="payments",
            api_version="v1",
            method="POST",
            path="/v1/refunds",
            request_fingerprint=generate_ksuid("fp").ljust(64, "0"),
            state=state,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            created_by=agent_id,
        )
        session.add(held)
        await session.flush()
        return str(held.id), str(job.id)


@pytest.mark.parametrize("operation", ["archive", "purge"])
async def test_archive_and_purge_withdraw_pending_holds(
    integration_context: Context, purge_env: _Fixture, operation: str
) -> None:
    """An archived agent's call never runs, so its pending holds leave the review
    queue: withdrawn by the archiving actor, held job cancelled, audited. A decided
    hold is history and stays as it is."""
    ctx = integration_context
    agent_id = await purge_env.agent()
    pending_id, pending_job = await _seed_hold(ctx, agent_id, state=HeldExecutionState.PENDING)
    decided_id, decided_job = await _seed_hold(ctx, agent_id, state=HeldExecutionState.APPROVED)
    identity = _admin_identity(purge_env.admin_id)

    await getattr(AgentService(ctx), operation)(agent_id, identity=identity)

    async with ctx.admin_db.session() as session:
        pending = await session.get(HeldExecution, pending_id)
        decided = await session.get(HeldExecution, decided_id)
        assert pending is not None and decided is not None
        assert pending.state == HeldExecutionState.WITHDRAWN
        assert pending.decided_by == purge_env.admin_id
        assert decided.state == HeldExecutionState.APPROVED
        jobs = {
            j.id: j.status
            for j in (
                await session.execute(select(Job).where(Job.id.in_([pending_job, decided_job])))
            ).scalars()
        }
        assert jobs == {pending_job: JobStatus.CANCELLED, decided_job: JobStatus.COMPLETED}
        withdrawals = (
            (
                await session.execute(
                    select(AuditEntry).where(
                        AuditEntry.target_id == pending_id, AuditEntry.action == "withdraw"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [w.actor_id for w in withdrawals] == [purge_env.admin_id]
