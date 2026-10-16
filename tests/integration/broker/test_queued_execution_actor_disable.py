"""Integration tests: a queued execution never runs for an actor that is no longer active.

Pins #1138. Each test enqueues an execution job the way the async execute
route does (``enqueue_job``), changes the enqueuing actor through the real
``AgentService`` / ``UserService``, then lets the real ``WorkerLoop`` claim
the job and run it through ``ExecutionHandler`` wired as the broker lifespan
wires it: the uncached ``QueuedExecutionAuthorizer``, the live
``ActorStatusResolver`` read. The credential-injection and upstream seams are
recording stand-ins: what is under test is whether the worker reaches them.

The race tests disable the actor *after* the authorizer has allowed the job
and before credentials are resolved — the window the live actor read right
before injection closes. They call the handler on a session that has not
written yet (as the worker's does at that point) so the disable can commit on
SQLite too, whose single writer lock the worker's claim transaction would
otherwise hold.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.actor_permission_grants import ActorPermissionGrant
from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentRepository, UserPermissionGrantRepository, UserRepository
from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.broker.core.setup import build_queued_execution_authorizer
from jentic_one.broker.repos.actor_status import ActorStatusResolver
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION, ORG_ADMIN
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.jobs.enqueue import enqueue_job
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.handlers import JobHandlerRegistry
from jentic_one.shared.jobs.protocols import (
    ExecutionAuthorizer,
    InjectedAuth,
    QueuedExecutionRequest,
    QueuedExecutionVerdict,
    UpstreamExecRequest,
    UpstreamExecResult,
)
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models import ActorType, JobKind, JobStatus, StoredCredentialType
from jentic_one.shared.models.events import EventType

pytestmark = pytest.mark.integration

_VENDOR = "acme.com"
_API_NAME = "pets-api"
_API_VERSION = "v1"
_URL = "https://api.acme.com/v1/pets"
_SECRET = "queued-disable-secret"  # pragma: allowlist secret
_EMAIL_DOMAIN = "@queued-disable.test"


class _RecordingExecutor:
    """Stands in for the broker ``PipelineExecutor``: records, never calls out."""

    def __init__(self) -> None:
        self.requests: list[UpstreamExecRequest] = []

    async def execute(self, request: UpstreamExecRequest, *, session: Any) -> UpstreamExecResult:
        self.requests.append(request)
        return UpstreamExecResult(status_code=200, body=b"{}", content_type=None, duration_ms=1)


class _SpyInjector:
    """Stands in for the credential injection seam; counts resolutions.

    The point under test is whether the worker reaches credential resolution
    at all, so the stand-in records the identity it was asked to resolve for
    and returns the material a bound API key would inject.
    """

    def __init__(self) -> None:
        self.identities: list[Identity] = []

    @property
    def calls(self) -> int:
        return len(self.identities)

    async def inject(self, *, identity: Identity, **kwargs: Any) -> InjectedAuth:
        self.identities.append(identity)
        return InjectedAuth(headers={"X-Api-Key": _SECRET}, query_params={}, cookies={})


class _DisablesAfterAuthorizing:
    """The real authorizer, then a disable that commits before injection runs."""

    def __init__(self, inner: ExecutionAuthorizer, disable: Callable[[], Awaitable[None]]) -> None:
        self._inner = inner
        self._disable = disable

    async def authorize(self, request: QueuedExecutionRequest) -> QueuedExecutionVerdict:
        verdict = await self._inner.authorize(request)
        assert verdict.allowed, verdict.problem
        await self._disable()
        return verdict


@pytest.fixture()
async def clean(integration_context: Context) -> AsyncGenerator[None, None]:
    ctx = integration_context

    async def _truncate() -> None:
        async with ctx.admin_db.session() as session:
            await session.execute(delete(JobResult))
            await session.execute(delete(Job))
            await session.execute(delete(AgentCredentialBinding))
            await session.execute(delete(ActorPermissionGrant))
            await session.execute(delete(Agent))
            user_ids = select(User.id).where(User.created_by == "queued-disable")
            await session.execute(
                delete(UserPermissionGrant).where(UserPermissionGrant.user_id.in_(user_ids))
            )
            await session.execute(delete(User).where(User.created_by == "queued-disable"))
            await session.commit()
        async with ctx.control_db.session() as session:
            await session.execute(delete(AgentPermissionRule))
            await session.execute(delete(CustomerAPIKey))
            await session.execute(delete(Credential))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_user(ctx: Context, name: str, *, permissions: set[str] | None = None) -> str:
    async with ctx.admin_db.transaction() as session:
        user = await UserRepository.create(
            session,
            email=f"{name}{_EMAIL_DOMAIN}",
            first_name="Queued",
            last_name=name,
            active=True,
            created_by="queued-disable",
        )
        if permissions:
            await UserPermissionGrantRepository.set_permissions(
                session, user.id, permissions=permissions, created_by="queued-disable"
            )
        return user.id


async def _seed_agent(ctx: Context) -> str:
    """An active agent holding the execute permission."""
    agent = Agent(name="queued-disable-agent", registered_by="usr_owner", status="active")
    async with ctx.admin_db.transaction() as session:
        session.add(agent)
        await session.flush()
        session.add(
            ActorPermissionGrant(
                actor_id=agent.id, actor_type="agent", permission=BROKER_EXECUTE_PERMISSION
            )
        )
        return agent.id


async def _bind_credential(ctx: Context, actor_id: str) -> str:
    """One API-key credential bound to ``actor_id`` with an allow-GET rule."""
    credential = Credential(
        type=StoredCredentialType.API_KEY,
        name="acme-key",
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
    )
    async with ctx.control_db.transaction() as session:
        session.add(credential)
        await session.flush()
        session.add(
            CustomerAPIKey(
                id=generate_ksuid("key"),
                credential_id=credential.id,
                encrypted_key=ctx.encryption.encrypt(_SECRET),
                location="header",
                field_name="X-Api-Key",
            )
        )
        credential_id = credential.id
    async with ctx.admin_db.transaction() as session:
        session.add(
            AgentCredentialBinding(
                id=generate_ksuid("acb"), agent_id=actor_id, credential_id=credential_id
            )
        )
    async with ctx.control_db.transaction() as session:
        session.add(
            AgentPermissionRule(
                agent_id=actor_id,
                credential_id=credential_id,
                effect="allow",
                methods=["GET"],
                path=".*",
                sequence=1,
            )
        )
    return credential_id


async def _seed_actor(ctx: Context, actor_type: ActorType) -> str:
    """An active, credential-bound actor of ``actor_type`` that may execute GET."""
    if actor_type is ActorType.AGENT:
        actor_id = await _seed_agent(ctx)
    else:
        actor_id = await _seed_user(ctx, "member")
    await _bind_credential(ctx, actor_id)
    return actor_id


async def _disable(ctx: Context, actor_type: ActorType, actor_id: str) -> None:
    """Disable through the real lifecycle service, as an ``org:admin``."""
    admin_id = await _seed_user(ctx, f"admin-{generate_ksuid('x')}", permissions={ORG_ADMIN})
    admin = Identity(sub=admin_id, permissions=[ORG_ADMIN])
    if actor_type is ActorType.AGENT:
        await AgentService(ctx).disable(actor_id, identity=admin)
    else:
        await UserService(ctx).disable(actor_id, identity=admin)


async def _enqueue(ctx: Context, actor_id: str, actor_type: ActorType) -> str:
    """Queue an execution the way the async execute route does."""
    payload = {
        "execution_id": generate_ksuid("exec"),
        "upstream_url": _URL,
        "method": "GET",
        "trace_id": "d" * 32,
        "api_vendor": _VENDOR,
        "api_name": _API_NAME,
        "api_version": _API_VERSION,
    }
    async with ctx.admin_db.transaction() as session:
        return await enqueue_job(
            session,
            JobKind.EXECUTION,
            created_by=actor_id,
            actor_type=actor_type,
            payload=payload,
        )


async def _run_worker(
    ctx: Context,
    executor: _RecordingExecutor,
    injector: _SpyInjector,
) -> None:
    """One worker tick with the handler wired as the broker lifespan wires it."""
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=injector,
        execution_authorizer=build_queued_execution_authorizer(ctx),
        actor_status=ActorStatusResolver(ctx.admin_db),
    )
    registry = JobHandlerRegistry()
    registry.register(JobKind.EXECUTION, handler)
    assert await WorkerLoop(ctx.admin_db, registry, worker_config=WorkerConfig())._tick()


async def _job_outcome(ctx: Context, job_id: str) -> tuple[Job, JobResult, list[Event]]:
    async with ctx.admin_db.session() as session:
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
        result = (
            await session.execute(select(JobResult).where(JobResult.job_id == job_id))
        ).scalar_one()
        events = list(
            (await session.execute(select(Event).where(Event.job_id == job_id))).scalars().all()
        )
    return job, result, events


def _assert_denied_inactive(
    job: Job,
    result: JobResult,
    events: list[Event],
    executor: _RecordingExecutor,
    injector: _SpyInjector,
) -> None:
    """Failed as the sync edge's 401, recorded like any failed execution, never run."""
    assert executor.requests == []
    assert injector.calls == 0
    assert job.status == JobStatus.COMPLETED
    assert result.body["status"] == "failed"
    assert result.body["http_status"] == 401
    assert result.body["problem"]["type"] == "unauthorized"
    assert result.body["problem"]["instance"] == "/api.acme.com/v1/pets"
    assert [e.type for e in events] == [EventType.EXECUTION_FAILED]
    assert _SECRET not in str(result.body)


