"""Held execution jobs: filing a require-approval hold and its terminal results.

The broker execute route calls :func:`file_hold` instead of ``enqueue_job``
when the rule evaluator returns ``REQUIRE_APPROVAL``. Filing writes a ``held``
job (not claimable by the worker) and its ``pending`` ``execution_approvals``
row in the caller's admin-DB transaction. The problem bodies a denied,
expired, or unreleased hold fails with live here so the admin decide path, the
worker's expiry sweep, and the run-time gate tell the agent the same story.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from jentic_one.admin.core.schema.execution_approvals import ExecutionApproval
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos.audit_repo import AuditRepository
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.models.actors import ActorType, Origin, actor_type_label_from_id
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.execution_approvals import ExecutionApprovalState
from jentic_one.shared.models.jobs import JobKind, JobStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

APPROVAL_PENDING = ExecutionApprovalState.PENDING
APPROVAL_APPROVED = ExecutionApprovalState.APPROVED
APPROVAL_DENIED = ExecutionApprovalState.DENIED
APPROVAL_EXPIRED = ExecutionApprovalState.EXPIRED

#: Problem types a failed held job's result carries. All are 403
#: permission-denied problems: the call never reached the upstream.
APPROVAL_DENIED_TYPE = "approval_denied"
APPROVAL_EXPIRED_TYPE = "approval_expired"
APPROVAL_REQUIRED_TYPE = "approval_required"
APPROVAL_RESUME_FAILED_TYPE = "approval_resume_failed"

PROBLEM_CONTENT_TYPE = "application/problem+json"

#: A held job's payload is ``{ENCRYPTED_PAYLOAD_KEY: <platform-key ciphertext of
#: the JSON payload>}``; the execution handler decrypts it on claim.
ENCRYPTED_PAYLOAD_KEY = "_enc"

#: What the agent is told to do with a held 202.
HELD_AGENT_DIRECTIVE = (
    "This call needs human approval. Most important: show the user the review_url, "
    "since they cannot approve it without that link, and end your turn so they see it. "
    "Do not call get_execution_result until the user replies; then call it once with "
    "job_id and wait_seconds: 30. If it is still held, tell the user it is waiting for "
    "approval and end your turn again. Do not re-send the call."
)


class PendingApprovalLimitError(Exception):
    """The agent already holds ``limit`` pending approvals."""

    def __init__(self, pending: int, limit: int) -> None:
        super().__init__(f"Agent has {pending} pending approvals (limit {limit})")
        self.pending = pending
        self.limit = limit


@dataclass(frozen=True, slots=True)
class HoldOutcome:
    """The held job and approval an execute call filed or joined."""

    job_id: str
    approval_id: str
    expires_at: datetime
    execution_id: str | None
    joined: bool


def canonical_body(body: bytes | None) -> bytes:
    """The request body in a stable form for fingerprinting.

    JSON bodies are re-serialised with sorted keys and compact separators so
    key order and whitespace do not split identical requests; any other body
    is fingerprinted byte for byte.
    """
    if not body:
        return b""
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def canonical_query(query: str) -> str:
    """The query string in a stable form for fingerprinting.

    Parameters are sorted (blank values kept) so their order does not split
    identical requests; a different value or parameter files a new hold.
    """
    if not query:
        return ""
    return urlencode(sorted(parse_qsl(query, keep_blank_values=True)))


def compute_execution_fingerprint(
    *,
    agent_id: str,
    credential_id: str,
    method: str,
    path: str,
    query: str = "",
    body: bytes | None,
) -> str:
    """SHA-256 over ``(agent_id, credential_id, method, path, query, body)``.

    The query and body are canonicalised (:func:`canonical_query`,
    :func:`canonical_body`).
    """
    digest = hashlib.sha256()
    for part in (agent_id, credential_id, method.upper(), path, canonical_query(query)):
        digest.update(part.encode())
        digest.update(b"\x00")
    digest.update(canonical_body(body))
    return digest.hexdigest()


async def _pending_by_fingerprint(
    session: AsyncSession, fingerprint: str
) -> ExecutionApproval | None:
    result = await session.execute(
        select(ExecutionApproval).where(
            ExecutionApproval.request_fingerprint == fingerprint,
            ExecutionApproval.state == APPROVAL_PENDING,
        )
    )
    return result.scalar_one_or_none()


async def count_pending_by_agent(session: AsyncSession, agent_id: str) -> int:
    """Number of pending approvals the agent holds."""
    result = await session.execute(
        select(func.count())
        .select_from(ExecutionApproval)
        .where(
            ExecutionApproval.agent_id == agent_id,
            ExecutionApproval.state == APPROVAL_PENDING,
        )
    )
    return int(result.scalar_one() or 0)


#: Namespace of the per-agent advisory lock that serialises hold filing.
_HOLD_LOCK_NAMESPACE = "execution_approvals:agent:"


async def _lock_agent_holds(session: AsyncSession, agent_id: str) -> None:
    """Serialise hold filing per agent until the caller's transaction ends.

    The pending cap is a count followed by an insert; two concurrent filings
    by one agent would both count below the cap and both insert. On
    PostgreSQL a transaction-scoped advisory lock keyed on the agent makes
    the second wait for the first to commit, so its count sees the first
    row. SQLite transactions already take the write lock up front
    (``BEGIN IMMEDIATE``), so they never overlap.
    """
    if session.get_bind().dialect.name != "postgresql":
        return
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": _HOLD_LOCK_NAMESPACE + agent_id},
    )


async def _joined(session: AsyncSession, row: ExecutionApproval) -> HoldOutcome:
    job = await session.get(Job, row.job_id)
    return HoldOutcome(
        job_id=str(row.job_id),
        approval_id=str(row.id),
        expires_at=row.expires_at,
        execution_id=job.execution_id if job is not None else None,
        joined=True,
    )


async def file_hold(
    session: AsyncSession,
    *,
    agent_id: str,
    actor_type: str,
    credential_id: str,
    matched_rule_id: str | None,
    api_vendor: str,
    api_name: str,
    api_version: str,
    operation_id: str | None,
    method: str,
    path: str,
    query: str = "",
    body: bytes | None,
    trace_id: str | None,
    execution_id: str,
    payload: dict[str, Any],
    ttl_seconds: int,
    max_pending: int,
) -> HoldOutcome:
    """File (or join) a hold inside the caller's admin-DB transaction.

    Order: the per-agent pending cap first (raises
    :class:`PendingApprovalLimitError`), then the fingerprint join — an
    identical pending request returns its existing job — then the insert of the
    ``held`` job and its ``pending`` approval. A concurrent identical filing
    that wins the partial unique index between the join check and the insert
    is joined as well, so two racing retries never produce two holds. The cap
    is counted under a per-agent lock held to the end of the transaction, so
    concurrent filings cannot together exceed it.
    """
    await _lock_agent_holds(session, agent_id)
    pending = await count_pending_by_agent(session, agent_id)
    if pending >= max_pending:
        raise PendingApprovalLimitError(pending, max_pending)

    fingerprint = compute_execution_fingerprint(
        agent_id=agent_id,
        credential_id=credential_id,
        method=method,
        path=path,
        query=query,
        body=body,
    )
    existing = await _pending_by_fingerprint(session, fingerprint)
    if existing is not None:
        return await _joined(session, existing)

    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    try:
        async with session.begin_nested():
            job: Any = Job(
                kind=str(JobKind.EXECUTION),
                status=JobStatus.HELD,
                execution_id=execution_id,
                payload=dict(payload),
                created_by=agent_id,
                actor_type=actor_type,
            )
            session.add(job)
            await session.flush()
            approval: Any = ExecutionApproval(
                job_id=job.id,
                agent_id=agent_id,
                credential_id=credential_id,
                api_vendor=api_vendor,
                api_name=api_name,
                api_version=api_version,
                operation_id=operation_id,
                method=method.upper(),
                path=path,
                request_fingerprint=fingerprint,
                matched_rule_id=matched_rule_id,
                state=APPROVAL_PENDING,
                expires_at=expires_at,
                trace_id=trace_id,
                created_by=agent_id,
            )
            session.add(approval)
            await session.flush()
    except IntegrityError:
        raced = await _pending_by_fingerprint(session, fingerprint)
        if raced is None:
            raise
        return await _joined(session, raced)
    return HoldOutcome(
        job_id=str(job.id),
        approval_id=str(approval.id),
        expires_at=expires_at,
        execution_id=execution_id,
        joined=False,
    )


async def get_approved_by_job_id(session: AsyncSession, job_id: str) -> ExecutionApproval | None:
    """The ``approved`` approval row for a job, or None."""
    result = await session.execute(
        select(ExecutionApproval).where(
            ExecutionApproval.job_id == job_id,
            ExecutionApproval.state == APPROVAL_APPROVED,
        )
    )
    return result.scalar_one_or_none()


def _approval_problem(
    *, type: str, title: str, detail: str, approval_id: str | None, state: str | None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": type,
        "title": title,
        "status": 403,
        "detail": detail,
        "error_origin": "broker",
    }
    if approval_id is not None:
        body["approval"] = {"id": approval_id, "state": state}
    return body


def approval_denied_problem(approval_id: str, reason: str | None) -> dict[str, Any]:
    """Result body for a held job a reviewer denied."""
    detail = "A reviewer denied this held execution"
    if reason:
        detail = f"{detail}: {reason}"
    return _approval_problem(
        type=APPROVAL_DENIED_TYPE,
        title="Execution approval denied",
        detail=detail,
        approval_id=approval_id,
        state=APPROVAL_DENIED,
    )


def approval_expired_problem(approval_id: str) -> dict[str, Any]:
    """Result body for a held job whose approval window lapsed undecided."""
    return _approval_problem(
        type=APPROVAL_EXPIRED_TYPE,
        title="Execution approval expired",
        detail="No reviewer decided this held execution before its approval expired",
        approval_id=approval_id,
        state=APPROVAL_EXPIRED,
    )


def approval_required_problem(instance: str) -> dict[str, Any]:
    """Run-time gate result for a job that reaches require-approval with no approved row."""
    body = _approval_problem(
        type=APPROVAL_REQUIRED_TYPE,
        title="Execution approval required",
        detail="A permission rule requires approval for this call and the job carries none",
        approval_id=None,
        state=None,
    )
    body["instance"] = instance
    return body


def approval_resume_failed_problem(approval_id: str) -> dict[str, Any]:
    """Result body for an approved job re-claimed after its worker died mid-run.

    The upstream call may already have happened, so the job is failed rather
    than run a second time.
    """
    body = _approval_problem(
        type=APPROVAL_RESUME_FAILED_TYPE,
        title="Approved execution could not resume",
        detail="The worker running this approved execution stopped mid-run; it is not re-run",
        approval_id=approval_id,
        state=APPROVAL_APPROVED,
    )
    body["status"] = 409
    return body


async def fail_held_job(
    session: AsyncSession, *, job_id: str, problem: dict[str, Any], error: str
) -> bool:
    """Fail a ``held`` job with a problem-body result; False when it is no longer held."""
    result = await session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.HELD)
        .values(status=JobStatus.FAILED, error=error, visible_at=None)
        .returning(Job.created_by)
    )
    created_by = result.scalar_one_or_none()
    if created_by is None:
        return False
    session.add(
        JobResult(
            job_id=job_id,
            kind=str(JobKind.EXECUTION),
            content_type=PROBLEM_CONTENT_TYPE,
            body=problem,
            created_by=created_by,
        )
    )
    await session.flush()
    return True


def _filer_actor_type(agent_id: str) -> str:
    """Actor type of a hold's filer: holds come from agent rule bindings."""
    try:
        return actor_type_label_from_id(agent_id)
    except ValueError:
        return ActorType.AGENT.value


