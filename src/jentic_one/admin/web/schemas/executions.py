"""Execution request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from jentic_one.shared.web import openapi_examples as ex

__all__ = [
    "ApiInfoResponse",
    "ExecutionListResponse",
    "ExecutionRecordLinks",
    "ExecutionResponse",
]


class ApiInfoResponse(BaseModel):
    """API identification information."""

    vendor: str = Field(description="Vendor of the API that was called.", examples=[ex.API_VENDOR])
    name: str = Field(description="Name of the API that was called.", examples=[ex.API_NAME])
    version: str = Field(
        description="Version of the API that was called.", examples=[ex.API_VERSION]
    )
    host: str | None = Field(
        default=None,
        description="Upstream host called; null if unknown.",
        examples=["api.stripe.com"],
    )


class ExecutionRecordLinks(BaseModel):
    """HATEOAS links for an execution record."""

    self_link: str = Field(
        serialization_alias="self",
        description="URL of this execution record.",
        examples=[f"https://jentic.example.com/executions/{ex.EXECUTION_ID}"],
    )


class ExecutionResponse(BaseModel):
    """Execution record representation in API responses."""

    execution_id: str = Field(
        description="Unique ID of the execution record.", examples=[ex.EXECUTION_ID]
    )
    # Nullable-legacy (theme-5 Phase 2): null for direct-binding executions —
    # attribute those via credential_id/credential_name instead.
    toolkit_id: str | None = Field(
        default=None,
        description=(
            "Legacy: toolkit that mediated the execution. Null for direct-binding "
            "executions; attribute those via `credential_id` instead."
        ),
        examples=[ex.TOOLKIT_ID],
    )
    toolkit_name: str | None = Field(
        default=None,
        description="Legacy: name of the toolkit in `toolkit_id`; null when unavailable.",
        examples=["billing-toolkit"],
    )
    trace_id: str = Field(description="Trace ID of the execution request.", examples=[ex.TRACE_ID])
    started_at: datetime = Field(
        description="When the execution started (RFC 3339).", examples=["2026-01-15T09:30:00Z"]
    )
    duration_ms: int | None = Field(
        default=None,
        description="Duration of the execution in milliseconds; null if unknown.",
        examples=[342],
    )
    status: str = Field(
        description=(
            "Terminal outcome: `completed` or `failed`. An upstream response with "
            "HTTP status 400 or above counts as `failed`."
        ),
        examples=["completed"],
    )
    operation_id: str | None = Field(
        default=None,
        description="Machine identifier of the operation called; null when none was resolved.",
        examples=[ex.OPERATION_ID],
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
        examples=["/v1/charges"],
    )
    operation_method: str | None = Field(
        default=None,
        description=(
            "The operation's HTTP method, e.g. GET. Null whenever operation_path is null."
        ),
        examples=["GET"],
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
        examples=[{ex.API_REF: ex.REVISION_ID}],
    )
    http_status: int | None = Field(
        default=None,
        description="HTTP status returned by the upstream; null if no upstream response.",
        examples=[200],
    )
    error: str | None = Field(
        default=None,
        description="Short error message (at most 128 characters) for a failed execution.",
        examples=["Upstream returned HTTP 502 Bad Gateway"],
    )
    created_at: datetime = Field(
        description="When the execution record was created (RFC 3339).",
        examples=["2026-01-15T09:30:00Z"],
    )
    actor_id: str = Field(
        description="ID of the actor that ran the execution.", examples=[ex.AGENT_ID]
    )
    actor_type: str = Field(
        description=(
            "Kind of actor that ran the execution, e.g. `user` or `agent`. Opaque string: "
            "historical rows may carry a retired type."
        ),
        examples=["agent"],
    )
    origin: str | None = Field(
        default=None,
        description=(
            "Surface the execution was initiated from, e.g. `api`, `agent` or `mcp`; "
            "null if not recorded."
        ),
        examples=["agent"],
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
        examples=[ex.CREDENTIAL_ID],
    )
    credential_name: str | None = Field(
        default=None,
        description="Name of the credential used; null when no credential is recorded.",
        examples=["Stripe production key"],
    )
    links: ExecutionRecordLinks = Field(serialization_alias="_links")


class ExecutionListResponse(BaseModel):
    """Paginated list of executions."""

    data: list[ExecutionResponse] = Field(description="The page of execution records.")
    has_more: bool = Field(
        description="Whether more results exist after this page.", examples=[True]
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the next page; null when there are no more results.",
        examples=[ex.CURSOR],
    )
