"""Execution approval service — list, read, decide on and withdraw held executions.

Visibility follows jobs (``build_access_filters``): ``org:admin`` sees every
approval, a user sees approvals filed by agents they own, an agent sees its
own. Deciding needs that same reviewer visibility — the agent's owner or an
``org:admin``; an ownerless agent's approvals are admin-only — and is refused
outright for any agent caller, whatever its scopes. Withdrawing is open only
to the identity that filed the hold.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

import structlog

from jentic_one.admin.core.schema.execution_approvals import ExecutionApproval
from jentic_one.admin.repos import AgentRepository, AuditRepository, JobResultRepository
from jentic_one.admin.repos.execution_approval_repo import ExecutionApprovalRepository
from jentic_one.admin.repos.job_repo import JobRepository
from jentic_one.admin.scoping.filters import build_access_filters
from jentic_one.admin.services._support.pagination import Page, decode_cursor, encode_cursor
from jentic_one.admin.services.errors import (
    ExecutionApprovalAlreadyDecidedError,
    ExecutionApprovalForbiddenError,
    ExecutionApprovalNotFoundError,
)
from jentic_one.admin.services.schemas.execution_approvals import (
    DecideInput,
    ExecutionApprovalDetailView,
    ExecutionApprovalView,
    HeldRequestView,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.jobs.hold import (
    ENCRYPTED_PAYLOAD_KEY,
    PROBLEM_CONTENT_TYPE,
    approval_denied_problem,
)
from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.execution_approvals import ApprovalDecision, ExecutionApprovalState
from jentic_one.shared.models.jobs import JobKind, JobStatus

logger = structlog.get_logger(__name__)

#: Cap on the held body shown to a reviewer; the page notes the truncation.
_MAX_REVIEW_BODY_BYTES = 64 << 10


class ExecutionApprovalService:
    """Read and decide execution approvals."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_all(
        self,
        *,
        identity: Identity,
        state: ExecutionApprovalState | None = None,
        agent_id: str | None = None,
        cursor: str | None = None,
        limit: int = 25,
    ) -> Page[ExecutionApprovalView]:
        cursor_dt: datetime | None = None
        cursor_id: str | None = None
        if cursor is not None:
            cursor_dt, cursor_id = decode_cursor(cursor)
        filters = build_access_filters(identity, ExecutionApproval)
        async with self._ctx.admin_db.session() as session:
            rows = await ExecutionApprovalRepository.list_all(
                session,
                state=state.value if state is not None else None,
                agent_id=agent_id,
                limit=limit + 1,
                cursor_created_at=cursor_dt,
                cursor_id=cursor_id,
                filters=filters,
            )
        has_more = len(rows) > limit
        data = [ExecutionApprovalView.model_validate(r) for r in rows[:limit]]
        next_cursor = encode_cursor(data[-1].created_at, data[-1].id) if has_more else None
        return Page(data=data, has_more=has_more, next_cursor=next_cursor)

    async def get(self, approval_id: str, *, identity: Identity) -> ExecutionApprovalDetailView:
        filters = build_access_filters(identity, ExecutionApproval)
        async with self._ctx.admin_db.session() as session:
            row = await ExecutionApprovalRepository.get_by_id(session, approval_id, filters=filters)
            if row is None:
                raise ExecutionApprovalNotFoundError(approval_id)
            agent = await AgentRepository.get_by_id(session, row.agent_id)
            job = await JobRepository.get_by_id(session, row.job_id)
        view = ExecutionApprovalView.model_validate(row)
        return ExecutionApprovalDetailView(
            **view.model_dump(),
            agent_name=agent.name if agent is not None else None,
            agent_owner_id=agent.owner_id if agent is not None else None,
            request=self._held_request(job.payload if job is not None else None),
        )

    def _held_request(self, payload: dict[str, Any] | None) -> HeldRequestView | None:
        """Decrypt the held job payload into what the reviewer is approving.

        Upstream credentials are injected only when the job runs, so the
        payload never carries them.
        """
        if not payload:
            return None
        if ENCRYPTED_PAYLOAD_KEY in payload:
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
    ) -> ExecutionApprovalView:
        """Approve or deny a pending approval — compare-and-set, first reviewer wins.

        In one transaction: approve moves the held job to ``queued``; deny fails
        it with a permission-denied problem as its result. The decision is
        audit-logged and emits ``execution.approval_decided``.
        """
        if identity.actor_type == ActorType.AGENT:
            raise ExecutionApprovalForbiddenError(
                f"Agent '{identity.sub}' cannot decide execution approvals"
            )
        approve = body.decision == ApprovalDecision.APPROVE
        new_state = ExecutionApprovalState.APPROVED if approve else ExecutionApprovalState.DENIED
        filters = build_access_filters(identity, ExecutionApproval)
        now = datetime.now(UTC)
        async with self._ctx.admin_db.transaction() as session:
            visible = await ExecutionApprovalRepository.get_by_id(
                session, approval_id, filters=filters
            )
            if visible is None:
                raise ExecutionApprovalNotFoundError(approval_id)
            updated = await ExecutionApprovalRepository.decide(
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
                    await ExecutionApprovalRepository.get_state(session, approval_id)
                    or visible.state
                )
                if state == ExecutionApprovalState.PENDING:
                    state = ExecutionApprovalState.EXPIRED.value
                raise ExecutionApprovalAlreadyDecidedError(
                    f"Execution approval '{approval_id}' is already {state}"
                )
            if approve:
                await JobRepository.transition(
                    session,
                    updated.job_id,
                    from_status=JobStatus.HELD,
                    to_status=JobStatus.QUEUED,
                )
            else:
                moved = await JobRepository.transition(
                    session,
                    updated.job_id,
                    from_status=JobStatus.HELD,
                    to_status=JobStatus.FAILED,
                )
                if moved:
                    await JobRepository.update(session, updated.job_id, error="approval denied")
                    await JobResultRepository.create(
                        session,
                        job_id=updated.job_id,
                        kind=JobKind.EXECUTION.value,
                        body=approval_denied_problem(approval_id, body.reason),
                        content_type=PROBLEM_CONTENT_TYPE,
                        created_by=identity.sub,
                    )
            await AuditRepository.record(
                session,
                action=AuditAction.APPROVE if approve else AuditAction.DENY,
                target_type=AuditTargetType.EXECUTION_APPROVAL,
                target_id=approval_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                before={"state": ExecutionApprovalState.PENDING.value},
                after={"state": new_state.value},
                reason=body.reason,
            )
            await emit_event_best_effort(
                session,
                type=EventType.EXECUTION_APPROVAL_DECIDED,
                severity=EventSeverity.INFO,
                summary=f"Execution approval {approval_id} {new_state.value}",
                job_id=updated.job_id,
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
                data={
                    "approval_id": approval_id,
                    "agent_id": updated.agent_id,
                    "decision": body.decision.value,
                    "method": updated.method,
                    "path": updated.path,
                },
            )
            view = ExecutionApprovalView.model_validate(updated)
        logger.info(
            "execution_approval_decided",
            approval_id=approval_id,
            decision=body.decision.value,
            actor_id=identity.sub,
        )
        return view

    async def withdraw(self, approval_id: str, *, identity: Identity) -> ExecutionApprovalView:
        """Withdraw a pending approval — only the identity that filed the hold.

        A compare-and-set on ``pending``: in one transaction the approval
        becomes ``withdrawn`` and its held job ``cancelled``, with no result
        written. Any other caller gets ``ExecutionApprovalNotFoundError``, as
        if the row did not exist; an approval that is no longer pending gets
        ``ExecutionApprovalAlreadyDecidedError``. The withdrawal is
        audit-logged and emits ``execution.approval_withdrawn``.
        """
        now = datetime.now(UTC)
        async with self._ctx.admin_db.transaction() as session:
            row = await ExecutionApprovalRepository.get_by_id(session, approval_id)
            if row is None or row.created_by != identity.sub:
                raise ExecutionApprovalNotFoundError(approval_id)
            updated = await ExecutionApprovalRepository.withdraw(
                session, approval_id, created_by=identity.sub, now=now
            )
            if updated is None:
                state = await ExecutionApprovalRepository.get_state(session, approval_id)
                raise ExecutionApprovalAlreadyDecidedError(
                    f"Execution approval '{approval_id}' is already {state or row.state}"
                )
            await JobRepository.transition(
                session,
                updated.job_id,
                from_status=JobStatus.HELD,
                to_status=JobStatus.CANCELLED,
            )
            await AuditRepository.record(
                session,
                action=AuditAction.WITHDRAW,
                target_type=AuditTargetType.EXECUTION_APPROVAL,
                target_id=approval_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                before={"state": ExecutionApprovalState.PENDING.value},
                after={"state": ExecutionApprovalState.WITHDRAWN.value},
            )
            await emit_event_best_effort(
                session,
                type=EventType.EXECUTION_APPROVAL_WITHDRAWN,
                severity=EventSeverity.INFO,
                summary=f"Execution approval {approval_id} withdrawn",
                job_id=updated.job_id,
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
                data={
                    "approval_id": approval_id,
                    "agent_id": updated.agent_id,
                    "method": updated.method,
                    "path": updated.path,
                },
            )
            view = ExecutionApprovalView.model_validate(updated)
        logger.info(
            "execution_approval_withdrawn",
            approval_id=approval_id,
            job_id=view.job_id,
            actor_id=identity.sub,
        )
        return view