async def expire_lapsed_approvals(session: AsyncSession, *, now: datetime) -> int:
    """Expire every pending approval past ``expires_at`` and fail its held job.

    Runs in the caller's transaction, so each approval, its job, its audit
    entry and its ``execution.approval_expired`` event settle together. The
    audit entry is attributed to the agent that filed the hold (there is no
    system actor) with a ``system`` origin. Returns the number expired.
    """
    result = await session.execute(
        update(ExecutionApproval)
        .where(
            ExecutionApproval.state == APPROVAL_PENDING,
            ExecutionApproval.expires_at <= now,
        )
        .values(state=APPROVAL_EXPIRED, decided_at=now)
        .returning(
            ExecutionApproval.id,
            ExecutionApproval.job_id,
            ExecutionApproval.agent_id,
            ExecutionApproval.method,
            ExecutionApproval.path,
            ExecutionApproval.trace_id,
        )
        .execution_options(synchronize_session=False)
    )
    expired = list(result.all())
    for approval_id, job_id, agent_id, method, path, trace_id in expired:
        await fail_held_job(
            session,
            job_id=job_id,
            problem=approval_expired_problem(approval_id),
            error="approval expired",
        )
        actor_type = _filer_actor_type(agent_id)
        await AuditRepository.record(
            session,
            action=AuditAction.EXPIRE,
            target_type=AuditTargetType.EXECUTION_APPROVAL,
            target_id=approval_id,
            actor_type=actor_type,
            actor_id=agent_id,
            before={"state": APPROVAL_PENDING.value},
            after={"state": APPROVAL_EXPIRED.value},
            job_id=job_id,
            trace_id=trace_id,
            reason="approval window lapsed with no decision",
            origin=Origin.SYSTEM.value,
        )
        await emit_event_best_effort(
            session,
            type=EventType.EXECUTION_APPROVAL_EXPIRED,
            severity=EventSeverity.INFO,
            summary=f"Execution approval {approval_id} expired",
            trace_id=trace_id,
            job_id=job_id,
            created_by=agent_id,
            actor_id=agent_id,
            actor_type=actor_type,
            data={
                "approval_id": approval_id,
                "agent_id": agent_id,
                "method": method,
                "path": path,
            },
        )
    return len(expired)


async def set_execution_id_for_job(session: AsyncSession, job_id: str, execution_id: str) -> None:
    """Link an approved job's approval to the execution record its run wrote."""
    await session.execute(
        update(ExecutionApproval)
        .where(ExecutionApproval.job_id == job_id, ExecutionApproval.state == APPROVAL_APPROVED)
        .values(execution_id=execution_id)
        .execution_options(synchronize_session=False)
    )
