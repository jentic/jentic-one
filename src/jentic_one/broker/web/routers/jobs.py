"""Broker caller-scoped job polling and result routes (#1807)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import JSONResponse

from jentic_one.broker.services.jobs import BrokerJobView
from jentic_one.broker.web.deps import BrokerJobServiceDep, RequireJobAccess
from jentic_one.broker.web.schemas.jobs import JobLinksResponse, JobResponse
from jentic_one.shared.context import Context
from jentic_one.shared.models import JobKind
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.deps import get_ctx
from jentic_one.shared.web.links import build_link

router = APIRouter()


def _execution_record_link(ctx: Context, request: Request, execution_id: str) -> str:
    """Absolute URL of the control-plane execution record."""
    path = f"/executions/{execution_id}"
    admin_base = ctx.config.broker.jobs_api_base_url or ctx.config.server.public_base_url
    if admin_base:
        return f"{admin_base.rstrip('/')}{path}"
    return build_link(request, path)


def _job_response(view: BrokerJobView, request: Request, ctx: Context) -> JobResponse:
    """Project a BrokerJobView to a JobResponse with broker-rooted polling links."""
    result_link = build_link(request, f"/jobs/{view.id}/result") if view.result_available else None
    execution_link = (
        _execution_record_link(ctx, request, view.execution_id) if view.execution_id else None
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


@router.get(
    "/jobs/{job_id}",
    operation_id="brokerGetJob",
    summary="Get a job",
)
async def get_job(
    request: Request,
    job_id: Annotated[
        str, Path(description="ID of the job.", openapi_examples=ex.example(ex.JOB_ID))
    ],
    identity: RequireJobAccess,
    job_svc: BrokerJobServiceDep,
    ctx: Context = Depends(get_ctx),
) -> JobResponse:
    """Get a caller-owned execution job by ID."""
    view = await job_svc.get_by_id(job_id, identity=identity, instance=request.url.path)
    return _job_response(view, request, ctx)


@router.get(
    "/jobs/{job_id}/result",
    operation_id="brokerGetJobResult",
    summary="Get a job's result",
)
async def get_job_result(
    request: Request,
    job_id: Annotated[
        str, Path(description="ID of the job.", openapi_examples=ex.example(ex.JOB_ID))
    ],
    identity: RequireJobAccess,
    job_svc: BrokerJobServiceDep,
) -> Response:
    """Get the stored result of a finished caller-owned execution job."""
    view = await job_svc.get_result(job_id, identity=identity, instance=request.url.path)
    if view.kind == JobKind.EXECUTION and view.content_type:
        return Response(content=view.raw_body, media_type=view.content_type)
    return JSONResponse(content=view.body)
