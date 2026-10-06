"""Execution approvals router — review, decide and withdraw require-approval holds.

The routes need only a signed-in caller: who may see or decide an approval is
reviewer visibility (the agent's owner or ``org:admin``), and who may withdraw
one is the identity that filed the hold, both applied by the service, not a
scope.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from jentic_one.admin.services.execution_approval_service import ExecutionApprovalService
from jentic_one.admin.services.schemas.execution_approvals import (
    DecideInput,
    ExecutionApprovalDetailView,
    ExecutionApprovalView,
)
from jentic_one.admin.web.deps import get_execution_approval_service
from jentic_one.admin.web.schemas.execution_approvals import (
    DecideRequest,
    ExecutionApprovalDetailResponse,
    ExecutionApprovalLinksResponse,
    ExecutionApprovalListResponse,
    ExecutionApprovalResponse,
    HeldRequestResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models.execution_approvals import ExecutionApprovalState
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.links import build_link
from jentic_one.shared.web.openapi_responses import conflict, not_found, with_responses

router = APIRouter()


def _fields(view: ExecutionApprovalView, request: Request) -> dict[str, object]:
    return {
        **ExecutionApprovalView.model_validate(view).model_dump(),
        "links": ExecutionApprovalLinksResponse(
            self_link=build_link(request, f"/executions/approvals/{view.id}"),
            job=build_link(request, f"/jobs/{view.job_id}"),
        ),
    }


def _approval_response(view: ExecutionApprovalView, request: Request) -> ExecutionApprovalResponse:
    """Project an approval view to its API response."""
    return ExecutionApprovalResponse.model_validate(_fields(view, request))


def _detail_response(
    view: ExecutionApprovalDetailView, request: Request
) -> ExecutionApprovalDetailResponse:
    """Project an approval detail view to its API response."""
    held = view.request
    return ExecutionApprovalDetailResponse.model_validate(
        {
            **_fields(view, request),
            "agent_name": view.agent_name,
            "agent_owner_id": view.agent_owner_id,
            "request": (
                HeldRequestResponse.model_validate(held.model_dump()) if held is not None else None
            ),
        }
    )


@router.get("/executions/approvals", summary="List execution approvals")
async def list_execution_approvals(
    request: Request,
    identity: Identity = get_current_identity(),
    svc: ExecutionApprovalService = Depends(get_execution_approval_service),
    state: ExecutionApprovalState | None = None,
    agent_id: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> ExecutionApprovalListResponse:
    """List the approvals the caller may review, newest first.

    ``org:admin`` sees every approval; a user sees approvals for agents they
    own; an agent sees its own. Filter by ``state`` (e.g. ``pending``) or
    ``agent_id``.
    """
    page = await svc.list_all(
        identity=identity, state=state, agent_id=agent_id, cursor=cursor, limit=limit
    )
    return ExecutionApprovalListResponse(
        data=[_approval_response(v, request) for v in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


@router.get(
    "/executions/approvals/{approval_id}",
    summary="Get an execution approval",
    responses=with_responses(not_found()),
)
async def get_execution_approval(
    approval_id: str,
    request: Request,
    identity: Identity = get_current_identity(),
    svc: ExecutionApprovalService = Depends(get_execution_approval_service),
) -> ExecutionApprovalDetailResponse:
    """One approval with its agent, owner, matched rule and the held request body.

    An approval outside the caller's reviewer visibility answers ``404``.
    """
    view = await svc.get(approval_id, identity=identity)
    return _detail_response(view, request)


@router.post(
    "/executions/approvals/{approval_id}:decide",
    summary="Approve or deny an execution approval",
    responses=with_responses(not_found(), conflict()),
)
async def decide_execution_approval(
    approval_id: str,
    body: DecideRequest,
    request: Request,
    identity: Identity = get_current_identity(),
    svc: ExecutionApprovalService = Depends(get_execution_approval_service),
) -> ExecutionApprovalResponse:
    """Decide a pending approval — the agent's owner or an ``org:admin`` only.

    ``approve`` releases the held job to the worker, which re-authorizes and
    runs it once; ``deny`` fails the job with a permission-denied result. The
    first decision wins: deciding an approval that is no longer pending (or
    has expired) answers ``409``. An agent caller is always refused (``403``),
    whatever its scopes.
    """
    view = await svc.decide(
        approval_id,
        DecideInput(decision=body.decision, reason=body.reason),
        identity=identity,
    )
    return _approval_response(view, request)


@router.post(
    "/executions/approvals/{approval_id}:withdraw",
    summary="Withdraw a held execution",
    responses=with_responses(not_found(), conflict()),
)
async def withdraw_execution_approval(
    approval_id: str,
    request: Request,
    identity: Identity = get_current_identity(),
    svc: ExecutionApprovalService = Depends(get_execution_approval_service),
) -> ExecutionApprovalResponse:
    """Abandon a held execution — the identity that filed the hold only.

    The approval becomes ``withdrawn`` and its held job ``cancelled``; the call
    never runs and no result is written. Any other caller gets ``404``, as if
    the approval did not exist; an approval that is no longer pending (decided,
    expired or already withdrawn) answers ``409``. It acts only on held
    executions: owners and admins use ``:decide`` with ``deny`` instead.
    """
    view = await svc.withdraw(approval_id, identity=identity)
    return _approval_response(view, request)
