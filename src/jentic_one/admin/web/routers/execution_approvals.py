"""Execution approvals router — list, get, and decide on held executions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from jentic_one.admin.services.execution_approval_service import ExecutionApprovalService
from jentic_one.admin.services.schemas.execution_approvals import DecideInput
from jentic_one.admin.web.deps import get_execution_approval_service
from jentic_one.admin.web.schemas.execution_approvals import (
    DecideRequest,
    ExecutionApprovalLinksResponse,
    ExecutionApprovalListResponse,
    ExecutionApprovalResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.links import build_link

router = APIRouter()


def _approval_response(view: object, request: Request) -> ExecutionApprovalResponse:
    """Project a view model to an API response."""
    from jentic_one.admin.services.schemas.execution_approvals import ExecutionApprovalView

    assert isinstance(view, ExecutionApprovalView)
    job_link = build_link(request, f"/jobs/{view.job_id}")
    links = ExecutionApprovalLinksResponse(
        self_link=build_link(request, f"/execution-approvals/{view.id}"),
        job=job_link,
    )
    return ExecutionApprovalResponse(
        id=view.id,
        job_id=view.job_id,
        agent_id=view.agent_id,
        credential_id=view.credential_id,
        api_vendor=view.api_vendor,
        api_name=view.api_name,
        api_version=view.api_version,
        operation_id=view.operation_id,
        method=view.method,
        path=view.path,
        matched_rule_id=view.matched_rule_id,
        state=view.state,
        expires_at=view.expires_at,
        decided_at=view.decided_at,
        decided_by=view.decided_by,
        decision_reason=view.decision_reason,
        trace_id=view.trace_id,
        execution_id=view.execution_id,
        created_at=view.created_at,
        updated_at=view.updated_at,
        links=links,
    )


@router.get("/execution-approvals")
async def list_execution_approvals(
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["execution_approvals:read"]),
    svc: ExecutionApprovalService = Depends(get_execution_approval_service),
    state: str | None = None,
    agent_id: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> ExecutionApprovalListResponse:
    """List execution approvals with optional state/agent filters."""
    page = await svc.list_approvals(
        state=state,
        agent_id=agent_id,
        cursor=cursor,
        limit=limit,
    )
    return ExecutionApprovalListResponse(
        data=[_approval_response(v, request) for v in page.data],
        has_more=page.next_cursor is not None,
        next_cursor=page.next_cursor,
    )


@router.get("/execution-approvals/{approval_id}")
async def get_execution_approval(
    approval_id: str,
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["execution_approvals:read"]),
    svc: ExecutionApprovalService = Depends(get_execution_approval_service),
) -> ExecutionApprovalResponse:
    """Get the detail of one execution approval."""
    view = await svc.get_approval(approval_id)
    return _approval_response(view, request)


@router.post("/execution-approvals/{approval_id}/:decide")
async def decide_execution_approval(
    approval_id: str,
    body: DecideRequest,
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["execution_approvals:write"]),
    svc: ExecutionApprovalService = Depends(get_execution_approval_service),
) -> ExecutionApprovalResponse:
    """Approve or deny a pending execution approval.

    ``decision`` must be ``"approved"`` or ``"denied"``. An optional ``reason``
    is stored on the approval row for audit purposes.

    Approving flips the held job to QUEUED so the worker picks it up on the
    next tick. Denying marks the job FAILED.
    """
    view = await svc.decide(
        approval_id,
        DecideInput(decision=body.decision, reason=body.reason),
        identity=identity,
    )
    return _approval_response(view, request)
