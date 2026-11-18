"""Integration tests for JobResultService against real PostgreSQL."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

import jentic_one.mcp.execute as mcp_ex
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.held_executions import HeldExecution
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    AgentRepository,
    JobRepository,
    JobResultRepository,
    UserRepository,
)
from jentic_one.admin.services.errors import (
    JobNotCompletedError,
    JobNotFoundError,
    JobResultExpiredError,
)
from jentic_one.admin.services.held_execution_service import HeldExecutionService
from jentic_one.admin.services.job_result_service import JobResultService
from jentic_one.admin.services.job_service import JobService
from jentic_one.admin.services.schemas.held_executions import DecideInput
from jentic_one.admin.services.schemas.jobs import JobFilter
from jentic_one.admin.web.app import create_app
from jentic_one.mcp.envelopes import ToolError
from jentic_one.mcp.tools import CallEnv, handle_get_execution_result, job_poll_payload
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.context import Context
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.handlers import JobHandlerRegistry
from jentic_one.shared.jobs.hold import ENCRYPTED_PAYLOAD_KEY, file_hold
from jentic_one.shared.jobs.protocols import (
    QueuedExecutionRequest,
    QueuedExecutionVerdict,
    UpstreamExecRequest,
    UpstreamExecResult,
)
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models import ActorStatus, ActorType, InviteState, JobKind, JobStatus
from jentic_one.shared.models.held_executions import ApprovalDecision
from jentic_one.shared.web.deps import resolve_identity

pytestmark = pytest.mark.integration

_OWNER = Identity(sub="usr_test", email="test@local")


@pytest.fixture()
async def clean_jobs(integration_context: Context) -> AsyncGenerator[None, None]:
    async def _wipe() -> None:
        async with integration_context.admin_db.session() as session:
            await session.execute(delete(HeldExecution))
            await session.execute(delete(JobResult))
            await session.execute(delete(Job))
            await session.execute(delete(Event))
            await session.commit()

    await _wipe()
    yield
    await _wipe()


async def test_get_result(integration_context: Context, clean_jobs: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        job = await JobRepository.create(
            session, kind=JobKind.IMPORT, status=JobStatus.COMPLETED, created_by="usr_test"
        )
        await JobResultRepository.create(
            session,
            job_id=job.id,
            kind="import",
            body={"records_imported": 42},
            available_until=datetime.now(UTC) + timedelta(days=7),
            created_by="usr_test",
        )
        await session.commit()
    job_id = job.id

    service = JobResultService(ctx)
    view = await service.get(job_id, identity=_OWNER)
    assert view.kind == "import"
    assert view.body == {"records_imported": 42}
    assert view.job_id == job_id


async def test_get_job_not_found(integration_context: Context, clean_jobs: None) -> None:
    service = JobResultService(integration_context)
    with pytest.raises(JobNotFoundError):
        await service.get("job_nonexistent0000000000", identity=_OWNER)


async def test_get_job_not_completed(integration_context: Context, clean_jobs: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        job = await JobRepository.create(
            session, kind=JobKind.IMPORT, status=JobStatus.RUNNING, created_by="usr_test"
        )
        await session.commit()
    job_id = job.id

    service = JobResultService(ctx)
    with pytest.raises(JobNotCompletedError):
        await service.get(job_id, identity=_OWNER)


async def test_get_result_expired(integration_context: Context, clean_jobs: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        job = await JobRepository.create(
            session, kind=JobKind.IMPORT, status=JobStatus.COMPLETED, created_by="usr_test"
        )
        await JobResultRepository.create(
            session,
            job_id=job.id,
            kind="import",
            body={},
            available_until=datetime.now(UTC) - timedelta(days=1),
            created_by="usr_test",
        )
        await session.commit()
    job_id = job.id

    service = JobResultService(ctx)
    with pytest.raises(JobResultExpiredError):
        await service.get(job_id, identity=_OWNER)


async def test_get_result_missing(integration_context: Context, clean_jobs: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        job = await JobRepository.create(
            session, kind=JobKind.IMPORT, status=JobStatus.COMPLETED, created_by="usr_test"
        )
        await session.commit()
    job_id = job.id

    service = JobResultService(ctx)
    with pytest.raises(JobResultExpiredError):
        await service.get(job_id, identity=_OWNER)


class _StubUpstreamExecutor:
    """Out-of-process upstream boundary stand-in returning a canned HTTP response or raising."""

    def __init__(self, outcome: UpstreamExecResult | Exception) -> None:
        self._outcome = outcome

    async def execute(self, request: UpstreamExecRequest, *, db: Any) -> UpstreamExecResult:
        assert request.url.startswith("https://api.example.com/")
        assert db is not None
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _DenyingAuthorizer:
    """Run-time execution authorizer that refuses the released job with a broker problem."""

    def __init__(self, problem: dict[str, Any]) -> None:
        self._problem = problem

    async def authorize(self, request: QueuedExecutionRequest) -> QueuedExecutionVerdict:
        assert request.actor_id
        return QueuedExecutionVerdict(allowed=False, problem=self._problem)


class _RaisingHandler:
    """Job handler that raises before dispatch, leaving no stored JobResult."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def execute(
        self,
        job_id: str,
        db: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> Any:
        assert job_id and db is not None
        raise self._exc


