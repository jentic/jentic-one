"""Held executions router — follow, decide, withdraw and collect calls held for approval.

A held execution is a call a require-approval rule parked for a human. The
routes take no ``jobs:*`` permission: who may see one is the service's
visibility rule (the filing agent, the agent's owner, or ``org:admin``), who
may decide one is that same rule minus every agent caller, and who may
withdraw one is the agent that filed it. Jobs are how a held call runs, never
part of this surface: every read goes through the held execution itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from jentic_one.admin.services.execution_approval_service import ExecutionApprovalService
from jentic_one.admin.services.schemas.execution_approvals import (
    DecideInput,
    ExecutionApprovalDetailView,
    ExecutionApprovalView,
)
from jentic_one.admin.web.deps import get_execution_approval_service
from jentic_one.admin.web.schemas.held_executions import (
    DecideHeldExecutionRequest,
    HeldExecutionDetailResponse,
    HeldExecutionLinksResponse,
    HeldExecutionListResponse,
    HeldExecutionResponse,
    HeldRequestResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.jobs.hold import PROBLEM_CONTENT_TYPE
from jentic_one.shared.models.execution_approvals import ExecutionApprovalState
from jentic_one.shared.models.jobs import JobStatus
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.links import build_link
from jentic_one.shared.web.openapi_responses import conflict, not_found, with_responses

router = APIRouter()

#: Execution statuses with a result to collect.
_FINISHED = frozenset({JobStatus.COMPLETED.value, JobStatus.FAILED.value})


def _fields(view: ExecutionApprovalView, request: Request) -> dict[str, object]:
    base = f"/executions/holds/{view.id}"
    return {
        **view.model_dump(exclude={"job_id"}),
        "links": HeldExecutionLinksResponse(
            self_link=build_link(request, base),
            result=(
                build_link(request, f"{base}/result")
                if view.execution_status in _FINISHED
                else None
            ),
            withdraw=(
                build_link(request, f"{base}:withdraw")
                if view.state == ExecutionApprovalState.PENDING.value
                else None
            ),
            execution=(
                build_link(request, f"/executions/{view.execution_id}")
                if view.execution_id
                else None
            ),
        ),
    }


def _response(view: ExecutionApprovalView, request: Request) -> HeldExecutionResponse:
    """Project a held execution view to its API response."""
    return HeldExecutionResponse.model_validate(_fields(view, request))


def _detail_response(
    view: ExecutionApprovalDetailView, request: Request
) -> HeldExecutionDetailResponse:
    """Project a held execution detail view to its API response."""
    held = view.request
    return HeldExecutionDetailResponse.model_validate(
        {
            **_fields(view, request),
            "agent_name": view.agent_name,
            "agent_owner_id": view.agent_owner_id,
            "request": (
                HeldRequestResponse.model_validate(held.model_dump()) if held is not None else None
            ),
        }
    )


@router.get("/executions/holds", summary="List held executions")
async def list_held_executions(
    request: Request,
    identity: Identity = get_current_identity(),
    svc: ExecutionApprovalService = Depends(get_execution_approval_service),
    state: ExecutionApprovalState | None = None,
    agent_id: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> HeldExecutionListResponse:
    """List the held executions the caller may see, newest first.

    ``org:admin`` sees every one; a user sees those filed by agents they own;
    an agent sees its own. Filter by ``state`` (e.g. ``pending``) or ``agent_id``.
    """
    page = await svc.list_all(
        identity=identity, state=state, agent_id=agent_id, cursor=cursor, limit=limit
    )
    return HeldExecutionListResponse(
        data=[_response(v, request) for v in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


@router.get(
    "/executions/holds/{held_execution_id}",
    summary="Get a held execution",
    responses=with_responses(not_found()),
)
async def get_held_execution(
    held_execution_id: str,
    request: Request,
    identity: Identity = get_current_identity(),
    svc: ExecutionApprovalService = Depends(get_execution_approval_service),
) -> HeldExecutionDetailResponse:
    """One held execution: its decision, where the call stands, and the held request.

    ``execution_status`` follows the call from ``held`` to its outcome; once it
    has finished, ``_links.result`` returns that outcome. A held execution
    outside the caller's visibility answers ``404``.
    """
    view = await svc.get(held_execution_id, identity=identity)
    return _detail_response(view, request)


@router.get(
    "/executions/holds/{held_execution_id}/result",
    summary="Get a held execution's result",
    responses=with_responses(not_found(), conflict()),
)
async def get_held_execution_result(
    held_execution_id: str,
    identity: Identity = get_current_identity(),
    svc: ExecutionApprovalService = Depends(get_execution_approval_service),
) -> Response:
    """The finished call's outcome: the upstream response, or why it never ran.

    A call that ran returns its execution document — ``execution_id``,
    ``status``, ``http_status``, ``duration_ms`` and the upstream body as
    ``body_b64`` — as JSON. A call that never ran (denied, expired, or refused
    at run time) returns the ``application/problem+json`` body explaining why.
    Before the call has finished this answers ``409``; past its retention
    window, ``410``.
    """
    view = await svc.get_result(held_execution_id, identity=identity)
    if view.content_type == PROBLEM_CONTENT_TYPE:
        return Response(content=view.raw_body, media_type=PROBLEM_CONTENT_TYPE)
    return JSONResponse(content=view.body)


@router.post(
    "/executions/holds/{held_execution_id}:decide",
    summary="Approve or deny a held execution",
    responses=with_responses(not_found(), conflict()),
)
async def decide_held_execution(
    held_execution_id: str,
    body: DecideHeldExecutionRequest,
    request: Request,
    identity: Identity = get_current_identity(),
    svc: ExecutionApprovalService = Depends(get_execution_approval_service),
) -> HeldExecutionResponse:
    """Decide a pending held execution — the agent's owner or an ``org:admin`` only.

    ``approve`` releases the call, which is re-authorized and run once; ``deny``
    fails it with a permission-denied result. The first decision wins: deciding
    one that is no longer pending (or has expired) answers ``409``. An agent
    caller is always refused (``403``), whatever its permissions.
    """
    view = await svc.decide(
        held_execution_id,
        DecideInput(decision=body.decision, reason=body.reason),
        identity=identity,
    )
    return _response(view, request)


@router.post(
    "/executions/holds/{held_execution_id}:withdraw",
    summary="Withdraw a held execution",
    responses=with_responses(not_found(), conflict()),
)
async def withdraw_held_execution(
    held_execution_id: str,
    request: Request,
    identity: Identity = get_current_identity(),
    svc: ExecutionApprovalService = Depends(get_execution_approval_service),
) -> HeldExecutionResponse:
    """Abandon a held execution — the agent that filed it only.

    The call becomes ``withdrawn`` and never runs; no result is written. Any
    other caller gets ``404``, as if it did not exist; one that is no longer
    pending (decided, expired or already withdrawn) answers ``409``. Owners and
    admins use ``:decide`` with ``deny`` instead.
    """
    view = await svc.withdraw(held_execution_id, identity=identity)
    return _response(view, request)
