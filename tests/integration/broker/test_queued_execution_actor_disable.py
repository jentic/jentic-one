"""Integration tests: a queued execution never runs for an actor that may no longer execute.

Pins #1138. Each test enqueues an execution job the way the async execute
route does (``enqueue_job``) and changes the enqueuing actor through the real
``AgentService`` / ``UserService`` / ``PermissionService`` (disable, delete,
or revoke the execute permission). The handler is wired as the broker
lifespan wires it: the uncached ``QueuedExecutionAuthorizer``, the live
``ActorStatusResolver`` read. The credential-injection and upstream seams are
recording stand-ins: what is under test is whether the worker reaches them.

A lifecycle disable or delete cancels the actor's *queued* executions in the
same transaction, so the worker never claims them. The worker's run-time
checks are the backstop for the jobs that sweep cannot reach: a job already
claimed (``running``) when the change commits. The claimed-job tests claim
through the real ``WorkerLoop`` first, then change the actor, then run the
claimed job.

The race tests make the change *after* the authorizer has allowed a claimed
job and before credentials are resolved — the window the live actor read
right before injection closes. They run the handler on a session that has not
written yet (``_UnwrittenSession``), as the worker's handler transaction has
not at that point, so the change can commit on SQLite too: a transaction
opened with ``BEGIN IMMEDIATE`` around the handler would hold the single
writer lock the change needs.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.actor_permission_grants import ActorPermissionGrant
from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentRepository, UserPermissionGrantRepository, UserRepository
from jentic_one.admin.services.permission_service import PermissionService
from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.broker.core.setup import build_queued_execution_authorizer
from jentic_one.broker.repos.actor_status import ActorStatusResolver
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    APIS_READ,
    BROKER_EXECUTE_PERMISSION,
    ORG_ADMIN,
)
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

    async def execute(self, request: UpstreamExecRequest, **_: Any) -> UpstreamExecResult:
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


class _ChangesAfterAuthorizing:
    """The real authorizer, then an actor change that commits before injection runs."""

    def __init__(self, inner: ExecutionAuthorizer, change: Callable[[], Awaitable[None]]) -> None:
        self._inner = inner
        self._change = change

    async def authorize(self, request: QueuedExecutionRequest) -> QueuedExecutionVerdict:
        verdict = await self._inner.authorize(request)
        assert verdict.allowed, verdict.problem
        await self._change()
        return verdict


class _UnwrittenSession:
    """A real session that has not written yet, offered as the handler's worker argument.

    It forwards to the session and its ``transaction()`` yields that same
    session, so the handler can write through it directly or open its own
    transaction on it; either way nothing takes a write lock until the
    handler's denial write, after the change under test has committed.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        yield self._session


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


async def _seed_actor(
    ctx: Context, actor_type: ActorType, *, user_permissions: set[str] | None = None
) -> str:
    """An active, credential-bound actor of ``actor_type`` that may execute GET.

    A user holds ``user_permissions`` (default: just the execute permission).
    """
    if actor_type is ActorType.AGENT:
        actor_id = await _seed_agent(ctx)
    else:
        actor_id = await _seed_user(
            ctx, "member", permissions=user_permissions or {BROKER_EXECUTE_PERMISSION}
        )
    await _bind_credential(ctx, actor_id)
    return actor_id


async def _admin(ctx: Context) -> Identity:
    admin_id = await _seed_user(ctx, f"admin-{generate_ksuid('x')}", permissions={ORG_ADMIN})
    return Identity(sub=admin_id, permissions=[ORG_ADMIN])


async def _disable(ctx: Context, actor_type: ActorType, actor_id: str) -> None:
    """Disable through the real lifecycle service, as an ``org:admin``."""
    admin = await _admin(ctx)
    if actor_type is ActorType.AGENT:
        await AgentService(ctx).disable(actor_id, identity=admin)
    else:
        await UserService(ctx).disable(actor_id, identity=admin)


async def _revoke_execute(ctx: Context, actor_type: ActorType, actor_id: str) -> None:
    """Revoke the execute permission through the real grant service, as an ``org:admin``.

    The actor stays active; a user keeps an unrelated permission so the
    revocation is a narrowing, not an emptied grant set.
    """
    admin = await _admin(ctx)
    if actor_type is ActorType.AGENT:
        await AgentService(ctx).replace_permissions(actor_id, [APIS_READ], identity=admin)
    else:
        await PermissionService(ctx).set_assigned(actor_id, [APIS_READ], identity=admin)


_Change = Callable[[Context, ActorType, str], Awaitable[None]]


async def _soft_delete_user(ctx: Context, actor_type: ActorType, actor_id: str) -> None:
    admin_id = await _seed_user(ctx, "deleter", permissions={ORG_ADMIN})
    await UserService(ctx).delete(actor_id, identity=Identity(sub=admin_id))


#: Lifecycle changes that cancel the actor's queued executions (the sync edge's 401).
_LIFECYCLE_CHANGES: list[Any] = [
    pytest.param(ActorType.AGENT, _disable, id="agent_disabled"),
    pytest.param(ActorType.USER, _disable, id="user_disabled"),
    pytest.param(ActorType.USER, _soft_delete_user, id="user_soft_deleted"),
]


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


