"""Job request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class JobLinksResponse(BaseModel):
    """Hypermedia links for a job."""

    self_link: str = Field(serialization_alias="self", description="URL of this job.")
    result: str | None = Field(
        default=None, description="URL of the job result; null until the job has completed."
    )
    execution: str | None = Field(
        default=None, description="URL of the related execution record; null if none."
    )


class JobResponse(BaseModel):
    """Job representation in API responses."""

    job_id: str = Field(description="Unique ID of the job.")
    kind: str = Field(description="Kind of job, e.g. `import` or `execution`.")
    status: str = Field(
        description=(
            "Lifecycle state: `queued`, `running`, `completed`, `failed`, `cancelled` "
            "or `dead_letter`."
        )
    )
    execution_id: str | None = Field(
        default=None, description="ID of the execution record linked to the job, if any."
    )
    error: str | None = Field(
        default=None, description="Error message recorded for the job; null if none."
    )
    created_at: datetime = Field(description="When the job was created (RFC 3339).")
    updated_at: datetime | None = Field(
        default=None,
        description="When the job was last updated (RFC 3339); null if never updated.",
    )
    actor_id: str | None = Field(
        default=None,
        description="Subject that enqueued the job. Null only for legacy rows with no actor.",
    )
    actor_type: str = Field(
        description=(
            "Kind of actor that enqueued the job: 'user' or 'agent'. Opaque string: "
            "historical rows may carry a retired type such as 'service_account'."
        )
    )
    links: JobLinksResponse = Field(serialization_alias="_links")


class ApiImportRevisionResult(BaseModel):
    """A single revision produced by an import job."""

    api: dict[str, Any]
    revision_id: str
    state: str


class ApiImportJobResult(BaseModel):
    """Result body for a completed kind=import job."""

    revisions: list[ApiImportRevisionResult]


class JobListResponse(BaseModel):
    """Paginated list of jobs."""

    data: list[JobResponse] = Field(description="The page of jobs.")
    has_more: bool = Field(description="Whether more results exist after this page.")
    next_cursor: str | None = Field(
        default=None, description="Cursor for the next page; null when there are no more results."
    )
