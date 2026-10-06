"""Integration tests for the broker's require-approval hold path (``_handle_hold``).

Drives the route's hold branch with a real Context and admin DB: the 202 held
envelope, the encrypted held payload, the identical-retry join, the pending
cap denial, and the ``execution.approval_requested`` event.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import delete, select
from starlette.requests import Request

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_approvals import ExecutionApproval
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.broker.core.exceptions import ApprovalPendingLimitError
from jentic_one.broker.core.problem import broker_error_problem, status_for_broker_error
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.broker.services.execution.authorization import ExecutionAuthorization
from jentic_one.broker.web.routers.execute import _handle_hold
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import RuleVerdict
from jentic_one.shared.context import Context
from jentic_one.shared.jobs.hold import ENCRYPTED_PAYLOAD_KEY, HELD_AGENT_DIRECTIVE
from jentic_one.shared.models import (
    ActorType,
    CredentialType,
    JobStatus,
    StoredCredentialType,
)
from jentic_one.shared.models.events import EventType

pytestmark = pytest.mark.integration

_AGENT = Identity(sub="agnt_holdpath", actor_type=ActorType.AGENT, permissions=[])
_URL = "https://api.example.com/v1/charges"
_BODY = b'{"amount": 500}'


@pytest.fixture()
async def clean(integration_context: Context) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(delete(Job))
            await session.execute(delete(Event).where(Event.type.like("execution.approval_%")))

    await _truncate()
    yield
    await _truncate()


def _request(body: bytes) -> Request:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

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


def _ctx_req() -> ExecuteRequestContext:
    return ExecuteRequestContext(
        upstream_url=_URL,
        method="POST",
        trace_id="b" * 32,
        api_vendor="api.example.com",
        api_name="payments",
        api_version="1",
    )


def _authorization() -> ExecutionAuthorization:
    credential = ResolvedCredential(
        credential_id="cred_holdpath",
        name="payments-key",
        wire_type=CredentialType.API_KEY,
        stored_type=StoredCredentialType.API_KEY,
        provider="api.example.com",
    )
    return ExecutionAuthorization(
        allowed_credential_ids=["cred_holdpath"],
        selected_credential=credential,
        verdict=RuleVerdict.REQUIRE_APPROVAL,
        matched_rule_id="apr_holdpath",
    )


async def _hold(ctx: Context, body: bytes = _BODY) -> dict[str, Any]:
    response = await _handle_hold(_request(body), _ctx_req(), ctx, _AGENT, _authorization())
    assert response.status_code == 202
    assert response.headers["Preference-Applied"] == "respond-async"
    return dict(json.loads(bytes(response.body)))


async def test_hold_answers_the_held_envelope_and_stores_an_encrypted_payload(
    integration_context: Context, clean: None
) -> None:
    ctx = integration_context
    envelope = await _hold(ctx)

    assert set(envelope) == {"job_id", "status", "approval", "agent_directive", "_links"}
    assert envelope["status"] == "held"
    assert envelope["agent_directive"] == HELD_AGENT_DIRECTIVE
    assert envelope["_links"]["self"].endswith(f"/jobs/{envelope['job_id']}")
    approval = envelope["approval"]
    assert set(approval) == {"id", "review_url", "expires_at"}
    assert approval["review_url"].endswith(f"/app/approvals/{approval['id']}")

    async with ctx.admin_db.session() as session:
        job = await session.get(Job, envelope["job_id"])
        row = await session.get(ExecutionApproval, approval["id"])
        events = (
            (
                await session.execute(
                    select(Event).where(Event.type == EventType.EXECUTION_APPROVAL_REQUESTED)
                )
            )
            .scalars()
            .all()
        )
    assert job is not None and row is not None
    assert job.status == JobStatus.HELD
    payload = job.payload or {}
    assert set(payload) == {ENCRYPTED_PAYLOAD_KEY}
    assert b"amount" not in json.dumps(payload).encode()
    decrypted = json.loads(ctx.encryption.decrypt(payload[ENCRYPTED_PAYLOAD_KEY]))
    assert decrypted["method"] == "POST"
    assert decrypted["credential_id"] == "cred_holdpath"
    assert row.matched_rule_id == "apr_holdpath"
    assert row.path == "/v1/charges"

    (event,) = events
    assert event.requires_action is True
    assert event.data["review_url"] == approval["review_url"]
    assert event.data["method"] == "POST"
    assert event.data["path"] == "/v1/charges"
    assert "body" not in json.dumps(event.data)
    assert "amount" not in json.dumps(event.data)


async def test_identical_retry_joins_without_a_second_event(
    integration_context: Context, clean: None
) -> None:
    ctx = integration_context
    first = await _hold(ctx)
    again = await _hold(ctx)
    assert again["job_id"] == first["job_id"]
    assert again["approval"]["id"] == first["approval"]["id"]
    async with ctx.admin_db.session() as session:
        count = len(
            (
                await session.execute(
                    select(Event).where(Event.type == EventType.EXECUTION_APPROVAL_REQUESTED)
                )
            )
            .scalars()
            .all()
        )
    assert count == 1


async def test_pending_cap_is_a_distinct_403_denial(
    integration_context: Context, clean: None
) -> None:
    ctx = integration_context
    ctx.config.execution_approvals.max_pending_per_agent = 1
    try:
        await _hold(ctx, b'{"n": 1}')
        with pytest.raises(ApprovalPendingLimitError) as exc:
            await _hold(ctx, b'{"n": 2}')
    finally:
        ctx.config.execution_approvals.max_pending_per_agent = 10
    problem = broker_error_problem(exc.value)
    assert status_for_broker_error(exc.value) == 403
    assert problem["type"] == "approval_pending_limit_reached"
    assert problem["agent_directive"]["strategy"] == "prompt_human"
    async with ctx.admin_db.session() as session:
        jobs = (await session.execute(select(Job))).scalars().all()
    assert len(jobs) == 1
