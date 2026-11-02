"""Hold service — list, read, decide on and withdraw held executions, and read their result.

A hold is an execution a require-approval rule parked for a human, stored as an
approval row over a held execution job. Visibility follows ``build_access_filters``:
``org:admin`` sees every hold, a user sees holds filed by agents they own, an
agent sees its own. Deciding needs that same reviewer visibility — the agent's
owner or an ``org:admin``; an ownerless agent's holds are admin-only — and is
refused outright for any agent caller, whatever its scopes. Withdrawing is open
only to the identity that filed the hold.

The holds routes take no ``jobs:*`` permission, so the service reads a hold's
job only through ``JobRepository.get_held_execution`` and
``held_execution_statuses``: the held execution job its approval names, under
the caller's job visibility too. No hold reaches any other job.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import structlog

from jentic_one.admin.core.schema.held_executions import HeldExecution
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos import AgentRepository, AuditRepository, JobResultRepository
from jentic_one.admin.repos.held_execution_repo import HeldExecutionRepository
from jentic_one.admin.repos.job_repo import JobRepository
from jentic_one.admin.scoping.filters import build_access_filters
from jentic_one.admin.services.errors import (
    HeldExecutionAlreadyDecidedError,
    HeldExecutionForbiddenError,
    HeldExecutionJobNotHeldError,
    HeldExecutionNotFoundError,
    HeldExecutionResultExpiredError,
    HeldExecutionResultNotReadyError,
)
from jentic_one.admin.services.job_result_service import JobResultService
from jentic_one.admin.services.schemas.held_executions import (
    DecideInput,
    HeldExecutionDetailView,
    HeldExecutionView,
    HeldRequestView,
)
from jentic_one.admin.services.schemas.jobs import JobResultView
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.jobs.hold import (
    ENCRYPTED_PAYLOAD_KEY,
    PROBLEM_CONTENT_TYPE,
    approval_denied_problem,
    held_call_summary,
)
from jentic_one.shared.jobs.wakeup import notify_job_enqueued
from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.held_executions import ApprovalDecision, HeldExecutionState
from jentic_one.shared.models.jobs import JobKind, JobStatus
from jentic_one.shared.pagination import Page, decode_cursor, encode_cursor

logger = structlog.get_logger(__name__)

#: Cap on the held body shown to a reviewer; the page notes the truncation.
_MAX_REVIEW_BODY_BYTES = 64 << 10

#: A hold's call has a result once its job is completed, or failed with a
#: recorded problem (a denied or expired approval, or a run-time refusal).
_RESULT_STATUSES = frozenset({JobStatus.COMPLETED.value, JobStatus.FAILED.value})


def _execution_status(job_status: str | None) -> str | None:
    """A held execution job's status as the holds surface names it.

    ``dead_letter`` is a worker retry detail: to a hold it is a failed call.
    """
    if job_status == JobStatus.DEAD_LETTER.value:
        return JobStatus.FAILED.value
    return job_status


class HeldExecutionService:
    """Read and decide execution approvals."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_all(
        self,
        *,
        identity: Identity,
        state: HeldExecutionState | None = None,
        agent_id: str | None = None,
        cursor: str | None = None,
        limit: int = 25,
    ) -> Page[HeldExecutionView]:
        cursor_key = decode_cursor(cursor, id_kind="str") if cursor is not None else None
        filters = build_access_filters(identity, HeldExecution)
        async with self._ctx.admin_db.session() as session:
            rows = await HeldExecutionRepository.list_all(
                session,
                state=state.value if state is not None else None,
                agent_id=agent_id,
                limit=limit + 1,
                cursor=cursor_key,
                filters=filters,
            )
            statuses = await JobRepository.held_execution_statuses(
                session,
                [r.job_id for r in rows[:limit]],
                filters=build_access_filters(identity, Job),
            )
        has_more = len(rows) > limit
        data = [
            HeldExecutionView.model_validate(r).model_copy(
                update={"execution_status": _execution_status(statuses.get(r.job_id))}
            )
            for r in rows[:limit]
        ]
        next_cursor = encode_cursor(data[-1].created_at, data[-1].id) if has_more else None
        return Page(data=data, has_more=has_more, next_cursor=next_cursor)

    async def get(self, approval_id: str, *, identity: Identity) -> HeldExecutionDetailView:
        filters = build_access_filters(identity, HeldExecution)
        async with self._ctx.admin_db.session() as session:
            row = await HeldExecutionRepository.get_by_id(session, approval_id, filters=filters)
            if row is None:
                raise HeldExecutionNotFoundError(approval_id)
            agent = await AgentRepository.get_by_id(session, row.agent_id)
            job = await JobRepository.get_held_execution(
                session, row.job_id, filters=build_access_filters(identity, Job)
            )
        view = HeldExecutionView.model_validate(row)
        return HeldExecutionDetailView(
            **view.model_dump(exclude={"execution_status"}),
            execution_status=_execution_status(str(job.status) if job is not None else None),
            agent_name=agent.name if agent is not None else None,
            agent_owner_id=agent.owner_id if agent is not None else None,
            request=self._held_request(job.payload if job is not None else None),
        )

    async def get_result(self, approval_id: str, *, identity: Identity) -> JobResultView:
        """The finished call's result: the upstream response, or why it never ran.

        Read through the hold, never by job id: the job is the held execution
        the visible approval names (``JobRepository.get_held_execution``), and
        its result row is read only after that lookup succeeds.
        """
        filters = build_access_filters(identity, HeldExecution)
        async with self._ctx.admin_db.session() as session:
            row = await HeldExecutionRepository.get_by_id(session, approval_id, filters=filters)
            if row is None:
                raise HeldExecutionNotFoundError(approval_id)
            job = await JobRepository.get_held_execution(
                session, row.job_id, filters=build_access_filters(identity, Job)
            )
            if job is None:
                raise HeldExecutionNotFoundError(approval_id)
            status = _execution_status(str(job.status)) or ""
            if status not in _RESULT_STATUSES:
                raise HeldExecutionResultNotReadyError(approval_id, status)
            result = await JobResultRepository.get_by_job_id(session, job.id)
            if result is None:
                if status == JobStatus.FAILED.value:
                    # A failure with no recorded problem body has nothing to show.
                    raise HeldExecutionResultNotReadyError(approval_id, status)
                raise HeldExecutionResultExpiredError(approval_id)
            if (
                result.available_until is not None
                and result.available_until < await HeldExecutionRepository.now(session)
            ):
                raise HeldExecutionResultExpiredError(approval_id)
        return JobResultService.to_view(result)

    def _held_request(self, payload: dict[str, Any] | None) -> HeldRequestView | None:
        """Decrypt the held job payload into what the reviewer is approving.

        Upstream credentials are injected only when the job runs, so the
        payload never carries them.
        """
        if not payload:
            return None
        if ENCRYPTED_PAYLOAD_KEY in payload:
            if not self._ctx.has_encryption_keyset:
                logger.warning("held_payload_unreadable_without_keyset")
                return None
            payload = json.loads(self._ctx.encryption.decrypt(str(payload[ENCRYPTED_PAYLOAD_KEY])))
        raw = base64.b64decode(payload["body_b64"]) if payload.get("body_b64") else b""
        truncated = len(raw) > _MAX_REVIEW_BODY_BYTES
        body = raw[:_MAX_REVIEW_BODY_BYTES].decode("utf-8", errors="replace") if raw else None
        return HeldRequestView(
            method=str(payload.get("method", "")),
            url=str(payload.get("upstream_url", "")),
            body=body,
            body_truncated=truncated,
        )

    async def decide(
        self, approval_id: str, body: DecideInput, *, identity: Identity
    ) -> HeldExecutionView:
        """Approve or deny a pending approval — compare-and-set, first reviewer wins.

        In one transaction: approve moves the held job to ``queued``; deny fails
        it with a permission-denied problem as its result. A job that is no
        longer ``held`` raises ``HeldExecutionJobNotHeldError`` and rolls
        the decision back, so the approval stays ``pending``. The decision is
        audit-logged and emits ``execution.approval_decided``.
        """
        if identity.actor_type == ActorType.AGENT:
            raise HeldExecutionForbiddenError(
                f"Agent '{identity.sub}' cannot decide execution approvals"
            )
        approve = body.decision == ApprovalDecision.APPROVE
        new_state = HeldExecutionState.APPROVED if approve else HeldExecutionState.DENIED
        filters = build_access_filters(identity, HeldExecution)
        async with self._ctx.admin_db.transaction() as session:
            now = await HeldExecutionRepository.now(session)
            visible = await HeldExecutionRepository.get_by_id(session, approval_id, filters=filters)
            if visible is None:
                raise HeldExecutionNotFoundError(approval_id)
            updated = await HeldExecutionRepository.decide(
                session,
                approval_id,
                new_state=new_state.value,
                decided_by=identity.sub,
                decision_reason=body.reason,
                now=now,
            )
            if updated is None:
                # Read the state from the database: the row loaded above is
                # cached in this session and predates a concurrent settle.
                state = (
                    await HeldExecutionRepository.get_state(session, approval_id) or visible.state
                )
                if state == HeldExecutionState.PENDING:
                    state = HeldExecutionState.EXPIRED.value
                raise HeldExecutionAlreadyDecidedError(
                    f"Execution approval '{approval_id}' is already {state}"
                )
            moved = await JobRepository.transition(
                session,
                updated.job_id,
                from_status=JobStatus.HELD,
                to_status=JobStatus.QUEUED if approve else JobStatus.FAILED,
            )
            if not moved:
                # Raising rolls the decision back with the transaction.
                raise HeldExecutionJobNotHeldError(approval_id, updated.job_id)
            if approve:
                # The released job is claimable once this commits; wake any
                # worker in this process or across processes instead of waiting
                # for its next poll.
                await notify_job_enqueued(session, JobKind.EXECUTION)
            else:
                await JobRepository.update(session, updated.job_id, error="approval denied")
                await JobResultRepository.create(
                    session,
                    job_id=updated.job_id,
                    kind=JobKind.EXECUTION.value,
                    body=approval_denied_problem(approval_id, body.reason),
                    content_type=PROBLEM_CONTENT_TYPE,
                    created_by=identity.sub,
                )
            agent = await AgentRepository.get_by_id(session, updated.agent_id)
            await AuditRepository.record(
                session,
                action=AuditAction.APPROVE if approve else AuditAction.DENY,
                target_type=AuditTargetType.HELD_EXECUTION,
                target_id=approval_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                before={"state": HeldExecutionState.PENDING.value},
                after={"state": new_state.value},
                reason=body.reason,
            )
            await emit_event_best_effort(
                session,
                type=EventType.EXECUTION_APPROVAL_DECIDED,
                severity=EventSeverity.INFO,
                summary=held_call_summary(
                    agent_name=agent.name if agent is not None else None,
                    agent_id=updated.agent_id,
                    method=updated.method,
                    path=updated.path,
                    outcome=new_state.value,
                ),
                job_id=updated.job_id,
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
                data={
                    "held_execution_id": approval_id,
                    "agent_id": updated.agent_id,
                    "decision": body.decision.value,
                    "method": updated.method,
                    "path": updated.path,
                },
            )
            view = HeldExecutionView.model_validate(updated).model_copy(
                update={
                    "execution_status": (
                        JobStatus.QUEUED.value if approve else JobStatus.FAILED.value
                    )
                }
            )
        logger.info(
            "held_execution_decided",
            approval_id=approval_id,
            decision=body.decision.value,
            actor_id=identity.sub,
        )
        return view

    async def withdraw(self, approval_id: str, *, identity: Identity) -> HeldExecutionView:
        """Withdraw a pending approval — only the identity that filed the hold.

        A compare-and-set on ``pending``: in one transaction the approval
        becomes ``withdrawn`` and its held job ``cancelled``, with no result
        written. Any other caller gets ``HeldExecutionNotFoundError``, as
        if the row did not exist; an approval that is no longer pending gets
        ``HeldExecutionAlreadyDecidedError``. The withdrawal is
        audit-logged and emits ``execution.approval_withdrawn``.
        """
        async with self._ctx.admin_db.transaction() as session:
            now = await HeldExecutionRepository.now(session)
            row = await HeldExecutionRepository.get_by_id(session, approval_id)
            if row is None or row.created_by != identity.sub:
                raise HeldExecutionNotFoundError(approval_id)
            updated = await HeldExecutionRepository.withdraw(
                session, approval_id, created_by=identity.sub, now=now
            )
            if updated is None:
                state = await HeldExecutionRepository.get_state(session, approval_id)
                raise HeldExecutionAlreadyDecidedError(
                    f"Execution approval '{approval_id}' is already {state or row.state}"
                )
            moved = await JobRepository.transition(
                session,
                updated.job_id,
                from_status=JobStatus.HELD,
                to_status=JobStatus.CANCELLED,
            )
            if not moved:
                raise HeldExecutionJobNotHeldError(approval_id, updated.job_id)
            agent = await AgentRepository.get_by_id(session, updated.agent_id)
            await AuditRepository.record(
                session,
                action=AuditAction.WITHDRAW,
                target_type=AuditTargetType.HELD_EXECUTION,
                target_id=approval_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                before={"state": HeldExecutionState.PENDING.value},
                after={"state": HeldExecutionState.WITHDRAWN.value},
            )
            await emit_event_best_effort(
                session,
                type=EventType.EXECUTION_APPROVAL_WITHDRAWN,
                severity=EventSeverity.INFO,
                summary=held_call_summary(
                    agent_name=agent.name if agent is not None else None,
                    agent_id=updated.agent_id,
                    method=updated.method,
                    path=updated.path,
                    outcome="withdrawn",
                ),
                job_id=updated.job_id,
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
                data={
                    "held_execution_id": approval_id,
                    "agent_id": updated.agent_id,
                    "method": updated.method,
                    "path": updated.path,
                },
            )
            view = HeldExecutionView.model_validate(updated).model_copy(
                update={"execution_status": JobStatus.CANCELLED.value}
            )
        logger.info(
            "held_execution_withdrawn",
            approval_id=approval_id,
            job_id=view.job_id,
            actor_id=identity.sub,
        )
        return view