@asynccontextmanager
async def _admin_http(ctx: Context, identity: Identity) -> AsyncIterator[AsyncClient]:
    app = create_app(ctx)
    app.dependency_overrides[resolve_identity] = lambda: identity
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as c:
        yield c


def _mcp_env(ctx: Context, identity: Identity) -> CallEnv:
    return CallEnv(
        ctx=ctx,
        identity=identity,
        credential="jak_test",
        base_url="https://testserver",
        session_id=None,
    )


@pytest.fixture()
async def approved_hold_actors(
    integration_context: Context, clean_jobs: None
) -> AsyncGenerator[dict[str, Identity], None]:
    ctx = integration_context
    async with ctx.admin_db.transaction() as session:
        owner = await UserRepository.create(
            session,
            email="approved-fail-owner@test.local",
            first_name="Approved",
            last_name="Owner",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        outsider = await UserRepository.create(
            session,
            email="approved-fail-outsider@test.local",
            first_name="Out",
            last_name="Sider",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        agent = await AgentRepository.create(
            session,
            name="approved-fail-agent",
            owner_id=owner.id,
            registered_by=owner.id,
            created_by=owner.id,
            status=ActorStatus.ACTIVE,
        )
        sibling = await AgentRepository.create(
            session,
            name="approved-fail-sibling",
            owner_id=owner.id,
            registered_by=owner.id,
            created_by=owner.id,
            status=ActorStatus.ACTIVE,
        )
        owner_id, outsider_id, agent_id, sibling_id = (
            owner.id,
            outsider.id,
            agent.id,
            sibling.id,
        )

    yield {
        "owner": Identity(
            sub=owner_id, email="approved-fail-owner@test.local", permissions=["jobs:read"]
        ),
        "outsider": Identity(
            sub=outsider_id,
            email="approved-fail-outsider@test.local",
            permissions=["jobs:read"],
        ),
        "agent": Identity(
            sub=agent_id,
            actor_type=ActorType.AGENT,
            parent_actor_id=owner_id,
            permissions=["jobs:read"],
        ),
        "sibling": Identity(
            sub=sibling_id,
            actor_type=ActorType.AGENT,
            parent_actor_id=owner_id,
            permissions=["jobs:read", "owner:agents:read"],
        ),
        "admin": Identity(sub="usr_approved_fail_admin", permissions=["org:admin"]),
    }

    async with ctx.admin_db.transaction() as session:
        await session.execute(delete(HeldExecution))
        await session.execute(delete(JobResult))
        await session.execute(delete(Job))
        await session.execute(delete(Agent).where(Agent.id.in_([agent_id, sibling_id])))
        await session.execute(delete(User).where(User.id.in_([owner_id, outsider_id])))


async def _file_and_approve_hold(
    ctx: Context,
    *,
    agent: Identity,
    owner: Identity,
    path: str = "/v1/charges",
    execution_id: str = "exec_approved_fail_1",
) -> str:
    raw_payload = {
        "method": "POST",
        "upstream_url": f"https://api.example.com{path}",
        "execution_id": execution_id,
        "api_vendor": "api.example.com",
        "api_name": "payments",
        "api_version": "1",
        "body_b64": base64.b64encode(b'{"amount": 500}').decode(),
    }
    async with ctx.admin_db.transaction() as session:
        hold = await file_hold(
            session,
            agent_id=agent.sub,
            actor_type=agent.actor_type.value,
            credential_id="cred_approved_fail",
            matched_rule_id="apr_approved_fail",
            api_vendor="api.example.com",
            api_name="payments",
            api_version="1",
            operation_id="createCharge",
            method="POST",
            path=path,
            body=b'{"amount": 500}',
            trace_id=None,
            execution_id=execution_id,
            payload={ENCRYPTED_PAYLOAD_KEY: ctx.encryption.encrypt(json.dumps(raw_payload))},
            ttl_seconds=3600,
            max_pending=10,
        )
    await HeldExecutionService(ctx).decide(
        hold.approval_id,
        DecideInput(decision=ApprovalDecision.APPROVE, reason="approved for test"),
        identity=owner,
    )
    return hold.job_id


async def test_approved_execution_upstream_failure_exposes_result_across_admin_and_mcp(
    integration_context: Context, approved_hold_actors: dict[str, Identity]
) -> None:
    """Pins #2030: approved upstream failures expose their stored JobResult and keep 404 scoping."""
    ctx = integration_context
    agent = approved_hold_actors["agent"]
    owner = approved_hold_actors["owner"]
    outsider = approved_hold_actors["outsider"]
    sibling = approved_hold_actors["sibling"]
    admin = approved_hold_actors["admin"]

    job_id = await _file_and_approve_hold(
        ctx, agent=agent, owner=owner, path="/v1/charges", execution_id="exec_up_422"
    )

    upstream_error_bytes = b'{"error":{"code":"card_declined","message":"Insufficient funds"}}'
    registry = JobHandlerRegistry()
    registry.register(
        JobKind.EXECUTION,
        ExecutionHandler(
            executor=_StubUpstreamExecutor(
                UpstreamExecResult(
                    status_code=422,
                    body=upstream_error_bytes,
                    content_type="application/json",
                    duration_ms=34,
                )
            ),
            encryption=ctx.encryption,
        ),
    )
    worker = WorkerLoop(ctx.admin_db, registry, worker_config=WorkerConfig())
    assert await worker._tick() is True

    # Admin HTTP polling (GET /jobs/{id} + GET /jobs) and result retrieval (GET /jobs/{id}/result).
    async with _admin_http(ctx, agent) as client:
        poll_resp = await client.get(f"/jobs/{job_id}")
        assert poll_resp.status_code == 200
        poll_json = poll_resp.json()
        assert poll_json["status"] == "failed"
        assert poll_json["error"] is None
        assert poll_json["_links"]["result"] is not None
        assert poll_json["_links"]["result"].endswith(f"/jobs/{job_id}/result")

        list_resp = await client.get("/jobs")
        assert list_resp.status_code == 200
        row = next(r for r in list_resp.json()["data"] if r["job_id"] == job_id)
        assert row["_links"]["result"] is not None
        assert row["_links"]["result"].endswith(f"/jobs/{job_id}/result")

        result_resp = await client.get(f"/jobs/{job_id}/result")
        assert result_resp.status_code == 200
        assert result_resp.headers["content-type"].startswith("application/json")
        result_json = result_resp.json()
        assert result_json["execution_id"] == "exec_up_422"
        assert result_json["status"] == "failed"
        assert result_json["http_status"] == 422
        assert base64.b64decode(result_json["body_b64"]) == upstream_error_bytes

    # Service-layer polling & retrieval for authorized callers (agent, owner, admin).
    for caller in (agent, owner, admin):
        job_view = await JobService(ctx).get_by_id(job_id, identity=caller)
        assert job_view.status == JobStatus.FAILED
        assert job_view.error is None
        assert job_view.result_available is True

        listed = await JobService(ctx).list_all(JobFilter(), identity=caller)
        listed_job = next(j for j in listed.data if j.id == job_id)
        assert listed_job.result_available is True

        res_view = await JobResultService(ctx).get(job_id, identity=caller)
        assert res_view.body["status"] == "failed"
        assert res_view.body["http_status"] == 422
        assert base64.b64decode(res_view.body["body_b64"]) == upstream_error_bytes

    # MCP job polling & get_execution_result tool retrieval.
    mcp_res = await handle_get_execution_result(
        _mcp_env(ctx, agent), {"job_id": job_id, "timeout": 0}
    )
    mcp_payload = json.loads(mcp_res.content[0].text)  # type: ignore[union-attr]
    assert mcp_payload["status"] == "failed"
    assert "error" not in mcp_payload
    assert "result_error" not in mcp_payload
    assert mcp_payload["result"]["status"] == "failed"
    assert mcp_payload["result"]["http_status"] == 422
    assert base64.b64decode(mcp_payload["result"]["body_b64"]) == upstream_error_bytes

    # Cross-user denial (404 anti-enumeration for outsider user and sibling agent).
    for unauthorized in (outsider, sibling):
        with pytest.raises(JobNotFoundError):
            await JobService(ctx).get_by_id(job_id, identity=unauthorized)
        with pytest.raises(JobNotFoundError):
            await JobResultService(ctx).get(job_id, identity=unauthorized)
        async with _admin_http(ctx, unauthorized) as client:
            assert (await client.get(f"/jobs/{job_id}")).status_code == 404
            assert (await client.get(f"/jobs/{job_id}/result")).status_code == 404
            visible_ids = {r["job_id"] for r in (await client.get("/jobs")).json()["data"]}
            assert job_id not in visible_ids
        with pytest.raises(ToolError, match="not found"):
            await handle_get_execution_result(
                _mcp_env(ctx, unauthorized), {"job_id": job_id, "timeout": 0}
            )


async def test_approved_execution_runtime_denial_exposes_problem_result(
    integration_context: Context, approved_hold_actors: dict[str, Identity]
) -> None:
    """Pins #2030: approved runs denied at re-authorization expose their stored problem result."""
    ctx = integration_context
    agent = approved_hold_actors["agent"]
    owner = approved_hold_actors["owner"]

    job_id = await _file_and_approve_hold(
        ctx, agent=agent, owner=owner, path="/v1/refunds", execution_id="exec_rt_deny"
    )
    problem = {
        "type": "no_credential_binding",
        "title": "Credential binding revoked",
        "status": 403,
        "detail": "Agent is no longer bound to credential",
        "error_origin": "broker",
    }
    registry = JobHandlerRegistry()
    registry.register(
        JobKind.EXECUTION,
        ExecutionHandler(
            executor=_StubUpstreamExecutor(
                UpstreamExecResult(status_code=200, body=b"{}", content_type=None, duration_ms=1)
            ),
            execution_authorizer=_DenyingAuthorizer(problem),
            encryption=ctx.encryption,
        ),
    )
    worker = WorkerLoop(ctx.admin_db, registry, worker_config=WorkerConfig())
    assert await worker._tick() is True

    async with _admin_http(ctx, agent) as client:
        poll_resp = await client.get(f"/jobs/{job_id}")
        assert poll_resp.status_code == 200
        assert poll_resp.json()["status"] == "failed"
        assert poll_resp.json()["_links"]["result"] is not None
        assert poll_resp.json()["_links"]["result"].endswith(f"/jobs/{job_id}/result")

        res_resp = await client.get(f"/jobs/{job_id}/result")
        assert res_resp.status_code == 200
        assert res_resp.json()["problem"] == problem

    mcp_payload = await job_poll_payload(_mcp_env(ctx, agent), job_id)
    assert mcp_payload["status"] == "failed"
    assert mcp_payload["result"]["problem"] == problem


async def test_failed_job_without_stored_result_preserves_error_and_clean_not_available(
    integration_context: Context, approved_hold_actors: dict[str, Identity]
) -> None:
    """Handler exceptions keep job.error, omit _links.result, 409 on /result, and clean MCP poll."""
    ctx = integration_context
    agent = approved_hold_actors["agent"]
    owner = approved_hold_actors["owner"]

    job_id = await _file_and_approve_hold(
        ctx, agent=agent, owner=owner, path="/v1/crash", execution_id="exec_crash_1"
    )
    registry = JobHandlerRegistry()
    registry.register(
        JobKind.EXECUTION,
        _RaisingHandler(RuntimeError("credential lookup timed out before dispatch")),
    )
    worker = WorkerLoop(ctx.admin_db, registry, worker_config=WorkerConfig())
    assert await worker._tick() is True

    job_view = await JobService(ctx).get_by_id(job_id, identity=agent)
    assert job_view.status == JobStatus.FAILED
    assert job_view.error == "credential lookup timed out before dispatch"
    assert job_view.result_available is False

    with pytest.raises(JobNotCompletedError):
        await JobResultService(ctx).get(job_id, identity=agent)

    async with _admin_http(ctx, agent) as client:
        poll_resp = await client.get(f"/jobs/{job_id}")
        assert poll_resp.status_code == 200
        assert poll_resp.json()["status"] == "failed"
        assert poll_resp.json()["error"] == "credential lookup timed out before dispatch"
        assert poll_resp.json()["_links"]["result"] is None

        res_resp = await client.get(f"/jobs/{job_id}/result")
        assert res_resp.status_code == 409
        assert res_resp.json()["type"].endswith("job_not_completed")

    mcp_payload = await job_poll_payload(_mcp_env(ctx, agent), job_id)
    assert mcp_payload["status"] == "failed"
    assert mcp_payload["error"] == "credential lookup timed out before dispatch"
    assert "result" not in mcp_payload
    assert "result_error" not in mcp_payload


async def test_failed_job_result_expiry_boundary_and_mcp_size_cap(
    integration_context: Context, approved_hold_actors: dict[str, Identity]
) -> None:
    """Expired failed results (<= now) 410 and hide _links.result; oversized live results cap."""
    ctx = integration_context
    agent = approved_hold_actors["agent"]
    owner = approved_hold_actors["owner"]

    # 1. Retention = 0 seconds -> available_until <= now immediately on completion.
    expired_job_id = await _file_and_approve_hold(
        ctx, agent=agent, owner=owner, path="/v1/expire", execution_id="exec_exp_0"
    )
    registry = JobHandlerRegistry()
    registry.register(
        JobKind.EXECUTION,
        ExecutionHandler(
            executor=_StubUpstreamExecutor(
                UpstreamExecResult(
                    status_code=502,
                    body=b'{"error":"bad_gateway"}',
                    content_type="application/json",
                    duration_ms=11,
                )
            ),
            encryption=ctx.encryption,
        ),
    )
    worker = WorkerLoop(
        ctx.admin_db,
        registry,
        worker_config=WorkerConfig(),
        approved_result_retention_seconds=0,
    )
    assert await worker._tick() is True

    expired_view = await JobService(ctx).get_by_id(expired_job_id, identity=agent)
    assert expired_view.status == JobStatus.FAILED
    assert expired_view.result_available is False

    with pytest.raises(JobResultExpiredError):
        await JobResultService(ctx).get(expired_job_id, identity=agent)

    async with _admin_http(ctx, agent) as client:
        poll_resp = await client.get(f"/jobs/{expired_job_id}")
        assert poll_resp.status_code == 200
        assert poll_resp.json()["_links"]["result"] is None

        res_resp = await client.get(f"/jobs/{expired_job_id}/result")
        assert res_resp.status_code == 410
        assert res_resp.json()["type"].endswith("job_result_expired")

    expired_mcp = await job_poll_payload(_mcp_env(ctx, agent), expired_job_id)
    assert expired_mcp["status"] == "failed"
    assert "result" not in expired_mcp
    assert "expired" in expired_mcp["result_error"]

    # 2. Live oversized failed result -> MCP polling caps at MAX_RESULT_BYTES.
    big_job_id = await _file_and_approve_hold(
        ctx, agent=agent, owner=owner, path="/v1/big", execution_id="exec_big_fail"
    )
    big_registry = JobHandlerRegistry()
    big_registry.register(
        JobKind.EXECUTION,
        ExecutionHandler(
            executor=_StubUpstreamExecutor(
                UpstreamExecResult(
                    status_code=500,
                    body=b"X" * (mcp_ex.MAX_RESULT_BYTES + 1024),
                    content_type="text/plain",
                    duration_ms=40,
                )
            ),
            encryption=ctx.encryption,
        ),
    )
    big_worker = WorkerLoop(ctx.admin_db, big_registry, worker_config=WorkerConfig())
    assert await big_worker._tick() is True

    big_mcp = await job_poll_payload(_mcp_env(ctx, agent), big_job_id)
    assert big_mcp["status"] == "failed"
    assert big_mcp["truncated"] is True
    assert big_mcp["total_bytes"] > mcp_ex.MAX_RESULT_BYTES
    assert len(big_mcp["result"].encode("utf-8")) <= mcp_ex.MAX_RESULT_BYTES
