"""Event request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from jentic_one.shared.models.events import EventSeverity
from jentic_one.shared.web import openapi_examples as ex


class EventLinks(BaseModel):
    """HAL-style links for an event."""

    self_: str = Field(
        serialization_alias="self",
        description="URL of this event.",
        examples=[f"https://jentic.example.com/events/{ex.EVENT_ID}"],
    )
    execution: str | None = Field(
        default=None,
        description="URL of the related execution record; null if none.",
        examples=[f"https://jentic.example.com/executions/{ex.EXECUTION_ID}"],
    )
    job: str | None = Field(
        default=None,
        description="URL of the related job; null if none.",
        examples=[f"https://jentic.example.com/jobs/{ex.JOB_ID}"],
    )
    action: str | None = Field(
        default=None,
        description="Reserved link to a follow-up action; currently always null.",
        examples=[f"https://jentic.example.com/events/{ex.EVENT_ID}/action"],
    )


class EventResponse(BaseModel):
    """Event representation in API responses."""

    event_id: str = Field(description="Unique ID of the event (`evt_…`).", examples=[ex.EVENT_ID])
    type: str = Field(
        description=(
            "Namespaced event type, e.g. `import.completed`. Opaque string: historical "
            "events may carry types that are no longer emitted."
        ),
        examples=["import.completed"],
    )
    severity: EventSeverity = Field(examples=["info"])
    summary: str = Field(
        description="Short human-readable summary of the event.",
        examples=["Imported stripe.com/stripe 2024-04-10"],
    )
    requires_action: bool = Field(
        description="Whether the event needs someone to act on it.", examples=[False]
    )
    created_at: datetime = Field(
        description="When the event was created (RFC 3339).", examples=["2026-01-15T09:30:00Z"]
    )
    trace_id: str | None = Field(
        default=None,
        description="Trace ID of the request that produced the event, if any.",
        examples=[ex.TRACE_ID],
    )
    detail: str | None = Field(
        default=None,
        description="Longer human-readable detail; null if none.",
        examples=["The import produced 1 revision with 142 operations."],
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific structured payload; empty object when there is none.",
        examples=[{"job_id": ex.JOB_ID, "revision_id": ex.REVISION_ID}],
    )
    actor_id: str | None = Field(
        default=None,
        description="ID of the actor that caused the event; null if none.",
        examples=[ex.USER_ID],
    )
    actor_type: str | None = Field(
        default=None,
        description="Kind of actor that caused the event, e.g. `user` or `agent`; null if none.",
        examples=["user"],
    )
    links: EventLinks = Field(serialization_alias="_links")


class EventListResponse(BaseModel):
    """Paginated list of events."""

    data: list[EventResponse] = Field(description="The page of events.")
    has_more: bool = Field(
        description="Whether more results exist after this page.", examples=[True]
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the next page; null when there are no more results.",
        examples=[ex.CURSOR],
    )
