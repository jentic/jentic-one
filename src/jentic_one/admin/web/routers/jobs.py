"""Jobs router — list, get, result, and cancel."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Final

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
from jentic_one.shared.web.openapi_responses import (
    PROBLEM_JSON,
    conflict,
    not_found,
    with_responses,
)

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


@router.get("/jobs", summary="List jobs")
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


@router.get(
    "/jobs/{job_id}",
    summary="Get a job",
    responses=not_found(
        "No job with this id is visible to the caller. A job belonging to another actor, "
        "or one whose retention window has expired, is indistinguishable from one that "
        "never existed."
    ),
)
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


#: A job result body is stored as JSON chosen by the handler that produced it,
#: so it has no single shape to model: the two kinds carry unrelated documents
#: and a held execution that never ran carries a problem body. The schema stays
#: an open object and the description carries the per-kind contract.
_JOB_RESULT_RESPONSE: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": (
            "The completed job's result, polymorphic by the job's `kind` (read `kind` from "
            "`GET /jobs/{job_id}`). The body is always a JSON object — never an array or a "
            "scalar — but its members depend on the kind:\n\n"
            "- **`kind=import`**: `revisions`, one entry per API revision the import "
            "produced, each with `api` (the API object), `revision_id` and `state`.\n"
            "- **`kind=execution`**: the execution document — `execution_id`, `status` "
            "(`completed` or `failed`), `http_status` (the upstream status, or null when the "
            "call never reached the upstream), `duration_ms`, and `body_b64` when the "
            "upstream returned a body. The upstream payload is base64 in `body_b64`; it is "
            "never inlined as JSON, so it needs decoding and interpreting against the "
            "upstream's own content type. A run-time authorization denial adds `problem`, "
            "the RFC 9457 body the synchronous execute route would have returned.\n"
            "- **A held execution that never ran** (denied, its approval window lapsed, or "
            "its approved run interrupted) carries an `application/problem+json` body in "
            "place of the execution document, with `type` `approval_denied`, "
            "`approval_expired`, `approval_required` or `approval_resume_failed`, plus "
            "`error_origin` and a `held_execution` object (`id`, `state`). The HTTP status "
            "stays `200`; the problem's own `status` member (403, or 409 for an interrupted "
            "approved run) says why the call never happened.\n\n"
            "`Content-Type` caveat: for a `kind=execution` result the header echoes the "
            "**upstream** response's content type, while the body is still the JSON "
            "execution document above — parse the body as JSON whatever the header says."
        ),
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "description": (
                        "The result document for the job's kind; see the response "
                        "description for the members of each."
                    ),
                },
                # One arm illustrated: a finished `kind=execution` job. A
                # `kind=import` job's result carries `revisions` instead.
                "example": {
                    "execution_id": ex.EXECUTION_ID,
                    "status": "completed",
                    "http_status": 200,
                    "duration_ms": 412,
                    "body_b64": "eyJpZCI6ICJyZV8xMjMifQ==",
                },
            },
            PROBLEM_JSON: {
                "schema": {"$ref": "#/components/schemas/ProblemDetail"},
                "example": {
                    "type": "approval_denied",
                    "title": "Execution approval denied",
                    "status": 403,
                    "detail": "A reviewer denied this held execution: Not this account",
                    "error_origin": "broker",
                    "held_execution": {"id": ex.HELD_EXECUTION_ID, "state": "denied"},
                },
            },
        },
    }
}


@router.get(
    "/jobs/{job_id}/result",
    summary="Get a job's result",
    responses={
        **_JOB_RESULT_RESPONSE,
        **not_found(
            "No job with this id is visible to the caller, or its retention window has "
            "expired and the result is gone. Both are indistinguishable from a job that "
            "never existed."
        ),
    },
)
async def get_job_result(
    job_id: Annotated[
        str, Path(description="ID of the job.", openapi_examples=ex.example(ex.JOB_ID))
    ],
    identity: Identity = get_current_identity(required_permissions=["jobs:read"]),
    result_svc: JobResultService = Depends(get_job_result_service),
) -> Response:
    """Get the result of a completed job — polymorphic by kind."""
    view = await result_svc.get(job_id, identity=identity)
    if view.kind == JobKind.EXECUTION and view.content_type:
        return Response(content=view.raw_body, media_type=view.content_type)
    return JSONResponse(content=view.body)


@router.post(
    "/jobs/{job_id}:cancel",
    summary="Cancel a job",
    responses=with_responses(not_found(), conflict()),
)
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
