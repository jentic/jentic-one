"""Job request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from jentic_one.shared.web import openapi_examples as ex


class JobLinksResponse(BaseModel):
    """Hypermedia links for a job."""

    self_link: str = Field(
        serialization_alias="self",
        description="URL of this job.",
        examples=[f"https://jentic.example.com/jobs/{ex.JOB_ID}"],
    )
    result: str | None = Field(
        default=None,
        description="URL of the job result; null until the job has completed.",
        examples=[f"https://jentic.example.com/jobs/{ex.JOB_ID}/result"],
    )
    execution: str | None = Field(
        default=None,
        description="URL of the related execution record; null if none.",
        examples=[f"https://jentic.example.com/executions/{ex.EXECUTION_ID}"],
    )


class JobResponse(BaseModel):
    """Job representation in API responses."""

    job_id: str = Field(description="Unique ID of the job.", examples=[ex.JOB_ID])
    kind: str = Field(
        description="Kind of job, e.g. `import` or `execution`.", examples=["execution"]
    )
    status: str = Field(
        description=(
            "Lifecycle state: `queued`, `running`, `completed`, `failed`, `cancelled` "
            "or `dead_letter`."
        ),
        examples=["completed"],
    )
    execution_id: str | None = Field(
        default=None,
        description="ID of the execution record linked to the job, if any.",
        examples=[ex.EXECUTION_ID],
    )
    error: str | None = Field(
        default=None,
        description="Error message recorded for the job; null if none.",
        examples=["Upstream returned HTTP 502 Bad Gateway"],
    )
    created_at: datetime = Field(
        description="When the job was created (RFC 3339).", examples=["2026-01-15T09:30:00Z"]
    )
    updated_at: datetime | None = Field(
        default=None,
        description="When the job was last updated (RFC 3339); null if never updated.",
        examples=["2026-01-15T09:30:05Z"],
    )
    actor_id: str | None = Field(
        default=None,
        description="Subject that enqueued the job. Null only for legacy rows with no actor.",
        examples=[ex.AGENT_ID],
    )
    actor_type: str = Field(
        description=(
            "Kind of actor that enqueued the job: 'user' or 'agent'. Opaque string: "
            "historical rows may carry a retired type such as 'service_account'."
        ),
        examples=["agent"],
    )
    links: JobLinksResponse = Field(serialization_alias="_links")


class ApiImportRevisionResult(BaseModel):
    """A single revision produced by an import job."""

    api: dict[str, Any]
    revision_id: str
    state: str
    promoted: bool | None = Field(
        default=None,
        description=(
            "Whether the revision is the one the API serves. False for a `draft`: "
            "unpinned Broker calls do not reach it until it is promoted. Absent on "
            "results written before this field existed."
        ),
        examples=[False],
    )
    promote_path: str | None = Field(
        default=None,
        description="Path that promotes the draft (`POST`); present only when `promoted` is false.",
        examples=[
            f"/apis/{ex.API_VENDOR}/{ex.API_NAME}/{ex.API_VERSION}/revisions/{ex.REVISION_ID}:promote"
        ],
    )
    promote_hint: str | None = Field(
        default=None,
        description="Human-readable note on how to promote; present only when `promoted` is false.",
    )


class ApiImportJobResult(BaseModel):
    """Result body for a completed kind=import job."""

    revisions: list[ApiImportRevisionResult]


class JobListResponse(BaseModel):
    """Paginated list of jobs."""

    data: list[JobResponse] = Field(description="The page of jobs.")
    has_more: bool = Field(
        description="Whether more results exist after this page.", examples=[True]
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the next page; null when there are no more results.",
        examples=[ex.CURSOR],
    )
