"""End-to-end: a require-approval call from hold to result, across surfaces.

Chains the steps the per-surface tests pin one by one, against a real Context
and admin DB:

1. the broker's hold path answers the 202 held envelope;
2. the agent's owner decides through the admin HTTP API;
3. the worker claims the released job and runs it exactly once;
4. the agent reads the outcome back — over ``GET /jobs/{id}`` and through the
   MCP ``get_execution_result`` tool (with a bounded wait).

The upstream leg is a recording execution handler: the broker's real
upstream call is covered by its own suites.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import mcp.types as mcp_types
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from starlette.requests import Request

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_approvals import ExecutionApproval
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentRepository, UserRepository
from jentic_one.admin.web.app import create_app
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.broker.services.execution.authorization import ExecutionAuthorization
from jentic_one.broker.web.routers.execute import _handle_hold
from jentic_one.mcp.tools import CallEnv, dispatch_mcp_tool_call
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import RuleVerdict
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.context import Context
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models import (
    ActorStatus,
    ActorType,
    CredentialType,
    InviteState,
    JobKind,
    JobStatus,
    StoredCredentialType,
)
from jentic_one.shared.web.deps import resolve_identity

pytestmark = pytest.mark.integration

_URL = "https://api.example.com/v1/charges"
_BODY = b'{"amount": 500}'


@dataclass(frozen=True)
class _Parties:
    owner: Identity
    agent: Identity


class _RecordingHandler:
    """Stands in for the upstream leg: counts runs and answers a fixed 200."""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        self.calls += 1
        return JobResultPayload(
            body={"execution_id": "exec_e2e", "status": "completed", "http_status": 200}
        )


@pytest.fixture()
async def parties(integration_context: Context) -> AsyncGenerator[_Parties, None]:
    """An owner and their agent, with the approval tables clean around the test."""
    ctx = integration_context

    async def _clean() -> None:
        async with ctx.admin_db.transaction() as session:
            await session.execute(delete(ExecutionApproval))
            await session.execute(delete(JobResult))
            await session.execute(delete(Job))
            await session.execute(delete(Event).where(Event.type.like("execution.approval_%")))

    await _clean()
    async with ctx.admin_db.transaction() as session:
        owner = await UserRepository.create(
            session,
            email="e2e-owner@test.local",
            first_name="E2E",
            last_name="Owner",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        agent = await AgentRepository.create(
            session,
            name="e2e-agent",
            owner_id=owner.id,
            registered_by=owner.id,
            created_by=owner.id,
            status=ActorStatus.ACTIVE,
        )
        owner_id, agent_id = owner.id, agent.id
    yield _Parties(
        owner=Identity(sub=owner_id, email="e2e-owner@test.local", permissions=["jobs:write"]),
        agent=Identity(
            sub=agent_id,
            permissions=["jobs:read"],
            actor_type=ActorType.AGENT,
            parent_actor_id=owner_id,
        ),
    )
    await _clean()
    async with ctx.admin_db.transaction() as session:
        await session.execute(delete(Agent).where(Agent.id == agent_id))
        await session.execute(delete(User).where(User.id == owner_id))


def _request() -> Request:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": _BODY, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "server": ("broker.local", 8080),
        "path": "/api.example.com/v1/charges",
        "raw_path": b"/api.example.com/v1/charges",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"host", b"broker.local:8080")],
    }
    return Request(scope, receive)


async def _broker_holds(ctx: Context, agent: Identity) -> dict[str, Any]:
    ctx_req = ExecuteRequestContext(
        upstream_url=_URL,
        method="POST",
        trace_id="e" * 32,
        api_vendor="api.example.com",
        api_name="payments",
        api_version="1",
    )
    authorization = ExecutionAuthorization(
        allowed_credential_ids=["cred_e2e"],
        selected_credential=ResolvedCredential(
            credential_id="cred_e2e",
            name="payments-key",
            wire_type=CredentialType.API_KEY,
            stored_type=StoredCredentialType.API_KEY,
            provider="api.example.com",
        ),
        verdict=RuleVerdict.REQUIRE_APPROVAL,
        matched_rule_id="apr_e2e",
    )
    response = await _handle_hold(_request(), ctx_req, ctx, agent, authorization)
    assert response.status_code == 202
    return dict(json.loads(bytes(response.body)))


@asynccontextmanager
async def _admin_client(ctx: Context, identity: Identity) -> AsyncIterator[AsyncClient]:
    app = create_app(ctx)
    app.dependency_overrides[resolve_identity] = lambda: identity
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as c:
        yield c


def _worker(ctx: Context, handler: _RecordingHandler) -> WorkerLoop:
    registry = JobHandlerRegistry()
    registry.register(JobKind.EXECUTION, handler)
    return WorkerLoop(
        ctx.admin_db,
        registry,
        worker_config=WorkerConfig(max_attempts=5),
        approved_result_retention_seconds=3600,
    )


async def _mcp_result(ctx: Context, agent: Identity, job_id: str, **extra: Any) -> dict[str, Any]:
    env = CallEnv(
        ctx=ctx, identity=agent, credential="at_e2e", base_url="https://testserver", session_id=None
    )
    result = await dispatch_mcp_tool_call(env, "get_execution_result", {"job_id": job_id, **extra})
    assert isinstance(result, mcp_types.CallToolResult)
    (content,) = result.content
    assert isinstance(content, mcp_types.TextContent)
    return dict(json.loads(content.text))


async def test_approved_call_runs_once_and_its_result_reaches_the_agent(
    integration_context: Context, parties: _Parties
) -> None:
    ctx = integration_context
    envelope = await _broker_holds(ctx, parties.agent)
    job_id, approval_id = envelope["job_id"], envelope["approval"]["id"]
    assert envelope["status"] == "held"

    # Before a decision: the worker leaves the held job alone, and the agent's
    # poll answers "held".
    handler = _RecordingHandler()
    assert await _worker(ctx, handler)._tick() is False
    assert (await _mcp_result(ctx, parties.agent, job_id))["status"] == JobStatus.HELD

    async with _admin_client(ctx, parties.owner) as client:
        decided = await client.post(
            f"/executions/approvals/{approval_id}:decide", json={"decision": "approve"}
        )
    assert decided.status_code == 200, decided.text
    assert decided.json()["state"] == "approved"

    worker = _worker(ctx, handler)
    assert await worker._tick() is True
    assert await worker._tick() is False
    assert handler.calls == 1

    async with _admin_client(ctx, parties.agent) as client:
        job = await client.get(f"/jobs/{job_id}")
        result = await client.get(f"/jobs/{job_id}/result")
    assert job.status_code == 200, job.text
    assert job.json()["status"] == JobStatus.COMPLETED
    assert result.status_code == 200, result.text

    polled = await _mcp_result(ctx, parties.agent, job_id, wait_seconds=30)
    assert polled["status"] == JobStatus.COMPLETED
    assert "result" in polled


async def test_denied_call_never_runs_and_the_agent_reads_the_denial(
    integration_context: Context, parties: _Parties
) -> None:
    ctx = integration_context
    envelope = await _broker_holds(ctx, parties.agent)
    job_id, approval_id = envelope["job_id"], envelope["approval"]["id"]

    async with _admin_client(ctx, parties.owner) as client:
        decided = await client.post(
            f"/executions/approvals/{approval_id}:decide",
            json={"decision": "deny", "reason": "not this one"},
        )
    assert decided.status_code == 200, decided.text

    handler = _RecordingHandler()
    assert await _worker(ctx, handler)._tick() is False
    assert handler.calls == 0

    polled = await _mcp_result(ctx, parties.agent, job_id)
    assert polled["status"] == JobStatus.FAILED
    assert polled["result"]["status"] == 403
