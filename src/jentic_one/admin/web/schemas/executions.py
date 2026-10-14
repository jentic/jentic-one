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

    vendor: str = Field(description="Vendor of the API that was called.")
    name: str = Field(description="Name of the API that was called.")
    version: str = Field(description="Version of the API that was called.")
    host: str | None = Field(default=None, description="Upstream host called; null if unknown.")


class ExecutionRecordLinks(BaseModel):
    """HATEOAS links for an execution record."""

    self_link: str = Field(serialization_alias="self", description="URL of this execution record.")


class ExecutionResponse(BaseModel):
    """Execution record representation in API responses."""

    execution_id: str = Field(description="Unique ID of the execution record.")
    # Nullable-legacy (theme-5 Phase 2): null for direct-binding executions —
    # attribute those via credential_id/credential_name instead.
    toolkit_id: str | None = Field(
        default=None,
        description=(
            "Legacy: toolkit that mediated the execution. Null for direct-binding "
            "executions; attribute those via `credential_id` instead."
        ),
    )
    toolkit_name: str | None = Field(
        default=None,
        description="Legacy: name of the toolkit in `toolkit_id`; null when unavailable.",
    )
    trace_id: str = Field(description="Trace ID of the execution request.")
    started_at: datetime = Field(description="When the execution started (RFC 3339).")
    duration_ms: int | None = Field(
        default=None, description="Duration of the execution in milliseconds; null if unknown."
    )
    status: str = Field(
        description=(
            "Terminal outcome: `completed` or `failed`. An upstream response with "
            "HTTP status 400 or above counts as `failed`."
        )
    )
    operation_id: str | None = Field(
        default=None,
        description="Machine identifier of the operation called; null when none was resolved.",
    )
    # Human-readable operation identity. Field descriptions land in the
    # generated OpenAPI/TS contract, so external clients see the semantics too.
    operation_path: str | None = Field(
        default=None,
        description=(
            "The operation's spec path template, e.g. /repos/{owner}/{repo}. "
            "Null when the record carries no human-readable operation identity: "
            "records predating the column, executions of a URL that resolved to "
            "no registered operation, and async jobs enqueued with only an "
            "operation_id. Display surfaces show a placeholder for such rows — "
            "the opaque operation_id is a machine key, not a human fallback."
        ),
    )
    operation_method: str | None = Field(
        default=None,
        description=(
            "The operation's HTTP method, e.g. GET. Null whenever operation_path is null."
        ),
    )
    api: ApiInfoResponse | None = Field(
        default=None, description="The API that was called; null when it is not recorded."
    )
    pinned_revisions: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Pinned API revisions used, keyed `vendor:name:version` with the revision ID "
            "as value; null when no pin applied."
        ),
    )
    http_status: int | None = Field(
        default=None,
        description="HTTP status returned by the upstream; null if no upstream response.",
    )
    error: str | None = Field(
        default=None,
        description="Short error message (at most 128 characters) for a failed execution.",
    )
    created_at: datetime = Field(description="When the execution record was created (RFC 3339).")
    actor_id: str = Field(description="ID of the actor that ran the execution.")
    actor_type: str = Field(
        description=(
            "Kind of actor that ran the execution, e.g. `user` or `agent`. Opaque string: "
            "historical rows may carry a retired type."
        )
    )
    origin: str | None = Field(
        default=None,
        description=(
            "Surface the execution was initiated from, e.g. `api`, `agent` or `mcp`; "
            "null if not recorded."
        ),
    )
    # Credential the broker used for this execution (#740). Both ``None`` for
    # executions using inline auth, credential-less APIs, historical rows, and
    # executions that failed before the resolver picked a credential.
    credential_id: str | None = Field(
        default=None,
        description=(
            "ID of the credential used. Null for inline auth, credential-less APIs, "
            "older records and executions that failed before a credential was chosen."
        ),
    )
    credential_name: str | None = Field(
        default=None,
        description="Name of the credential used; null when no credential is recorded.",
    )
    links: ExecutionRecordLinks = Field(serialization_alias="_links")


class ExecutionListResponse(BaseModel):
    """Paginated list of executions."""

    data: list[ExecutionResponse] = Field(description="The page of execution records.")
    has_more: bool = Field(description="Whether more results exist after this page.")
    next_cursor: str | None = Field(
        default=None, description="Cursor for the next page; null when there are no more results."
    )
