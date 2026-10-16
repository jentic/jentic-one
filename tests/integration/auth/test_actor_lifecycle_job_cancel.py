"""Integration tests: disabling/archiving an actor cancels its queued execution jobs (#1674).

``AgentService.disable``/``archive`` and ``UserService.disable``/``delete``
cancel the actor's *queued* execution jobs in the same admin transaction as
the status change, and record one ``UPDATE`` audit entry on the actor with the
count. Running executions (left to the worker's execution-time actor check),
other job kinds, finished jobs and other actors' jobs are untouched, and
re-enabling the actor does not requeue anything.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, or_, select

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos import JobRepository
from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import JobKind, JobStatus
from tests.integration.auth import seeds

pytestmark = pytest.mark.integration

_CANCEL_REASON_PREFIX = "queued execution jobs cancelled: "


def _admin_identity(admin_id: str) -> Identity:
    return Identity(
        sub=admin_id,
        email="admin@job-cancel.test",
        permissions=["agents:write", "agents:read", "users:write", "org:admin"],
    )


@dataclass
class _Env:
    ctx: Context
    admin_id: str
    owner_id: str
    job_ids: list[str] = field(default_factory=list)
    actor_ids: list[str] = field(default_factory=list)

    async def job(
        self,
        created_by: str,
        *,
        kind: JobKind = JobKind.EXECUTION,
        status: JobStatus = JobStatus.QUEUED,
        requeued: bool = False,
    ) -> str:
        """Seed one job; ``requeued`` mimics a failed attempt waiting out its backoff."""
        async with self.ctx.admin_db.transaction() as session:
            job = await JobRepository.create(
                session, kind=kind, status=status, created_by=created_by
            )
            if requeued:
                job.visible_at = datetime.now(UTC) + timedelta(minutes=5)
                job.error = "upstream timed out"
                job.attempts = 1
            elif status == JobStatus.RUNNING:
                job.visible_at = datetime.now(UTC) + timedelta(minutes=5)
                job.attempts = 1
            job_id = str(job.id)
        self.job_ids.append(job_id)
        return job_id


@pytest.fixture()
async def env(integration_context: Context, clean_grants: None) -> AsyncGenerator[_Env, None]:
    ctx = integration_context
    admin_id = await seeds.seed_user(ctx, "usr_jobcancel_admin")
    await seeds.seed_permissions(ctx, admin_id, ["org:admin"])
    owner_id = await seeds.seed_user(ctx, "usr_jobcancel_owner")
    environment = _Env(ctx=ctx, admin_id=admin_id, owner_id=owner_id)
    yield environment
    actor_ids = [admin_id, owner_id, *environment.actor_ids]
    async with ctx.admin_db.transaction() as session:
        await session.execute(delete(Job).where(Job.id.in_(environment.job_ids)))
        await session.execute(
            delete(AuditEntry).where(
                or_(AuditEntry.actor_id.in_(actor_ids), AuditEntry.target_id.in_(actor_ids))
            )
        )


@dataclass(frozen=True)
class _Lifecycle:
    """One actor lifecycle action under test, plus its inverse when reversible."""

    target_type: str
    cause: str
    seed: Callable[[_Env], Awaitable[str]]
    act: Callable[[_Env, str], Awaitable[None]]
    undo: Callable[[_Env, str], Awaitable[None]] | None


async def _seed_agent(env: _Env) -> str:
    agent_id = await seeds.seed_agent(env.ctx, owner_id=env.owner_id, scopes=["apis:read"])
    env.actor_ids.append(agent_id)
    return agent_id


async def _seed_user(env: _Env) -> str:
    user_id = await seeds.seed_user(env.ctx, f"usr_jobcancel_{len(env.actor_ids)}")
    env.actor_ids.append(user_id)
    return user_id


def _agents(env: _Env) -> AgentService:
    return AgentService(env.ctx)


def _users(env: _Env) -> UserService:
    return UserService(env.ctx)


async def _agent_disable(env: _Env, actor_id: str) -> None:
    await _agents(env).disable(actor_id, identity=_admin_identity(env.admin_id))


async def _agent_enable(env: _Env, actor_id: str) -> None:
    await _agents(env).enable(actor_id, identity=_admin_identity(env.admin_id))


async def _agent_archive(env: _Env, actor_id: str) -> None:
    await _agents(env).archive(actor_id, identity=_admin_identity(env.admin_id))


async def _user_disable(env: _Env, actor_id: str) -> None:
    await _users(env).disable(actor_id, identity=_admin_identity(env.admin_id))


async def _user_enable(env: _Env, actor_id: str) -> None:
    await _users(env).enable(actor_id, identity=_admin_identity(env.admin_id))


async def _user_delete(env: _Env, actor_id: str) -> None:
    await _users(env).delete(actor_id, identity=_admin_identity(env.admin_id))


_LIFECYCLES = {
    "agent-disable": _Lifecycle(
        "agent", "was disabled", _seed_agent, _agent_disable, _agent_enable
    ),
    "agent-archive": _Lifecycle("agent", "was archived", _seed_agent, _agent_archive, None),
    "user-disable": _Lifecycle("user", "was disabled", _seed_user, _user_disable, _user_enable),
    "user-delete": _Lifecycle("user", "was deleted", _seed_user, _user_delete, None),
}


async def _statuses(ctx: Context, job_ids: list[str]) -> dict[str, tuple[str, datetime | None]]:
    async with ctx.admin_db.session() as session:
        rows = await session.execute(select(Job).where(Job.id.in_(job_ids)))
        return {job.id: (job.status, job.visible_at) for job in rows.scalars()}


async def _cancel_audits(ctx: Context, actor_id: str) -> list[AuditEntry]:
    async with ctx.admin_db.session() as session:
        rows = await session.execute(
            select(AuditEntry).where(
                AuditEntry.target_id == actor_id,
                AuditEntry.reason.startswith(_CANCEL_REASON_PREFIX),
            )
        )
        return list(rows.scalars())


@pytest.mark.parametrize("lifecycle", list(_LIFECYCLES.values()), ids=list(_LIFECYCLES))
async def test_lifecycle_cancels_only_the_actors_queued_execution_jobs(
    env: _Env, lifecycle: _Lifecycle
) -> None:
    """Only ``created_by = actor AND kind = execution AND status = queued`` is
    cancelled; the claim deadline is cleared; one audit entry carries the count."""
    actor_id = await lifecycle.seed(env)
    bystander_id = await lifecycle.seed(env)

    queued = await env.job(actor_id)
    requeued = await env.job(actor_id, requeued=True)
    untouched = {
        "running": await env.job(actor_id, status=JobStatus.RUNNING),
        "completed": await env.job(actor_id, status=JobStatus.COMPLETED),
        "failed": await env.job(actor_id, status=JobStatus.FAILED),
        "queued_import": await env.job(actor_id, kind=JobKind.IMPORT),
        "bystander_queued": await env.job(bystander_id),
        "owner_queued": await env.job(env.owner_id),
    }
    before = await _statuses(env.ctx, list(untouched.values()))

    await lifecycle.act(env, actor_id)

    after = await _statuses(env.ctx, [queued, requeued, *untouched.values()])
    assert after[queued] == (JobStatus.CANCELLED, None)
    assert after[requeued] == (JobStatus.CANCELLED, None)
    assert {job_id: after[job_id] for job_id in untouched.values()} == before

    audits = await _cancel_audits(env.ctx, actor_id)
    assert len(audits) == 1
    audit = audits[0]
    assert audit.action == "update"
    assert audit.target_type == lifecycle.target_type
    assert audit.actor_id == env.admin_id
    assert audit.reason == f"{_CANCEL_REASON_PREFIX}{lifecycle.target_type} {lifecycle.cause}"
    assert audit.after == {
        "cancelled_queued_execution_jobs": 2,
        "job_ids": sorted([queued, requeued]),
    }
    assert await _cancel_audits(env.ctx, bystander_id) == []


@pytest.mark.parametrize("lifecycle", list(_LIFECYCLES.values()), ids=list(_LIFECYCLES))
async def test_lifecycle_without_queued_jobs_records_no_cancel_audit(
    env: _Env, lifecycle: _Lifecycle
) -> None:
    """An actor with nothing queued gets no cancel audit entry."""
    actor_id = await lifecycle.seed(env)
    running = await env.job(actor_id, status=JobStatus.RUNNING)

    await lifecycle.act(env, actor_id)

    assert (await _statuses(env.ctx, [running]))[running][0] == JobStatus.RUNNING
    assert await _cancel_audits(env.ctx, actor_id) == []


@pytest.mark.parametrize(
    "lifecycle",
    [lc for lc in _LIFECYCLES.values() if lc.undo is not None],
    ids=[name for name, lc in _LIFECYCLES.items() if lc.undo is not None],
)
async def test_reenable_does_not_resurrect_cancelled_jobs(env: _Env, lifecycle: _Lifecycle) -> None:
    """Cancellation is final: enable leaves the cancelled jobs cancelled, and a
    second disable finds nothing to cancel."""
    assert lifecycle.undo is not None
    actor_id = await lifecycle.seed(env)
    queued = await env.job(actor_id)

    await lifecycle.act(env, actor_id)
    await lifecycle.undo(env, actor_id)

    assert (await _statuses(env.ctx, [queued]))[queued] == (JobStatus.CANCELLED, None)

    await lifecycle.act(env, actor_id)

    assert (await _statuses(env.ctx, [queued]))[queued] == (JobStatus.CANCELLED, None)
    assert len(await _cancel_audits(env.ctx, actor_id)) == 1