def _handler(
    ctx: Context,
    executor: _RecordingExecutor,
    injector: _SpyInjector,
    *,
    authorizer: ExecutionAuthorizer | None = None,
) -> ExecutionHandler:
    """The handler wired as the broker lifespan wires it (``authorizer`` overridable)."""
    return ExecutionHandler(
        executor=executor,
        credential_injector=injector,
        execution_authorizer=authorizer or build_queued_execution_authorizer(ctx),
        actor_status=ActorStatusResolver(ctx.admin_db),
    )


def _worker(ctx: Context, handler: ExecutionHandler) -> WorkerLoop:
    registry = JobHandlerRegistry()
    registry.register(JobKind.EXECUTION, handler)
    return WorkerLoop(ctx.admin_db, registry, worker_config=WorkerConfig())


async def _run_worker(
    ctx: Context,
    executor: _RecordingExecutor,
    injector: _SpyInjector,
    *,
    expect_claim: bool = True,
) -> None:
    """One worker tick: claim, run and complete the oldest queued job."""
    claimed = await _worker(ctx, _handler(ctx, executor, injector))._tick()
    assert claimed is expect_claim


async def _claim(loop: WorkerLoop, job_id: str) -> Any:
    """Claim ``job_id`` through the worker; the claim commits it as ``running``."""
    job = await loop._claim_next()
    assert job is not None
    assert job.id == job_id
    assert job.status == JobStatus.RUNNING
    return job


