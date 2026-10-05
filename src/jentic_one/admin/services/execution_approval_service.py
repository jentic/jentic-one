"""Execution approval service — list, get, and decide on held executions."""

from __future__ import annotations

from jentic_one.admin.repos.execution_approval_repo import ExecutionApprovalRepository
from jentic_one.admin.repos.job_repo import JobRepository
from jentic_one.admin.services._support.pagination import Page, decode_cursor, encode_cursor
from jentic_one.admin.services.errors import (
    ExecutionApprovalAlreadyDecidedError,
    ExecutionApprovalNotFoundError,
)
from jentic_one.admin.services.schemas.execution_approvals import (
    DecideInput,
    ExecutionApprovalView,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.jobs import JobStatus


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
        state: str | None = None,
        agent_id: str | None = None,
        cursor: str | None = None,
        limit: int = 25,
    ) -> Page[ExecutionApprovalView]:
        cursor_dt = None
        cursor_id: str | None = None
        if cursor is not None:
            cursor_dt, cursor_id = decode_cursor(cursor)

        async with self._ctx.admin_db.session() as session:
            rows = await ExecutionApprovalRepository.list_by_state(
                session,
                state=state,
                agent_id=agent_id,
                limit=limit + 1,
                cursor_created_at=cursor_dt,
                cursor_id=cursor_id,
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

    async def get_approval(self, approval_id: str) -> ExecutionApprovalView:
        async with self._ctx.admin_db.session() as session:
            row = await ExecutionApprovalRepository.get_by_id(session, approval_id)
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
        next tick. On denial the held job is marked FAILED and no execution runs.
        """
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
                # Either the row doesn't exist or it's no longer pending.
                existing = await ExecutionApprovalRepository.get_by_id(session, approval_id)
                if existing is None:
                    raise ExecutionApprovalNotFoundError(approval_id)
                raise ExecutionApprovalAlreadyDecidedError(
                    f"Approval '{approval_id}' is already in state '{existing.state}'"
                )

            new_job_status = JobStatus.QUEUED if body.decision == "approved" else JobStatus.FAILED
            await JobRepository.update(session, updated.job_id, status=new_job_status)

        return ExecutionApprovalView.model_validate(updated)
