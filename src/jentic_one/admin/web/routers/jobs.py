"""Jobs router — list, get, result, and cancel."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from jentic_one.admin.services.job_result_service import JobResultService
from jentic_one.admin.services.job_service import JobService
from jentic_one.admin.services.schemas.jobs import JobFilter, JobView
from jentic_one.admin.web.deps import (
    get_job_result_service,
    get_job_service,
)
from jentic_one.admin.web.schemas.jobs import (
    JobLinksResponse,
    JobListResponse,
    JobResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import JobKind, JobStatus
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.links import build_link
from jentic_one.shared.web.openapi_responses import conflict, not_found, with_responses

router = APIRouter()


def _job_response(view: JobView, request: Request) -> JobResponse:
    """Project a JobView to a JobResponse."""
    has_result = view.status == JobStatus.COMPLETED or (
        view.status == JobStatus.FAILED and view.kind == JobKind.EXECUTION
    )
    result_link = build_link(request, f"/jobs/{view.id}/result") if has_result else None
    execution_link = (
        build_link(request, f"/executions/{view.execution_id}") if view.execution_id else None
    )
    links = JobLinksResponse(
        self_link=build_link(request, f"/jobs/{view.id}"),
        result=result_link,
        execution=execution_link,
    )
    return JobResponse(
        job_id=view.id,
        kind=view.kind,
        status=view.status,
        execution_id=view.execution_id,
        error=view.error,
        created_at=view.created_at,
        updated_at=view.updated_at,
        links=links,
    )


@router.get("/jobs")
async def list_jobs(
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["jobs:read"]),
    job_svc: JobService = Depends(get_job_service),
    kind: str | None = None,
    job_status: list[str] | None = Query(default=None, alias="status"),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> JobListResponse:
    """List jobs with optional filters."""
    page = await job_svc.list_all(
        filter=JobFilter(kind=kind, status=job_status, from_=from_, to=to),
        identity=identity,
        cursor=cursor,
        limit=limit,
    )
    return JobListResponse(
        data=[_job_response(j, request) for j in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


@router.get("/jobs/{job_id}")
async def get_job(
    request: Request,
    job_id: str,
    identity: Identity = get_current_identity(required_permissions=["jobs:read"]),
    job_svc: JobService = Depends(get_job_service),
) -> JobResponse:
    """Get a job by ID."""
    view = await job_svc.get_by_id(job_id, identity=identity)
    return _job_response(view, request)


@router.get("/jobs/{job_id}/result")
async def get_job_result(
    job_id: str,
    identity: Identity = get_current_identity(required_permissions=["jobs:read"]),
    result_svc: JobResultService = Depends(get_job_result_service),
) -> Response:
    """Get the result of a completed job, or the problem body of a failed execution.

    Polymorphic by kind. A held execution that was denied or expired is
    ``failed`` with a permission-denied problem as its result.
    """
    view = await result_svc.get(job_id, identity=identity)
    if view.kind == JobKind.EXECUTION and view.content_type:
        return Response(content=view.raw_body, media_type=view.content_type)
    return JSONResponse(content=view.body)


@router.post("/jobs/{job_id}:cancel", responses=with_responses(not_found(), conflict()))
async def cancel_job(
    request: Request,
    job_id: str,
    identity: Identity = get_current_identity(required_permissions=["jobs:write"]),
    job_svc: JobService = Depends(get_job_service),
) -> JobResponse:
    """Cancel a queued, running or held job; an already-terminal job is returned unchanged.

    A job held by an execution approval answers ``409``: it settles only through
    that approval (a reviewer's decision, the filing agent's withdrawal, or expiry).
    """
    view = await job_svc.cancel(job_id, identity=identity)
    return _job_response(view, request)