async def _assert_claim_survived(ctx: Context, job_id: str, actor_id: str) -> None:
    """The lifecycle sweep left the claimed job alone and recorded no cancellation."""
    async with ctx.admin_db.session() as session:
        job = await session.get(Job, job_id)
        sweeps = (
            (
                await session.execute(
                    select(AuditEntry).where(
                        AuditEntry.target_id == actor_id,
                        AuditEntry.reason.like("queued execution jobs cancelled%"),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert job is not None
    assert job.status == JobStatus.RUNNING
    assert sweeps == []


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


def _assert_denied(
    job: Job,
    result: JobResult,
    events: list[Event],
    executor: _RecordingExecutor,
    injector: _SpyInjector,
    denial: tuple[str, int] = ("unauthorized", 401),
) -> None:
    """Failed with the sync edge's problem, recorded like any failed execution, never run."""
    problem_type, status = denial
    assert executor.requests == []
    assert injector.calls == 0
    assert job.status == JobStatus.COMPLETED
    assert result.body["status"] == "failed"
    assert result.body["http_status"] == status
    assert result.body["problem"]["type"] == problem_type
    assert result.body["problem"]["instance"] == "/api.acme.com/v1/pets"
    assert [e.type for e in events] == [EventType.EXECUTION_FAILED]
    assert _SECRET not in str(result.body)


@pytest.mark.parametrize(
    ("actor_type", "user_permissions"),
    [
        (ActorType.AGENT, None),
        (ActorType.USER, None),
        # ``org:admin`` implies the execute permission (``compute_effective``).
        (ActorType.USER, {ORG_ADMIN}),
    ],
    ids=["agent", "user", "user_org_admin"],
)
async def test_queued_job_runs_for_an_actor_that_may_execute(
    integration_context: Context,
    clean: None,
    actor_type: ActorType,
    user_permissions: set[str] | None,
) -> None:
    ctx = integration_context
    actor_id = await _seed_actor(ctx, actor_type, user_permissions=user_permissions)
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
async def test_queued_job_denied_when_execute_revoked_before_it_runs(
    integration_context: Context, clean: None, actor_type: ActorType
) -> None:
    """A revocation leaves the actor active, so nothing cancels the queued job:
    the worker claims it and denies it with the sync edge's 403."""
    ctx = integration_context
    actor_id = await _seed_actor(ctx, actor_type)
    job_id = await _enqueue(ctx, actor_id, actor_type)
    await _revoke_execute(ctx, actor_type, actor_id)
    executor, injector = _RecordingExecutor(), _SpyInjector()

    await _run_worker(ctx, executor, injector)

    _assert_denied(
        *await _job_outcome(ctx, job_id), executor, injector, ("insufficient_scope", 403)
    )


@pytest.mark.parametrize(("actor_type", "change"), _LIFECYCLE_CHANGES)
async def test_claimed_job_denied_when_actor_disabled_before_the_handler_runs(
    integration_context: Context, clean: None, actor_type: ActorType, change: _Change
) -> None:
    """A job the worker already claimed is out of the lifecycle sweep's reach
    (it cancels only ``queued`` jobs): the worker's own run-time check denies
    it with the sync edge's 401 — no decrypt, no upstream call."""
    ctx = integration_context
    actor_id = await _seed_actor(ctx, actor_type)
    job_id = await _enqueue(ctx, actor_id, actor_type)
    executor, injector = _RecordingExecutor(), _SpyInjector()
    handler = _handler(ctx, executor, injector)
    loop = _worker(ctx, handler)
    job = await _claim(loop, job_id)

    await change(ctx, actor_type, actor_id)
    await _assert_claim_survived(ctx, job_id, actor_id)
    await loop._complete_job(job.id, job.kind, await loop._execute_handler(handler, job))

    _assert_denied(*await _job_outcome(ctx, job_id), executor, injector)


@pytest.mark.parametrize(
    ("actor_type", "change", "denial"),
    [
        pytest.param(ActorType.AGENT, _disable, ("unauthorized", 401), id="agent_disabled"),
        pytest.param(ActorType.USER, _disable, ("unauthorized", 401), id="user_disabled"),
        pytest.param(
            ActorType.USER, _soft_delete_user, ("unauthorized", 401), id="user_soft_deleted"
        ),
        pytest.param(
            ActorType.AGENT, _revoke_execute, ("insufficient_scope", 403), id="agent_revoked"
        ),
        pytest.param(
            ActorType.USER, _revoke_execute, ("insufficient_scope", 403), id="user_revoked"
        ),
    ],
)
async def test_claimed_job_denied_when_actor_changed_after_authorization(
    integration_context: Context,
    clean: None,
    actor_type: ActorType,
    change: _Change,
    denial: tuple[str, int],
) -> None:
    """The change commits after the authorizer allowed a claimed job: the live
    read right before injection still stops it — no decrypt, no upstream call —
    and logs the pre-injection denial without any credential material."""
    ctx = integration_context
    actor_id = await _seed_actor(ctx, actor_type)
    job_id = await _enqueue(ctx, actor_id, actor_type)
    executor, injector = _RecordingExecutor(), _SpyInjector()

    async def change_now() -> None:
        await change(ctx, actor_type, actor_id)
        await _assert_claim_survived(ctx, job_id, actor_id)

    handler = _handler(
        ctx,
        executor,
        injector,
        authorizer=_ChangesAfterAuthorizing(build_queued_execution_authorizer(ctx), change_now),
    )
    loop = _worker(ctx, handler)
    job = await _claim(loop, job_id)
    with structlog.testing.capture_logs() as logs:
        async with ctx.admin_db.session() as session:
            result = await handler.execute(
                job.id,
                _UnwrittenSession(session),
                payload=job.payload,
                created_by=job.created_by,
                actor_type=job.actor_type,
            )
            await session.commit()
    await loop._complete_job(job.id, job.kind, result)

    _assert_denied(*await _job_outcome(ctx, job_id), executor, injector, denial)
    [denied] = [
        e
        for e in logs
        if e["event"] == "queued_execution_denied" and e.get("stage") == "pre_injection"
    ]
    assert denied["job_id"] == job_id
    assert denied["actor_id"] == actor_id
    assert denied["reason"] == ("actor_inactive" if denial[1] == 401 else "insufficient_scope")
    assert _SECRET not in str(logs)


async def test_queued_job_denied_when_agent_row_deleted(
    integration_context: Context, clean: None
) -> None:
    """A raw row delete skips the lifecycle sweep; the worker denies the job."""
    ctx = integration_context
    actor_id = await _seed_actor(ctx, ActorType.AGENT)
    job_id = await _enqueue(ctx, actor_id, ActorType.AGENT)
    async with ctx.admin_db.transaction() as session:
        await AgentRepository.delete(session, actor_id)
    executor, injector = _RecordingExecutor(), _SpyInjector()

    await _run_worker(ctx, executor, injector)

    _assert_denied(*await _job_outcome(ctx, job_id), executor, injector)


async def test_queued_job_denied_for_an_actor_that_never_existed(
    integration_context: Context, clean: None
) -> None:
    ctx = integration_context
    job_id = await _enqueue(ctx, "agnt_neverexisted", ActorType.AGENT)
    executor, injector = _RecordingExecutor(), _SpyInjector()

    await _run_worker(ctx, executor, injector)

    _assert_denied(*await _job_outcome(ctx, job_id), executor, injector)


@pytest.mark.parametrize(("actor_type", "change"), _LIFECYCLE_CHANGES)
async def test_lifecycle_change_cancels_the_queued_job_before_the_worker_claims_it(
    integration_context: Context,
    clean: None,
    actor_type: ActorType,
    change: _Change,
) -> None:
    """Disable/delete through the lifecycle service cancels the actor's queued
    executions in the same transaction, so the worker never claims the job:
    nothing is decrypted and nothing is sent upstream."""
    ctx = integration_context
    actor_id = await _seed_actor(ctx, actor_type)
    job_id = await _enqueue(ctx, actor_id, actor_type)
    await change(ctx, actor_type, actor_id)
    executor, injector = _RecordingExecutor(), _SpyInjector()

    await _run_worker(ctx, executor, injector, expect_claim=False)

    async with ctx.admin_db.session() as session:
        job = await session.get(Job, job_id)
    assert job is not None
    assert job.status == JobStatus.CANCELLED
    assert executor.requests == []
    assert injector.calls == 0
