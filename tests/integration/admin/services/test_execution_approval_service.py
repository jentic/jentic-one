"""Integration tests for require-approval holds: filing, deciding, withdrawing, expiring.

Runs the shared hold path, ``ExecutionApprovalService``, ``JobService`` /
``JobResultService`` and the worker's expiry sweep against the real admin DB,
and drives the admin routes through the real admin app (only the identity
dependency is overridden).
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_approvals import ExecutionApproval
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentRepository, UserRepository
from jentic_one.admin.services.errors import (
    ExecutionApprovalAlreadyDecidedError,
    ExecutionApprovalForbiddenError,
    ExecutionApprovalJobNotHeldError,
    ExecutionApprovalNotFoundError,
    JobAwaitingApprovalError,
)
from jentic_one.admin.services.execution_approval_service import ExecutionApprovalService
from jentic_one.admin.services.job_result_service import JobResultService
from jentic_one.admin.services.job_service import JobService
from jentic_one.admin.services.schemas.execution_approvals import DecideInput
from jentic_one.admin.web.app import create_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.context import Context
from jentic_one.shared.jobs.handlers import JobHandlerRegistry
from jentic_one.shared.jobs.hold import (
    ENCRYPTED_PAYLOAD_KEY,
    HoldOutcome,
    PendingApprovalLimitError,
    file_hold,
)
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models import ActorStatus, ActorType, InviteState, JobStatus
from jentic_one.shared.models.events import EventType
from jentic_one.shared.models.execution_approvals import ApprovalDecision
from jentic_one.shared.web.deps import resolve_identity

pytestmark = pytest.mark.integration

_BODY = b'{"amount": 500, "currency": "eur"}'


@dataclass(frozen=True)
class _Actors:
    owner: Identity
    outsider: Identity
    admin: Identity
    agent: Identity
    ownerless_agent: Identity


@pytest.fixture()
async def actors(integration_context: Context) -> AsyncGenerator[_Actors, None]:
    """An owner with one agent, an ownerless agent, an outsider user and an admin."""
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
            email="exap-owner@test.local",
            first_name="Ex",
            last_name="Owner",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        agent = await AgentRepository.create(
            session,
            name="exap-agent",
            owner_id=owner.id,
            registered_by=owner.id,
            created_by=owner.id,
            status=ActorStatus.ACTIVE,
        )
        orphan = await AgentRepository.create(
            session,
            name="exap-orphan",
            owner_id=owner.id,
            registered_by=owner.id,
            created_by=owner.id,
            status=ActorStatus.ACTIVE,
        )
        owner_id, agent_id, orphan_id = owner.id, agent.id, orphan.id
        # An ownerless agent: its approvals are reviewable by org:admin only.
        await session.execute(update(Agent).where(Agent.id == orphan_id).values(owner_id=None))
    yield _Actors(
        owner=Identity(sub=owner_id, email="exap-owner@test.local", permissions=["jobs:write"]),
        outsider=Identity(sub="usr_exap_outsider", permissions=["jobs:read", "jobs:write"]),
        admin=Identity(sub="usr_exap_admin", permissions=["org:admin"]),
        agent=Identity(
            sub=agent_id,
            permissions=["jobs:read", "jobs:write"],
            actor_type=ActorType.AGENT,
            parent_actor_id=owner_id,
        ),
        ownerless_agent=Identity(
            sub=orphan_id, permissions=["jobs:read"], actor_type=ActorType.AGENT
        ),
    )
    await _clean()
    async with ctx.admin_db.transaction() as session:
        await session.execute(delete(Agent).where(Agent.id.in_([agent_id, orphan_id])))
        await session.execute(delete(User).where(User.id == owner_id))


async def _hold(
    ctx: Context,
    agent: Identity,
    *,
    body: bytes = _BODY,
    path: str = "/v1/charges",
    ttl_seconds: int = 3600,
    max_pending: int = 10,
) -> HoldOutcome:
    payload = {
        "method": "POST",
        "upstream_url": f"https://api.example.com{path}?expand=all",
        "body_b64": base64.b64encode(body).decode(),
    }
    async with ctx.admin_db.transaction() as session:
        return await file_hold(
            session,
            agent_id=agent.sub,
            actor_type=agent.actor_type.value,
            credential_id="cred_exap",
            matched_rule_id="apr_exap",
            api_vendor="api.example.com",
            api_name="payments",
            api_version="1",
            operation_id="createCharge",
            method="POST",
            path=path,
            body=body,
            trace_id=None,
            execution_id=f"exec_{path.strip('/').replace('/', '_')}_{len(body)}",
            payload={ENCRYPTED_PAYLOAD_KEY: ctx.encryption.encrypt(json.dumps(payload))},
            ttl_seconds=ttl_seconds,
            max_pending=max_pending,
        )


async def _job(ctx: Context, job_id: str) -> Job:
    async with ctx.admin_db.session() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        return job


async def _approval(ctx: Context, approval_id: str) -> ExecutionApproval:
    async with ctx.admin_db.session() as session:
        row = await session.get(ExecutionApproval, approval_id)
        assert row is not None
        return row


@asynccontextmanager
async def _client(ctx: Context, identity: Identity | None) -> AsyncIterator[AsyncClient]:
    """The real admin app; ``identity`` overrides token resolution, None sends no token."""
    app = create_app(ctx)
    if identity is not None:
        app.dependency_overrides[resolve_identity] = lambda: identity
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as c:
        yield c


async def _result_body(ctx: Context, job_id: str, identity: Identity) -> dict[str, Any]:
    view = await JobResultService(ctx).get(job_id, identity=identity)
    return view.body


async def test_file_hold_writes_a_held_job_and_pending_approval(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    assert hold.joined is False
    job = await _job(ctx, hold.job_id)
    assert job.status == JobStatus.HELD
    assert job.created_by == actors.agent.sub
    assert set(job.payload or {}) == {ENCRYPTED_PAYLOAD_KEY}
    approval = await _approval(ctx, hold.approval_id)
    assert approval.state == "pending"
    assert approval.matched_rule_id == "apr_exap"
    assert approval.execution_id is None
    assert approval.expires_at > datetime.now(UTC) + timedelta(minutes=59)


async def test_identical_request_joins_the_pending_hold(
    integration_context: Context, actors: _Actors
) -> None:
    """An identical retry returns the same job; a different body files a new hold."""
    ctx = integration_context
    first = await _hold(ctx, actors.agent)
    again = await _hold(ctx, actors.agent, body=b'{"currency":"eur","amount":500}')
    other = await _hold(ctx, actors.agent, body=b'{"amount": 501}')
    assert again.joined is True
    assert (again.job_id, again.approval_id) == (first.job_id, first.approval_id)
    assert other.joined is False
    assert other.job_id != first.job_id


async def test_concurrent_identical_holds_never_duplicate(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    results = await asyncio.gather(*(_hold(ctx, actors.agent) for _ in range(4)))
    assert len({r.job_id for r in results}) == 1
    async with ctx.admin_db.session() as session:
        count = len((await session.execute(select(ExecutionApproval))).scalars().all())
    assert count == 1


async def test_pending_cap_denies_further_holds_and_counts_only_pending(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    first = await _hold(ctx, actors.agent, path="/v1/a", max_pending=2)
    await _hold(ctx, actors.agent, path="/v1/b", max_pending=2)
    with pytest.raises(PendingApprovalLimitError) as exc:
        await _hold(ctx, actors.agent, path="/v1/c", max_pending=2)
    assert (exc.value.pending, exc.value.limit) == (2, 2)

    await ExecutionApprovalService(ctx).decide(
        first.approval_id, DecideInput(decision=ApprovalDecision.DENY), identity=actors.owner
    )
    third = await _hold(ctx, actors.agent, path="/v1/c", max_pending=2)
    assert third.joined is False


async def test_concurrent_filings_never_exceed_the_pending_cap(
    integration_context: Context, actors: _Actors
) -> None:
    """Five different calls race for two pending slots: exactly two are held."""
    ctx = integration_context
    outcomes = await asyncio.gather(
        *(_hold(ctx, actors.agent, path=f"/v1/race/{i}", max_pending=2) for i in range(5)),
        return_exceptions=True,
    )
    held = [o for o in outcomes if isinstance(o, HoldOutcome)]
    refused = [o for o in outcomes if isinstance(o, PendingApprovalLimitError)]
    assert len(held) == 2, outcomes
    assert len(refused) == 3, outcomes
    async with ctx.admin_db.session() as session:
        pending = (
            (
                await session.execute(
                    select(ExecutionApproval).where(
                        ExecutionApproval.agent_id == actors.agent.sub,
                        ExecutionApproval.state == "pending",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(pending) == 2


@pytest.mark.parametrize("decision", [ApprovalDecision.APPROVE, ApprovalDecision.DENY])
async def test_a_decision_whose_job_is_no_longer_held_is_rolled_back(
    integration_context: Context, actors: _Actors, decision: ApprovalDecision
) -> None:
    """The approval never settles without its job: it stays pending, nothing is
    written, and the route answers 409."""
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            update(Job).where(Job.id == hold.job_id).values(status=JobStatus.CANCELLED)
        )

    with pytest.raises(ExecutionApprovalJobNotHeldError):
        await ExecutionApprovalService(ctx).decide(
            hold.approval_id, DecideInput(decision=decision), identity=actors.owner
        )
    approval = await _approval(ctx, hold.approval_id)
    assert approval.state == "pending"
    assert approval.decided_by is None
    assert (await _job(ctx, hold.job_id)).status == JobStatus.CANCELLED
    assert await _job_result_count(ctx, hold.job_id) == 0
    async with ctx.admin_db.session() as session:
        audits = (
            await session.execute(
                select(AuditEntry).where(AuditEntry.target_id == hold.approval_id)
            )
        ).all()
    assert audits == []

    async with _client(ctx, actors.owner) as client:
        resp = await client.post(
            f"/executions/approvals/{hold.approval_id}:decide", json={"decision": decision.value}
        )
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("execution_approval_job_not_held")


async def test_a_withdrawal_whose_job_is_no_longer_held_is_rolled_back(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            update(Job).where(Job.id == hold.job_id).values(status=JobStatus.FAILED)
        )
    with pytest.raises(ExecutionApprovalJobNotHeldError):
        await ExecutionApprovalService(ctx).withdraw(hold.approval_id, identity=actors.agent)
    assert (await _approval(ctx, hold.approval_id)).state == "pending"


async def test_approve_releases_the_job_once_with_audit_and_event(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    view = await ExecutionApprovalService(ctx).decide(
        hold.approval_id,
        DecideInput(decision=ApprovalDecision.APPROVE, reason="ok"),
        identity=actors.owner,
    )
    assert view.state == "approved"
    assert view.decided_by == actors.owner.sub
    assert (await _job(ctx, hold.job_id)).status == JobStatus.QUEUED
    async with ctx.admin_db.session() as session:
        audit = (
            await session.execute(
                select(AuditEntry).where(AuditEntry.target_id == hold.approval_id)
            )
        ).scalar_one()
        events = (
            (await session.execute(select(Event).where(Event.job_id == hold.job_id)))
            .scalars()
            .all()
        )
    assert audit.action == "approve"
    assert audit.actor_id == actors.owner.sub
    assert {e.type for e in events} >= {EventType.EXECUTION_APPROVAL_DECIDED}


async def test_deny_fails_the_job_with_a_readable_permission_denied_result(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    await ExecutionApprovalService(ctx).decide(
        hold.approval_id,
        DecideInput(decision=ApprovalDecision.DENY, reason="wrong account"),
        identity=actors.admin,
    )
    job = await _job(ctx, hold.job_id)
    assert job.status == JobStatus.FAILED
    body = await _result_body(ctx, hold.job_id, actors.agent)
    assert body["type"] == "approval_denied"
    assert body["status"] == 403
    assert body["approval"] == {"id": hold.approval_id, "state": "denied"}
    assert "wrong account" in body["detail"]


async def test_first_decision_wins_and_later_decides_conflict(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    svc = ExecutionApprovalService(ctx)
    outcomes = await asyncio.gather(
        svc.decide(
            hold.approval_id, DecideInput(decision=ApprovalDecision.APPROVE), identity=actors.owner
        ),
        svc.decide(
            hold.approval_id, DecideInput(decision=ApprovalDecision.DENY), identity=actors.admin
        ),
        return_exceptions=True,
    )
    wins = [o for o in outcomes if not isinstance(o, BaseException)]
    losses = [o for o in outcomes if isinstance(o, BaseException)]
    assert len(wins) == 1
    assert len(losses) == 1
    assert isinstance(losses[0], ExecutionApprovalAlreadyDecidedError)
    assert f"already {wins[0].state}" in str(losses[0])
    expected_job = JobStatus.QUEUED if wins[0].state == "approved" else JobStatus.FAILED
    assert (await _job(ctx, hold.job_id)).status == expected_job

    with pytest.raises(ExecutionApprovalAlreadyDecidedError):
        await svc.decide(
            hold.approval_id, DecideInput(decision=ApprovalDecision.APPROVE), identity=actors.admin
        )


async def test_an_expired_but_unswept_approval_cannot_be_approved(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            update(ExecutionApproval)
            .where(ExecutionApproval.id == hold.approval_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    with pytest.raises(ExecutionApprovalAlreadyDecidedError, match="expired"):
        await ExecutionApprovalService(ctx).decide(
            hold.approval_id, DecideInput(decision=ApprovalDecision.APPROVE), identity=actors.owner
        )
    assert (await _job(ctx, hold.job_id)).status == JobStatus.HELD


async def test_only_the_owner_or_an_admin_may_review(
    integration_context: Context, actors: _Actors
) -> None:
    """Outsiders cannot see or decide; agents are refused; ownerless agents are admin-only."""
    ctx = integration_context
    svc = ExecutionApprovalService(ctx)
    hold = await _hold(ctx, actors.agent, path="/v1/owned")
    orphan_hold = await _hold(ctx, actors.ownerless_agent, path="/v1/orphan")

    with pytest.raises(ExecutionApprovalNotFoundError):
        await svc.get(hold.approval_id, identity=actors.outsider)
    with pytest.raises(ExecutionApprovalNotFoundError):
        await svc.decide(
            hold.approval_id,
            DecideInput(decision=ApprovalDecision.APPROVE),
            identity=actors.outsider,
        )
    agent_with_admin = Identity(
        sub=actors.agent.sub, permissions=["org:admin"], actor_type=ActorType.AGENT
    )
    for agent in (actors.agent, agent_with_admin):
        with pytest.raises(ExecutionApprovalForbiddenError):
            await svc.decide(
                hold.approval_id, DecideInput(decision=ApprovalDecision.APPROVE), identity=agent
            )
    with pytest.raises(ExecutionApprovalNotFoundError):
        await svc.decide(
            orphan_hold.approval_id,
            DecideInput(decision=ApprovalDecision.APPROVE),
            identity=actors.owner,
        )

    owner_list = await svc.list_all(identity=actors.owner)
    assert {v.id for v in owner_list.data} == {hold.approval_id}
    assert (await svc.list_all(identity=actors.outsider)).data == []
    admin_list = await svc.list_all(identity=actors.admin)
    assert {v.id for v in admin_list.data} == {hold.approval_id, orphan_hold.approval_id}

    await svc.decide(
        orphan_hold.approval_id,
        DecideInput(decision=ApprovalDecision.APPROVE),
        identity=actors.admin,
    )
    await svc.decide(
        hold.approval_id, DecideInput(decision=ApprovalDecision.APPROVE), identity=actors.owner
    )
    pending = await svc.list_all(identity=actors.admin, state=None)
    assert {v.state for v in pending.data} == {"approved"}


async def test_detail_shows_the_agent_owner_and_the_held_request(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    detail = await ExecutionApprovalService(ctx).get(hold.approval_id, identity=actors.owner)
    assert detail.agent_name == "exap-agent"
    assert detail.agent_owner_id == actors.owner.sub
    assert detail.matched_rule_id == "apr_exap"
    assert detail.request is not None
    assert detail.request.method == "POST"
    assert detail.request.url == "https://api.example.com/v1/charges?expand=all"
    assert detail.request.body == _BODY.decode()


async def test_jobs_cancel_refuses_a_held_job_and_leaves_its_approval_pending(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    for caller in (actors.agent, actors.admin):
        with pytest.raises(JobAwaitingApprovalError):
            await JobService(ctx).cancel(hold.job_id, identity=caller)
    assert (await _job(ctx, hold.job_id)).status == JobStatus.HELD
    assert (await _approval(ctx, hold.approval_id)).state == "pending"


async def test_jobs_cancel_cancels_a_held_job_of_another_kind(
    integration_context: Context, actors: _Actors
) -> None:
    """Only held executions are refused; a held job of any other kind cancels normally."""
    ctx = integration_context
    async with ctx.admin_db.transaction() as session:
        job = Job(
            kind="import",
            status=JobStatus.HELD,
            payload={},
            created_by=actors.agent.sub,
            actor_type=actors.agent.actor_type.value,
        )
        session.add(job)
        await session.flush()
        job_id = job.id
    view = await JobService(ctx).cancel(job_id, identity=actors.admin)
    assert view.status == JobStatus.CANCELLED
    assert (await _job(ctx, job_id)).status == JobStatus.CANCELLED


async def _job_result_count(ctx: Context, job_id: str) -> int:
    async with ctx.admin_db.session() as session:
        rows = await session.execute(select(JobResult).where(JobResult.job_id == job_id))
        return len(rows.scalars().all())


async def test_withdraw_cancels_the_held_job_without_a_result_with_audit_and_event(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    view = await ExecutionApprovalService(ctx).withdraw(hold.approval_id, identity=actors.agent)
    assert view.state == "withdrawn"
    assert view.decided_by == actors.agent.sub
    assert view.decided_at is not None
    assert (await _approval(ctx, hold.approval_id)).state == "withdrawn"
    assert (await _job(ctx, hold.job_id)).status == JobStatus.CANCELLED
    assert await _job_result_count(ctx, hold.job_id) == 0
    async with ctx.admin_db.session() as session:
        audit = (
            await session.execute(
                select(AuditEntry).where(AuditEntry.target_id == hold.approval_id)
            )
        ).scalar_one()
        events = (
            (await session.execute(select(Event).where(Event.job_id == hold.job_id)))
            .scalars()
            .all()
        )
    assert audit.action == "withdraw"
    assert audit.actor_id == actors.agent.sub
    (event,) = [e for e in events if e.type == EventType.EXECUTION_APPROVAL_WITHDRAWN]
    assert event.data["approval_id"] == hold.approval_id
    assert "body" not in json.dumps(event.data)
    with pytest.raises(ExecutionApprovalAlreadyDecidedError):
        await ExecutionApprovalService(ctx).decide(
            hold.approval_id, DecideInput(decision=ApprovalDecision.APPROVE), identity=actors.owner
        )


async def test_only_the_filing_identity_may_withdraw(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    svc = ExecutionApprovalService(ctx)
    for caller in (actors.owner, actors.admin, actors.outsider, actors.ownerless_agent):
        with pytest.raises(ExecutionApprovalNotFoundError):
            await svc.withdraw(hold.approval_id, identity=caller)
    with pytest.raises(ExecutionApprovalNotFoundError):
        await svc.withdraw("exap_missing", identity=actors.agent)
    assert (await _approval(ctx, hold.approval_id)).state == "pending"
    assert (await _job(ctx, hold.job_id)).status == JobStatus.HELD


async def test_withdraw_conflicts_once_the_approval_is_settled(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    svc = ExecutionApprovalService(ctx)
    denied = await _hold(ctx, actors.agent, path="/v1/denied")
    await svc.decide(
        denied.approval_id, DecideInput(decision=ApprovalDecision.DENY), identity=actors.owner
    )
    approved = await _hold(ctx, actors.agent, path="/v1/approved")
    await svc.decide(
        approved.approval_id, DecideInput(decision=ApprovalDecision.APPROVE), identity=actors.owner
    )
    withdrawn = await _hold(ctx, actors.agent, path="/v1/withdrawn")
    await svc.withdraw(withdrawn.approval_id, identity=actors.agent)

    for hold, state in ((denied, "denied"), (approved, "approved"), (withdrawn, "withdrawn")):
        with pytest.raises(ExecutionApprovalAlreadyDecidedError, match=f"already {state}"):
            await svc.withdraw(hold.approval_id, identity=actors.agent)
    assert (await _job(ctx, denied.job_id)).status == JobStatus.FAILED
    assert (await _job(ctx, approved.job_id)).status == JobStatus.QUEUED


async def test_concurrent_decide_and_withdraw_settle_exactly_once(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    for decision in (ApprovalDecision.APPROVE, ApprovalDecision.DENY):
        hold = await _hold(ctx, actors.agent, path=f"/v1/race/{decision.value}")
        svc = ExecutionApprovalService(ctx)
        outcomes = await asyncio.gather(
            svc.decide(hold.approval_id, DecideInput(decision=decision), identity=actors.owner),
            svc.withdraw(hold.approval_id, identity=actors.agent),
            return_exceptions=True,
        )
        wins = [o for o in outcomes if not isinstance(o, BaseException)]
        losses = [o for o in outcomes if isinstance(o, BaseException)]
        assert len(wins) == 1
        assert len(losses) == 1
        assert isinstance(losses[0], ExecutionApprovalAlreadyDecidedError)
        state = wins[0].state
        assert f"already {state}" in str(losses[0])
        assert (await _approval(ctx, hold.approval_id)).state == state
        expected_job = {
            "approved": JobStatus.QUEUED,
            "denied": JobStatus.FAILED,
            "withdrawn": JobStatus.CANCELLED,
        }[state]
        assert (await _job(ctx, hold.job_id)).status == expected_job
        assert await _job_result_count(ctx, hold.job_id) == (1 if state == "denied" else 0)


async def test_expiry_sweep_expires_and_fails_with_a_permission_denied_result(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    lapsed = await _hold(ctx, actors.agent, path="/v1/lapsed")
    live = await _hold(ctx, actors.agent, path="/v1/live")
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            update(ExecutionApproval)
            .where(ExecutionApproval.id == lapsed.approval_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    worker = WorkerLoop(ctx.admin_db, JobHandlerRegistry(), worker_config=WorkerConfig())
    await worker._sweep_expired()

    assert (await _approval(ctx, lapsed.approval_id)).state == "expired"
    assert (await _job(ctx, lapsed.job_id)).status == JobStatus.FAILED
    body = await _result_body(ctx, lapsed.job_id, actors.owner)
    assert body["type"] == "approval_expired"
    assert body["status"] == 403
    assert (await _approval(ctx, live.approval_id)).state == "pending"
    assert (await _job(ctx, live.job_id)).status == JobStatus.HELD

    # The expiry is audited (attributed to the filing agent, system origin)
    # and announced, so the requested prompt can settle.
    async with ctx.admin_db.session() as session:
        audits = (
            (
                await session.execute(
                    select(AuditEntry).where(AuditEntry.target_id == lapsed.approval_id)
                )
            )
            .scalars()
            .all()
        )
        events = (
            (
                await session.execute(
                    select(Event).where(Event.type == EventType.EXECUTION_APPROVAL_EXPIRED)
                )
            )
            .scalars()
            .all()
        )
    (audit,) = audits
    assert audit.action == "expire"
    assert audit.actor_id == actors.agent.sub
    assert audit.origin == "system"
    assert audit.job_id == lapsed.job_id
    (event,) = events
    assert event.job_id == lapsed.job_id
    assert event.data["approval_id"] == lapsed.approval_id
    assert event.data["path"] == "/v1/lapsed"


async def test_jobs_cancel_route_answers_409_for_a_held_job(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    async with _client(ctx, actors.agent) as client:
        resp = await client.post(f"/jobs/{hold.job_id}:cancel")
    assert resp.status_code == 409
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["type"].endswith("job_awaiting_approval")
    assert (await _job(ctx, hold.job_id)).status == JobStatus.HELD


async def test_withdraw_route_withdraws_for_the_filing_agent(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    async with _client(ctx, actors.agent) as client:
        resp = await client.post(f"/executions/approvals/{hold.approval_id}:withdraw")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == hold.approval_id
    assert body["state"] == "withdrawn"
    assert body["_links"]["job"].endswith(f"/jobs/{hold.job_id}")
    assert (await _job(ctx, hold.job_id)).status == JobStatus.CANCELLED
    assert await _job_result_count(ctx, hold.job_id) == 0


async def test_withdraw_route_rejects_an_unauthenticated_caller(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    async with _client(ctx, None) as client:
        resp = await client.post(f"/executions/approvals/{hold.approval_id}:withdraw")
    assert resp.status_code == 401
    assert (await _approval(ctx, hold.approval_id)).state == "pending"


async def test_withdraw_route_answers_404_to_anyone_but_the_filer(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    for caller in (actors.outsider, actors.ownerless_agent, actors.owner, actors.admin):
        async with _client(ctx, caller) as client:
            resp = await client.post(f"/executions/approvals/{hold.approval_id}:withdraw")
        assert resp.status_code == 404, caller.sub
        assert resp.headers["content-type"].startswith("application/problem+json")
    assert (await _approval(ctx, hold.approval_id)).state == "pending"
    assert (await _job(ctx, hold.job_id)).status == JobStatus.HELD


async def test_withdraw_route_answers_409_once_settled(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    await ExecutionApprovalService(ctx).decide(
        hold.approval_id, DecideInput(decision=ApprovalDecision.DENY), identity=actors.owner
    )
    async with _client(ctx, actors.agent) as client:
        resp = await client.post(f"/executions/approvals/{hold.approval_id}:withdraw")
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("execution_approval_already_decided")
    assert (await _job(ctx, hold.job_id)).status == JobStatus.FAILED


async def test_approval_routes_reject_an_unauthenticated_caller(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    async with _client(ctx, None) as client:
        listed = await client.get("/executions/approvals")
        detail = await client.get(f"/executions/approvals/{hold.approval_id}")
        decided = await client.post(
            f"/executions/approvals/{hold.approval_id}:decide", json={"decision": "approve"}
        )
    assert (listed.status_code, detail.status_code, decided.status_code) == (401, 401, 401)
    assert (await _approval(ctx, hold.approval_id)).state == "pending"


async def test_list_route_shows_each_reviewer_only_what_they_may_review(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    owned = await _hold(ctx, actors.agent, path="/v1/owned")
    orphan = await _hold(ctx, actors.ownerless_agent, path="/v1/orphan")

    async def _ids(identity: Identity, query: str = "") -> set[str]:
        async with _client(ctx, identity) as client:
            resp = await client.get(f"/executions/approvals{query}")
        assert resp.status_code == 200, resp.text
        return {row["id"] for row in resp.json()["data"]}

    assert await _ids(actors.owner) == {owned.approval_id}
    assert await _ids(actors.outsider) == set()
    assert await _ids(actors.admin) == {owned.approval_id, orphan.approval_id}
    assert await _ids(actors.admin, "?state=approved") == set()


async def test_get_route_returns_the_detail_and_hides_it_from_outsiders(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    async with _client(ctx, actors.owner) as client:
        resp = await client.get(f"/executions/approvals/{hold.approval_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == hold.approval_id
    assert body["state"] == "pending"
    assert body["agent_owner_id"] == actors.owner.sub
    assert body["request"]["body"] == _BODY.decode()
    assert body["_links"]["job"].endswith(f"/jobs/{hold.job_id}")

    async with _client(ctx, actors.outsider) as client:
        hidden = await client.get(f"/executions/approvals/{hold.approval_id}")
    assert hidden.status_code == 404
    assert hidden.headers["content-type"].startswith("application/problem+json")


async def test_decide_route_approves_for_the_owner_and_conflicts_after(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    async with _client(ctx, actors.owner) as client:
        resp = await client.post(
            f"/executions/approvals/{hold.approval_id}:decide",
            json={"decision": "approve", "reason": "looks right"},
        )
        again = await client.post(
            f"/executions/approvals/{hold.approval_id}:decide", json={"decision": "deny"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "approved"
    assert resp.json()["decision_reason"] == "looks right"
    assert (await _job(ctx, hold.job_id)).status == JobStatus.QUEUED
    assert again.status_code == 409
    assert (await _approval(ctx, hold.approval_id)).state == "approved"


async def test_decide_route_denies_with_a_readable_result(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    async with _client(ctx, actors.admin) as client:
        resp = await client.post(
            f"/executions/approvals/{hold.approval_id}:decide",
            json={"decision": "deny", "reason": "not today"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "denied"
    assert (await _job(ctx, hold.job_id)).status == JobStatus.FAILED
    assert (await _result_body(ctx, hold.job_id, actors.admin))["status"] == 403


async def test_decide_route_refuses_agents_and_hides_from_outsiders(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    admin_agent = actors.agent.model_copy(update={"permissions": ["org:admin"]})
    async with _client(ctx, admin_agent) as client:
        as_agent = await client.post(
            f"/executions/approvals/{hold.approval_id}:decide", json={"decision": "approve"}
        )
    async with _client(ctx, actors.outsider) as client:
        as_outsider = await client.post(
            f"/executions/approvals/{hold.approval_id}:decide", json={"decision": "approve"}
        )
    async with _client(ctx, actors.owner) as client:
        malformed = await client.post(
            f"/executions/approvals/{hold.approval_id}:decide", json={"decision": "maybe"}
        )
    assert as_agent.status_code == 403
    assert as_outsider.status_code == 404
    assert malformed.status_code == 422
    assert (await _approval(ctx, hold.approval_id)).state == "pending"
    assert (await _job(ctx, hold.job_id)).status == JobStatus.HELD


async def test_job_routes_link_a_held_execution_to_its_approval(
    integration_context: Context, actors: _Actors
) -> None:
    ctx = integration_context
    hold = await _hold(ctx, actors.agent)
    async with ctx.admin_db.transaction() as session:
        other = Job(
            kind="import",
            status=JobStatus.QUEUED,
            payload={},
            created_by=actors.agent.sub,
            actor_type=actors.agent.actor_type.value,
        )
        session.add(other)
        await session.flush()
        other_id = other.id
    owner = actors.owner.model_copy(update={"permissions": ["jobs:read"]})
    async with _client(ctx, owner) as client:
        detail = await client.get(f"/jobs/{hold.job_id}")
        listed = await client.get("/jobs")
        plain = await client.get(f"/jobs/{other_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["approval_id"] == hold.approval_id
    assert body["_links"]["approval"].endswith(f"/executions/approvals/{hold.approval_id}")
    rows = {row["job_id"]: row for row in listed.json()["data"]}
    assert rows[hold.job_id]["approval_id"] == hold.approval_id
    assert rows[other_id]["approval_id"] is None
    assert plain.json()["approval_id"] is None
    assert plain.json()["_links"].get("approval") is None
