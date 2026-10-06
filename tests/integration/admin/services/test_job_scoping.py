"""Integration tests for owner scoping of job reads, results and cancellation.

A job is visible to the actor that created it, to the human owner of the agent
that created it, and to ``org:admin``. Everyone else sees a 404-equivalent
``JobNotFoundError`` — on the service layer and on the MCP
``get_execution_result`` tool, which reuses the same services.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    AgentRepository,
    JobRepository,
    JobResultRepository,
    UserRepository,
)
from jentic_one.admin.services.errors import JobNotFoundError
from jentic_one.admin.services.job_result_service import JobResultService
from jentic_one.admin.services.job_service import JobService
from jentic_one.admin.services.schemas.jobs import JobFilter
from jentic_one.mcp.envelopes import ToolError
from jentic_one.mcp.tools import CallEnv, handle_get_execution_result
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType, InviteState, JobKind, JobStatus

pytestmark = pytest.mark.integration

_OTHER_SUB = "usr_jobscope_other"
_OTHER_AGENT_SUB = "agnt_jobscope_other"


@dataclass(frozen=True)
class _Seed:
    owner_id: str
    owner_job_id: str
    agent_job_id: str
    agent_id: str


def _user(sub: str, *permissions: str) -> Identity:
    return Identity(sub=sub, email=f"{sub}@test.local", permissions=list(permissions))


def _agent(sub: str, *, parent: str | None, permissions: list[str] | None = None) -> Identity:
    return Identity(
        sub=sub,
        permissions=permissions or ["jobs:read", "jobs:write", "owner:agents:read"],
        actor_type=ActorType.AGENT,
        parent_actor_id=parent,
    )


@pytest.fixture()
async def seed(integration_context: Context) -> AsyncGenerator[_Seed, None]:
    """One completed execution job per actor: the owner's and the owner's agent's."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        await session.execute(delete(JobResult))
        await session.execute(delete(Job))
        owner = await UserRepository.create(
            session,
            email="jobscope-owner@test.local",
            first_name="Job",
            last_name="Owner",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        agent = await AgentRepository.create(
            session,
            name="jobscope-agent",
            owner_id=owner.id,
            registered_by=owner.id,
            created_by=owner.id,
            status=ActorStatus.ACTIVE,
        )
        owner_job = await JobRepository.create(
            session, kind=JobKind.EXECUTION, status=JobStatus.COMPLETED, created_by=owner.id
        )
        agent_job = await JobRepository.create(
            session, kind=JobKind.EXECUTION, status=JobStatus.COMPLETED, created_by=agent.id
        )
        for job in (owner_job, agent_job):
            await JobResultRepository.create(
                session,
                job_id=job.id,
                kind="execution",
                body={"secret": job.id},
                available_until=datetime.now(UTC) + timedelta(days=1),
                created_by=owner.id,
            )
        await session.commit()
        seeded = _Seed(
            owner_id=owner.id,
            owner_job_id=owner_job.id,
            agent_job_id=agent_job.id,
            agent_id=agent.id,
        )

    yield seeded

    async with ctx.admin_db.session() as session:
        await session.execute(delete(JobResult))
        await session.execute(delete(Job))
        await session.execute(delete(Agent).where(Agent.id == seeded.agent_id))
        await session.execute(delete(User).where(User.id == seeded.owner_id))
        await session.commit()


async def _listed_ids(ctx: Context, identity: Identity) -> set[str]:
    page = await JobService(ctx).list_all(JobFilter(), identity=identity, limit=100)
    return {j.id for j in page.data}


async def test_other_actors_cannot_see_list_get_or_read_result(
    integration_context: Context, seed: _Seed
) -> None:
    ctx = integration_context
    outsiders = [
        _user(_OTHER_SUB, "jobs:read"),
        _agent(_OTHER_AGENT_SUB, parent=_OTHER_SUB),
        # An agent never inherits its owner's (or a sibling agent's) jobs.
        _agent(_OTHER_AGENT_SUB, parent=seed.owner_id),
    ]
    for identity in outsiders:
        assert await _listed_ids(ctx, identity) == set()
        for job_id in (seed.owner_job_id, seed.agent_job_id):
            with pytest.raises(JobNotFoundError):
                await JobService(ctx).get_by_id(job_id, identity=identity)
            with pytest.raises(JobNotFoundError):
                await JobResultService(ctx).get(job_id, identity=identity)


async def test_other_actor_cannot_cancel(integration_context: Context, seed: _Seed) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        queued = await JobRepository.create(
            session, kind=JobKind.EXECUTION, status=JobStatus.QUEUED, created_by=seed.owner_id
        )
        await session.commit()

    with pytest.raises(JobNotFoundError):
        await JobService(ctx).cancel(queued.id, identity=_user(_OTHER_SUB, "jobs:write"))
    still = await JobService(ctx).get_by_id(queued.id, identity=_user(seed.owner_id))
    assert still.status == JobStatus.QUEUED

    cancelled = await JobService(ctx).cancel(queued.id, identity=_user(seed.owner_id))
    assert cancelled.status == JobStatus.CANCELLED


async def test_owner_can_cancel_their_agents_job(integration_context: Context, seed: _Seed) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        queued = await JobRepository.create(
            session, kind=JobKind.EXECUTION, status=JobStatus.QUEUED, created_by=seed.agent_id
        )
        await session.commit()

    # A sibling identity under the same owner is still an outsider.
    with pytest.raises(JobNotFoundError):
        await JobService(ctx).cancel(
            queued.id, identity=_agent(_OTHER_AGENT_SUB, parent=seed.owner_id)
        )

    cancelled = await JobService(ctx).cancel(queued.id, identity=_user(seed.owner_id))
    assert cancelled.status == JobStatus.CANCELLED


async def test_creator_owner_and_admin_visibility(
    integration_context: Context, seed: _Seed
) -> None:
    ctx = integration_context
    both = {seed.owner_job_id, seed.agent_job_id}

    # The creating agent sees only its own job.
    agent = _agent(seed.agent_id, parent=seed.owner_id)
    assert await _listed_ids(ctx, agent) == {seed.agent_job_id}
    result = await JobResultService(ctx).get(seed.agent_job_id, identity=agent)
    assert result.body == {"secret": seed.agent_job_id}
    with pytest.raises(JobNotFoundError):
        await JobResultService(ctx).get(seed.owner_job_id, identity=agent)

    # The human owner sees their own job and their agent's job.
    owner = _user(seed.owner_id, "jobs:read")
    assert await _listed_ids(ctx, owner) == both
    for job_id in both:
        assert (await JobService(ctx).get_by_id(job_id, identity=owner)).id == job_id
        assert (await JobResultService(ctx).get(job_id, identity=owner)).body == {"secret": job_id}

    # org:admin is unrestricted.
    admin = _user("usr_jobscope_admin", "org:admin")
    assert await _listed_ids(ctx, admin) == both
    assert (await JobResultService(ctx).get(seed.owner_job_id, identity=admin)).body == {
        "secret": seed.owner_job_id
    }


def _env(ctx: Context, identity: Identity) -> CallEnv:
    return CallEnv(
        ctx=ctx, identity=identity, credential="tok", base_url="http://test", session_id=None
    )


async def test_mcp_get_execution_result_is_owner_scoped(
    integration_context: Context, seed: _Seed
) -> None:
    ctx = integration_context
    outsider = _agent(_OTHER_AGENT_SUB, parent=_OTHER_SUB)
    with pytest.raises(ToolError, match="not found"):
        await handle_get_execution_result(_env(ctx, outsider), {"job_id": seed.agent_job_id})

    creator = _agent(seed.agent_id, parent=seed.owner_id)
    result = await handle_get_execution_result(_env(ctx, creator), {"job_id": seed.agent_job_id})
    payload = json.loads(result.content[0].text)  # type: ignore[union-attr]
    assert payload["status"] == JobStatus.COMPLETED
    assert "result_error" not in payload
    assert seed.agent_job_id in json.dumps(payload["result"])
