"""Audit entry request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from jentic_one.shared.web import openapi_examples as ex


class AuditResponse(BaseModel):
    """Audit entry representation in API responses."""

    id: str = Field(description="Unique ID of the audit entry.", examples=[ex.AUDIT_ID])
    occurred_at: datetime = Field(
        description="When the audited action happened (RFC 3339).",
        examples=["2026-01-15T09:30:00Z"],
    )
    action: str = Field(
        description="The audited action, e.g. `create`, `update` or `revoke`.",
        examples=["update"],
    )
    target_type: str = Field(
        description="Type of the entity the action was performed on.", examples=["credential"]
    )
    target_id: str = Field(
        description="ID of the entity the action was performed on.", examples=[ex.CREDENTIAL_ID]
    )
    target_parent_id: str | None = Field(
        default=None,
        description="ID of the target's parent entity; null when not applicable.",
        examples=[ex.AGENT_ID],
    )
    actor_type: str = Field(
        description="Kind of actor that performed the action, e.g. `user` or `agent`.",
        examples=["user"],
    )
    actor_id: str | None = Field(
        default=None,
        description="ID of the actor that performed the action; null if unknown.",
        examples=[ex.USER_ID],
    )
    actor_session_id: str | None = Field(
        default=None,
        description="Session the actor acted under; null if not recorded.",
        examples=["5f0c2a9e-3b7d-4c1a-9e8f-2d6b4a1c7e30"],
    )
    before: dict[str, Any] | None = Field(
        default=None,
        description="Snapshot of the target before the action; null if none.",
        examples=[{"name": "Stripe test key", "active": True}],
    )
    after: dict[str, Any] | None = Field(
        default=None,
        description="Snapshot of the target after the action; null if none.",
        examples=[{"name": "Stripe production key", "active": True}],
    )
    diff: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Changes between `before` and `after`, keyed `added`, `removed` and `modified` "
            "(each modified key maps to `old`/`new`); null when nothing changed."
        ),
        examples=[
            {
                "added": {},
                "removed": {},
                "modified": {"name": {"old": "Stripe test key", "new": "Stripe production key"}},
            }
        ],
    )
    request_id: str | None = Field(
        default=None,
        description="ID of the request that triggered the action, if recorded.",
        examples=[ex.REQUEST_ID],
    )
    trace_id: str | None = Field(
        default=None,
        description="Trace ID of the triggering request, if recorded.",
        examples=[ex.TRACE_ID],
    )
    job_id: str | None = Field(
        default=None,
        description="ID of the job the action ran under, if any.",
        examples=[ex.JOB_ID],
    )
    reason: str | None = Field(
        default=None,
        description="Reason recorded for the action; null if none was given.",
        examples=["Rotated to the production key"],
    )
    ip_address: str | None = Field(
        default=None,
        description="Client IP address of the triggering request, if recorded.",
        examples=["203.0.113.42"],
    )
    user_agent: str | None = Field(
        default=None,
        description="User-Agent of the triggering request, if recorded.",
        examples=["Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15"],
    )
    origin: str | None = Field(
        default=None,
        description=(
            "Surface the action was initiated from, e.g. `dashboard`, `cli`, `api`, "
            "`agent`, `mcp` or `system`; null if not recorded."
        ),
        examples=["dashboard"],
    )


class AuditListResponse(BaseModel):
    """Paginated list of audit entries."""

    data: list[AuditResponse] = Field(description="The page of audit entries.")
    has_more: bool = Field(
        description="Whether more results exist after this page.", examples=[True]
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the next page; null when there are no more results.",
        examples=[ex.CURSOR],
    )
