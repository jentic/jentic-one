"""Executions router — read-only listing."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from jentic.problem_details import ValidationError as ProblemValidationError

from jentic_one.admin.services.execution_service import ExecutionService
from jentic_one.admin.services.schemas.executions import ExecutionFilter, ExecutionView
from jentic_one.admin.web.deps import get_execution_service
from jentic_one.admin.web.schemas.executions import (
    ApiInfoResponse,
    ExecutionListResponse,
    ExecutionRecordLinks,
    ExecutionResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.links import build_link

router = APIRouter()

_TERMINAL_STATUSES = {s.value for s in ExecutionStatus}


def _execution_response(view: ExecutionView, request: Request) -> ExecutionResponse:
    """Project an ExecutionView to an ExecutionResponse."""
    api = None
    if view.api is not None:
        api = ApiInfoResponse(
            vendor=view.api.vendor,
            name=view.api.name,
            version=view.api.version,
            host=view.api.host,
        )
    links = ExecutionRecordLinks(self_link=build_link(request, f"/executions/{view.id}"))
    return ExecutionResponse(
        execution_id=view.id,
        toolkit_id=view.toolkit_id,
        toolkit_name=view.toolkit_name,
        trace_id=view.trace_id,
        started_at=view.started_at,
        duration_ms=view.duration_ms,
        status=view.status,
        operation_id=view.operation_id,
        api=api,
        pinned_revisions=view.pinned_revisions,
        http_status=view.http_status,
        error=view.error,
        created_at=view.created_at,
        actor_id=view.actor_id,
        actor_type=view.actor_type,
        origin=view.origin,
        credential_id=view.credential_id,
        credential_name=view.credential_name,
        links=links,
    )


def _parse_api_filter(api: str) -> tuple[str | None, str | None, str | None]:
    """Parse colon-encoded api filter (vendor[:name[:version]])."""
    parts = api.split(":")
    vendor = parts[0] if len(parts) >= 1 and parts[0] else None
    name = parts[1] if len(parts) >= 2 and parts[1] else None
    version = parts[2] if len(parts) >= 3 and parts[2] else None
    return vendor, name, version


@router.get("/executions")
async def list_executions(
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["executions:read"]),
    exec_svc: ExecutionService = Depends(get_execution_service),
    toolkit_id: str | None = None,
    trace_id: str | None = None,
    status: list[str] | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    api: str | None = None,
    actor_id: str | None = None,
    origin: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> ExecutionListResponse:
    """List execution records with optional filters."""
    if status is not None:
        invalid = [s for s in status if s not in _TERMINAL_STATUSES]
        if invalid:
            raise ProblemValidationError(
                detail=f"Only terminal statuses allowed: {sorted(_TERMINAL_STATUSES)}",
                instance=str(request.url.path),
                type="invalid_status_filter",
            )

    api_vendor: str | None = None
    api_name: str | None = None
    api_version: str | None = None
    if api is not None:
        api_vendor, api_name, api_version = _parse_api_filter(api)

    page = await exec_svc.list_all(
        filter=ExecutionFilter(
            toolkit_id=toolkit_id,
            trace_id=trace_id,
            status=status,
            from_=from_,
            to=to,
            api_vendor=api_vendor,
            api_name=api_name,
            api_version=api_version,
            actor_id=actor_id,
            origin=origin,
        ),
        cursor=cursor,
        limit=limit,
    )
    return ExecutionListResponse(
        data=[_execution_response(e, request) for e in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


@router.get("/executions/{execution_id}")
async def get_execution(
    execution_id: str,
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["executions:read"]),
    exec_svc: ExecutionService = Depends(get_execution_service),
) -> ExecutionResponse:
    """Get an execution record by ID."""
    view = await exec_svc.get_by_id(execution_id)
    return _execution_response(view, request)
