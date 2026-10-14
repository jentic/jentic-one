"""Event request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from jentic_one.shared.models.events import EventSeverity


class EventLinks(BaseModel):
    """HAL-style links for an event."""

    self_: str = Field(serialization_alias="self", description="URL of this event.")
    execution: str | None = Field(
        default=None, description="URL of the related execution record; null if none."
    )
    job: str | None = Field(default=None, description="URL of the related job; null if none.")
    action: str | None = Field(
        default=None, description="Reserved link to a follow-up action; currently always null."
    )


class EventResponse(BaseModel):
    """Event representation in API responses."""

    event_id: str = Field(description="Unique ID of the event (`evt_…`).")
    type: str = Field(
        description=(
            "Namespaced event type, e.g. `import.completed`. Opaque string: historical "
            "events may carry types that are no longer emitted."
        )
    )
    severity: EventSeverity
    summary: str = Field(description="Short human-readable summary of the event.")
    requires_action: bool = Field(description="Whether the event needs someone to act on it.")
    created_at: datetime = Field(description="When the event was created (RFC 3339).")
    trace_id: str | None = Field(
        default=None, description="Trace ID of the request that produced the event, if any."
    )
    detail: str | None = Field(
        default=None, description="Longer human-readable detail; null if none."
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific structured payload; empty object when there is none.",
    )
    actor_id: str | None = Field(
        default=None, description="ID of the actor that caused the event; null if none."
    )
    actor_type: str | None = Field(
        default=None,
        description="Kind of actor that caused the event, e.g. `user` or `agent`; null if none.",
    )
    links: EventLinks = Field(serialization_alias="_links")


class EventListResponse(BaseModel):
    """Paginated list of events."""

    data: list[EventResponse] = Field(description="The page of events.")
    has_more: bool = Field(description="Whether more results exist after this page.")
    next_cursor: str | None = Field(
        default=None, description="Cursor for the next page; null when there are no more results."
    )