@pytest.mark.parametrize("actor_type", [ActorType.AGENT, ActorType.USER])
async def test_queued_job_runs_for_an_active_actor(
    integration_context: Context, clean: None, actor_type: ActorType
) -> None:
    ctx = integration_context
    actor_id = await _seed_actor(ctx, actor_type)
    job_id = await _enqueue(ctx, actor_id, actor_type)
    executor, injector = _RecordingExecutor(), _SpyInjector()

    await _run_worker(ctx, executor, injector)

    job, result, events = await _job_outcome(ctx, job_id)
    assert injector.calls == 1
    assert injector.identities[0].sub == actor_id
    assert injector.identities[0].active is True
    assert len(executor.requests) == 1
    assert executor.requests[0].headers["X-Api-Key"] == _SECRET
    assert job.status == JobStatus.COMPLETED
    assert result.body["status"] == "completed"
    assert result.body["http_status"] == 200
    assert EventType.EXECUTION_COMPLETED in [e.type for e in events]


@pytest.mark.parametrize("actor_type", [ActorType.AGENT, ActorType.USER])
async def test_queued_job_denied_when_actor_disabled_before_it_runs(
    integration_context: Context, clean: None, actor_type: ActorType
) -> None:
    ctx = integration_context
    actor_id = await _seed_actor(ctx, actor_type)
    job_id = await _enqueue(ctx, actor_id, actor_type)
    await _disable(ctx, actor_type, actor_id)
    executor, injector = _RecordingExecutor(), _SpyInjector()

    await _run_worker(ctx, executor, injector)

    _assert_denied_inactive(*await _job_outcome(ctx, job_id), executor, injector)


