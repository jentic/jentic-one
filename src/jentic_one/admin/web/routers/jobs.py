"""Jobs router — list, get, result, and cancel."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
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
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.links import build_link
from jentic_one.shared.web.openapi_responses import conflict, gone, not_found, with_responses

router = APIRouter()


def _job_response(view: JobView, request: Request) -> JobResponse:
    """Project a JobView to a JobResponse."""
    result_link = (
        build_link(request, f"/jobs/{view.id}/result")
        if view.status == JobStatus.COMPLETED
        else None
    )
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
        actor_id=view.actor_id,
        actor_type=view.actor_type,
        links=links,
    )


@router.get("/jobs")
async def list_jobs(
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["jobs:read"]),
    job_svc: JobService = Depends(get_job_service),
    kind: str | None = Query(
        default=None,
        description="Only return jobs of this kind, e.g. `import` or `execution`.",
        openapi_examples=ex.example("import"),
    ),
    job_status: list[str] | None = Query(
        default=None,
        alias="status",
        description="Only return jobs in one of these states; repeat the parameter for several.",
        openapi_examples=ex.example(["running"]),
    ),
    from_: datetime | None = Query(
        default=None,
        alias="from",
        description="Only return jobs created at or after this time (RFC 3339).",
        openapi_examples=ex.example(ex.DATETIME_FROM),
    ),
    to: datetime | None = Query(
        default=None,
        description="Only return jobs created before this time (RFC 3339).",
        openapi_examples=ex.example(ex.DATETIME_TO),
    ),
    cursor: str | None = Query(
        default=None,
        description="Opaque cursor from a previous page's `next_cursor`; omit for the first page.",
        openapi_examples=ex.example(ex.CURSOR),
    ),
    limit: int = Query(
        default=25,
        ge=1,
        le=100,
        description="Maximum number of jobs to return (1-100, default 25).",
        openapi_examples=ex.example(25),
    ),
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


@router.get("/jobs/{job_id}", responses=with_responses(not_found()))
async def get_job(
    request: Request,
    job_id: Annotated[
        str, Path(description="ID of the job.", openapi_examples=ex.example(ex.JOB_ID))
    ],
    identity: Identity = get_current_identity(required_permissions=["jobs:read"]),
    job_svc: JobService = Depends(get_job_service),
) -> JobResponse:
    """Get a job by ID."""
    view = await job_svc.get_by_id(job_id, identity=identity)
    return _job_response(view, request)


@router.get(
    "/jobs/{job_id}/result",
    responses=with_responses(not_found(), conflict(), gone()),
)
async def get_job_result(
    job_id: Annotated[
        str, Path(description="ID of the job.", openapi_examples=ex.example(ex.JOB_ID))
    ],
    identity: Identity = get_current_identity(required_permissions=["jobs:read"]),
    result_svc: JobResultService = Depends(get_job_result_service),
) -> Response:
    """Get the result of a completed job — polymorphic by kind.

    Answers ``409`` while the job has not completed, and ``410`` once an
    approved execution's result is past
    ``held_executions.result_retention_seconds``.
    """
    view = await result_svc.get(job_id, identity=identity)
    if view.kind == JobKind.EXECUTION and view.content_type:
        return Response(content=view.raw_body, media_type=view.content_type)
    return JSONResponse(content=view.body)


@router.post("/jobs/{job_id}:cancel", responses=with_responses(not_found(), conflict()))
async def cancel_job(
    request: Request,
    job_id: Annotated[
        str, Path(description="ID of the job.", openapi_examples=ex.example(ex.JOB_ID))
    ],
    identity: Identity = get_current_identity(required_permissions=["jobs:write"]),
    job_svc: JobService = Depends(get_job_service),
) -> JobResponse:
    """Cancel an active job.

    A held execution answers ``409``: it settles only through its hold (decide,
    withdraw or expiry), never by cancelling the job.
    """
    view = await job_svc.cancel(job_id, identity=identity)
    return _job_response(view, request)
