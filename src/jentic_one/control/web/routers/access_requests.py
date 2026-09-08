"""Access requests router — file, list, get, decide, amend, withdraw."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from jentic_one.control.services.access_requests.schemas.access_requests import (
    AccessRequestItemView,
    AccessRequestView,
    Evaluation,
)
from jentic_one.control.services.access_requests.service import AccessRequestService
from jentic_one.control.web.deps import get_access_request_service
from jentic_one.control.web.schemas.access_requests import (
    AccessRequestFileRequest,
    AccessRequestItemResponse,
    AccessRequestListResponse,
    AccessRequestOwnerResponse,
    AccessRequestResponse,
    AmendRequest,
    DecideRequest,
    DuplicatePendingProblem,
    EvaluationCheckResponse,
    EvaluationResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.openapi_responses import not_found

router = APIRouter()


def _to_item_response(item: AccessRequestItemView) -> AccessRequestItemResponse:
    return AccessRequestItemResponse(
        id=item.id,
        resource_type=item.resource_type,
        action=item.action,
        resource_id=item.resource_id,
        resource_reference=item.resource_reference,
        to_type=item.to_type,
        to_id=item.to_id,
        toolkit_name=item.toolkit_name,
        credential_name=item.credential_name,
        rules=item.rules,
        status=item.status,
        applied_effects=item.applied_effects,
        decided_by=item.decided_by,
        decided_at=item.decided_at,
        decision_reason=item.decision_reason,
        already_satisfied=item.already_satisfied,
        already_satisfied_by=item.already_satisfied_by,
    )


def _to_evaluation_response(evaluation: Evaluation) -> EvaluationResponse:
    return EvaluationResponse(
        can_fulfill=evaluation.can_fulfill,
        checks=[
            EvaluationCheckResponse(check=c.check, passed=c.passed, blocker=c.blocker)
            for c in evaluation.checks
        ],
    )


def _to_response(view: AccessRequestView) -> AccessRequestResponse:
    evaluation = None
    if view.evaluation is not None:
        evaluation = _to_evaluation_response(view.evaluation)
    filer_owner = None
    if view.filer_owner is not None:
        filer_owner = AccessRequestOwnerResponse(
            id=view.filer_owner.id,
            email=view.filer_owner.email,
            display_name=view.filer_owner.display_name,
        )
    return AccessRequestResponse(
        id=view.id,
        actor_id=view.actor_id,
        reason=view.reason,
        requested_by=view.requested_by,
        status=view.status,
        approve_url=view.approve_url,
        filed_at=view.filed_at,
        expires_at=view.expires_at,
        created_by=view.created_by,
        filer_owner_id=view.filer_owner_id,
        filer_owner=filer_owner,
        items=[_to_item_response(i) for i in view.items],
        evaluation=evaluation,
    )


@router.post(
    "/access-requests",
    status_code=202,
    summary="File access request",
    responses={
        409: {
            "description": "A pending request already exists for the same resource.",
            "model": DuplicatePendingProblem,
            "content": {
                "application/problem+json": {
                    "schema": {"$ref": "#/components/schemas/DuplicatePendingProblem"}
                }
            },
        }
    },
)
async def file_access_request(
    body: AccessRequestFileRequest,
    identity: Identity = get_current_identity(),
    svc: AccessRequestService = Depends(get_access_request_service),
) -> AccessRequestResponse:
    """File a new access request."""
    items = [item.model_dump(exclude_none=True) for item in body.items]
    view = await svc.file(
        actor_id=identity.sub,
        reason=body.reason,
        items=items,
        identity=identity,
    )
    return _to_response(view)


@router.get("/access-requests", summary="List access requests")
async def list_access_requests(
    identity: Identity = get_current_identity(),
    svc: AccessRequestService = Depends(get_access_request_service),
    actor_id: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> AccessRequestListResponse:
    """List access requests with cursor-based pagination."""
    page = await svc.list_all(
        identity=identity,
        actor_id=actor_id,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    return AccessRequestListResponse(
        data=[_to_response(v) for v in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


@router.get("/access-requests/{request_id}", summary="Get access request", responses=not_found())
async def get_access_request(
    request_id: str,
    identity: Identity = get_current_identity(),
    svc: AccessRequestService = Depends(get_access_request_service),
) -> AccessRequestResponse:
    """Get a single access request by ID."""
    view = await svc.get(request_id, identity=identity)
    return _to_response(view)


@router.post(
    "/access-requests/{request_id}:amend",
    summary="Amend access request",
    responses=not_found(),
)
async def amend_access_request(
    request_id: str,
    body: AmendRequest,
    identity: Identity = get_current_identity(),
    svc: AccessRequestService = Depends(get_access_request_service),
) -> AccessRequestResponse:
    """Amend pending items on an access request."""
    item_amendments = [item.model_dump(exclude_none=True) for item in body.items]
    view = await svc.amend(
        request_id,
        identity=identity,
        item_amendments=item_amendments,
    )
    return _to_response(view)


@router.post(
    "/access-requests/{request_id}:decide",
    summary="Decide access request items",
    responses=not_found(),
)
async def decide_access_request(
    request_id: str,
    body: DecideRequest,
    identity: Identity = get_current_identity(),
    svc: AccessRequestService = Depends(get_access_request_service),
) -> AccessRequestResponse:
    """Decide (approve/deny) items on an access request."""
    item_decisions = [item.model_dump(exclude_none=True) for item in body.items]
    view = await svc.decide(
        request_id,
        identity=identity,
        item_decisions=item_decisions,
    )
    return _to_response(view)


@router.post(
    "/access-requests/{request_id}:withdraw",
    summary="Withdraw access request",
    responses=not_found(),
)
async def withdraw_access_request(
    request_id: str,
    identity: Identity = get_current_identity(),
    svc: AccessRequestService = Depends(get_access_request_service),
) -> AccessRequestResponse:
    """Withdraw a pending access request."""
    view = await svc.withdraw(request_id, identity=identity)
    return _to_response(view)
