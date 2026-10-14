"""Audit entry request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditResponse(BaseModel):
    """Audit entry representation in API responses."""

    id: str = Field(description="Unique ID of the audit entry.")
    occurred_at: datetime = Field(description="When the audited action happened (RFC 3339).")
    action: str = Field(description="The audited action, e.g. `create`, `update` or `revoke`.")
    target_type: str = Field(description="Type of the entity the action was performed on.")
    target_id: str = Field(description="ID of the entity the action was performed on.")
    target_parent_id: str | None = Field(
        default=None, description="ID of the target's parent entity; null when not applicable."
    )
    actor_type: str = Field(
        description="Kind of actor that performed the action, e.g. `user` or `agent`."
    )
    actor_id: str | None = Field(
        default=None, description="ID of the actor that performed the action; null if unknown."
    )
    actor_session_id: str | None = Field(
        default=None, description="Session the actor acted under; null if not recorded."
    )
    before: dict[str, Any] | None = Field(
        default=None, description="Snapshot of the target before the action; null if none."
    )
    after: dict[str, Any] | None = Field(
        default=None, description="Snapshot of the target after the action; null if none."
    )
    diff: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Changes between `before` and `after`, keyed `added`, `removed` and `modified` "
            "(each modified key maps to `old`/`new`); null when nothing changed."
        ),
    )
    request_id: str | None = Field(
        default=None, description="ID of the request that triggered the action, if recorded."
    )
    trace_id: str | None = Field(
        default=None, description="Trace ID of the triggering request, if recorded."
    )
    job_id: str | None = Field(
        default=None, description="ID of the job the action ran under, if any."
    )
    reason: str | None = Field(
        default=None, description="Reason recorded for the action; null if none was given."
    )
    ip_address: str | None = Field(
        default=None, description="Client IP address of the triggering request, if recorded."
    )
    user_agent: str | None = Field(
        default=None, description="User-Agent of the triggering request, if recorded."
    )
    origin: str | None = Field(
        default=None,
        description=(
            "Surface the action was initiated from, e.g. `dashboard`, `cli`, `api`, "
            "`agent`, `mcp` or `system`; null if not recorded."
        ),
    )


class AuditListResponse(BaseModel):
    """Paginated list of audit entries."""

    data: list[AuditResponse] = Field(description="The page of audit entries.")
    has_more: bool = Field(description="Whether more results exist after this page.")
    next_cursor: str | None = Field(
        default=None, description="Cursor for the next page; null when there are no more results."
    )
