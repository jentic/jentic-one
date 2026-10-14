"""Job request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class JobLinksResponse(BaseModel):
    """Hypermedia links for a job."""

    self_link: str = Field(serialization_alias="self")
    result: str | None = None
    execution: str | None = None


class JobResponse(BaseModel):
    """Job representation in API responses."""

    job_id: str
    kind: str
    status: str
    execution_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
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

    data: list[JobResponse]
    has_more: bool
    next_cursor: str | None = None
