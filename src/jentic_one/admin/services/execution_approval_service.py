"""Execution approval service — list, get, and decide on held executions."""

from __future__ import annotations

from jentic_one.admin.core.schema.execution_approvals import ExecutionApproval
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.repos import AuditRepository
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
    ExecutionApprovalView,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.models.jobs import JobStatus

_DENY_CONTENT_TYPE = "application/problem+json"
_DENY_KIND = "execution"


class ExecutionApprovalService:
    """Business logic for the execution approval surface.

    Decide actions are scoped to admins (``execution_approvals:write``).
    List/detail reads use ``execution_approvals:read``.
    """

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_approvals(
        self,
        *,
        identity: Identity,
        state: str | None = None,
        agent_id: str | None = None,
        cursor: str | None = None,
        limit: int = 25,
    ) -> Page[ExecutionApprovalView]:
        cursor_dt = None
        cursor_id: str | None = None
        if cursor is not None:
            cursor_dt, cursor_id = decode_cursor(cursor)

        access_filters = build_access_filters(identity, ExecutionApproval)
        async with self._ctx.admin_db.session() as session:
            rows = await ExecutionApprovalRepository.list_by_state(
                session,
                state=state,
                agent_id=agent_id,
                limit=limit + 1,
                cursor_created_at=cursor_dt,
                cursor_id=cursor_id,
                extra_filters=access_filters,
            )

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        data = [ExecutionApprovalView.model_validate(r) for r in rows]
        next_cursor = None
        if has_more and data:
            last = data[-1]
            next_cursor = encode_cursor(last.created_at, last.id)

        return Page(data=data, has_more=has_more, next_cursor=next_cursor)

    async def get_approval(self, approval_id: str, *, identity: Identity) -> ExecutionApprovalView:
        access_filters = build_access_filters(identity, ExecutionApproval)
        async with self._ctx.admin_db.session() as session:
            row = await ExecutionApprovalRepository.get_by_id(
                session, approval_id, filters=access_filters
            )
        if row is None:
            raise ExecutionApprovalNotFoundError(approval_id)
        return ExecutionApprovalView.model_validate(row)

    async def decide(
        self,
        approval_id: str,
        body: DecideInput,
        *,
        identity: Identity,
    ) -> ExecutionApprovalView:
        """Approve or deny a pending execution approval.

        On approval the held job flips to QUEUED so the worker claims it on the
        next tick. On denial the held job is marked FAILED and a
        permission-denied result is written to job_results.
        """
        if identity.actor_type == ActorType.AGENT:
            raise ExecutionApprovalForbiddenError(
                "Agents cannot decide execution approvals — only human actors may."
            )

        if body.decision not in ("approved", "denied"):
            raise ExecutionApprovalAlreadyDecidedError(
                f"decision must be 'approved' or 'denied', got {body.decision!r}"
            )

        async with self._ctx.admin_db.transaction() as session:
            updated = await ExecutionApprovalRepository.decide(
                session,
                approval_id,
                new_state=body.decision,
                decided_by=identity.sub,
                decision_reason=body.reason,
            )
            if updated is None:
                # Either the row doesn't exist or it's no longer pending (CAS
                # semantics: the update only matches state = 'pending').
                existing = await ExecutionApprovalRepository.get_by_id(session, approval_id)
                if existing is None:
                    raise ExecutionApprovalNotFoundError(approval_id)
                raise ExecutionApprovalAlreadyDecidedError(
                    f"Approval '{approval_id}' is already in state '{existing.state}'"
                )

            new_job_status = JobStatus.QUEUED if body.decision == "approved" else JobStatus.FAILED
            await JobRepository.update(session, updated.job_id, status=new_job_status)

            if body.decision == "denied":
                # Write a permission-denied result so the agent polling
                # get_execution_result receives a structured error body rather
                # than a missing row.
                reason = body.reason or "Execution denied by reviewer."
                result = JobResult(
                    job_id=updated.job_id,
                    kind=_DENY_KIND,
                    content_type=_DENY_CONTENT_TYPE,
                    body={
                        "type": "execution_approval_denied",
                        "title": "Execution Denied",
                        "status": 403,
                        "detail": reason,
                        "approval_id": approval_id,
                    },
                )
                session.add(result)

            await AuditRepository.record(
                session,
                action=AuditAction.APPROVE if body.decision == "approved" else AuditAction.DENY,
                target_type=AuditTargetType.EXECUTION_APPROVAL,
                target_id=approval_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
            )

        return ExecutionApprovalView.model_validate(updated)