@pytest.mark.parametrize("actor_type", [ActorType.AGENT, ActorType.USER])
async def test_queued_job_denied_when_actor_disabled_after_authorization(
    integration_context: Context, clean: None, actor_type: ActorType
) -> None:
    """The disable commits after the authorizer allowed the job: the live actor
    read right before injection still stops it — no decrypt, no upstream call."""
    ctx = integration_context
    actor_id = await _seed_actor(ctx, actor_type)
    job_id = await _enqueue(ctx, actor_id, actor_type)
    executor, injector = _RecordingExecutor(), _SpyInjector()

    async def disable() -> None:
        await _disable(ctx, actor_type, actor_id)

    handler = ExecutionHandler(
        executor=executor,
        credential_injector=injector,
        execution_authorizer=_DisablesAfterAuthorizing(
            build_queued_execution_authorizer(ctx), disable
        ),
        actor_status=ActorStatusResolver(ctx.admin_db),
    )
    async with ctx.admin_db.session() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        payload, created_by, job_actor_type = job.payload, job.created_by, job.actor_type
    async with ctx.admin_db.session() as session:
        result = await handler.execute(
            job_id, session, payload=payload, created_by=created_by, actor_type=job_actor_type
        )
        await session.commit()

    assert executor.requests == []
    assert injector.calls == 0
    assert result.body["status"] == "failed"
    assert result.body["http_status"] == 401
    assert result.body["problem"]["type"] == "unauthorized"
    async with ctx.admin_db.session() as session:
        events = (await session.execute(select(Event).where(Event.job_id == job_id))).scalars()
        assert [e.type for e in events] == [EventType.EXECUTION_FAILED]


async def _delete_agent(ctx: Context, actor_id: str) -> None:
    async with ctx.admin_db.transaction() as session:
        await AgentRepository.delete(session, actor_id)


async def _delete_user(ctx: Context, actor_id: str) -> None:
    admin_id = await _seed_user(ctx, "deleter", permissions={ORG_ADMIN})
    await UserService(ctx).delete(actor_id, identity=Identity(sub=admin_id))


@pytest.mark.parametrize(
    ("actor_type", "remove"),
    [
        (ActorType.AGENT, _delete_agent),
        (ActorType.USER, _delete_user),
    ],
    ids=["agent_row_deleted", "user_soft_deleted"],
)
async def test_queued_job_denied_when_actor_deleted(
    integration_context: Context,
    clean: None,
    actor_type: ActorType,
    remove: Callable[[Context, str], Awaitable[None]],
) -> None:
    ctx = integration_context
    actor_id = await _seed_actor(ctx, actor_type)
    job_id = await _enqueue(ctx, actor_id, actor_type)
    await remove(ctx, actor_id)
    executor, injector = _RecordingExecutor(), _SpyInjector()

    await _run_worker(ctx, executor, injector)

    _assert_denied_inactive(*await _job_outcome(ctx, job_id), executor, injector)


async def test_queued_job_denied_for_an_actor_that_never_existed(
    integration_context: Context, clean: None
) -> None:
    ctx = integration_context
    job_id = await _enqueue(ctx, "agnt_neverexisted", ActorType.AGENT)
    executor, injector = _RecordingExecutor(), _SpyInjector()

    await _run_worker(ctx, executor, injector)

    _assert_denied_inactive(*await _job_outcome(ctx, job_id), executor, injector)
