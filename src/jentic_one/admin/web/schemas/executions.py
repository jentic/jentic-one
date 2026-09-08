"""Execution request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "ApiInfoResponse",
    "ExecutionListResponse",
    "ExecutionRecordLinks",
    "ExecutionResponse",
]


class ApiInfoResponse(BaseModel):
    """API identification information."""

    vendor: str
    name: str
    version: str
    host: str | None = None


class ExecutionRecordLinks(BaseModel):
    """HATEOAS links for an execution record."""

    self_link: str = Field(serialization_alias="self")


class ExecutionResponse(BaseModel):
    """Execution record representation in API responses."""

    execution_id: str
    toolkit_id: str
    toolkit_name: str | None = None
    trace_id: str
    started_at: datetime
    duration_ms: int | None = None
    status: str
    operation_id: str | None = None
    api: ApiInfoResponse | None = None
    pinned_revisions: dict[str, Any] | None = None
    http_status: int | None = None
    error: str | None = None
    created_at: datetime
    actor_id: str
    actor_type: str
    origin: str | None = None
    # Credential the broker used for this execution (#740). Both ``None`` for
    # executions using inline auth, credential-less APIs, historical rows, and
    # executions that failed before the resolver picked a credential.
    credential_id: str | None = None
    credential_name: str | None = None
    links: ExecutionRecordLinks = Field(serialization_alias="_links")


class ExecutionListResponse(BaseModel):
    """Paginated list of executions."""

    data: list[ExecutionResponse]
    has_more: bool
    next_cursor: str | None = None
