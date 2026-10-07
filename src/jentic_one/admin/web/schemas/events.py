"""Event request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from jentic_one.shared.models.events import EventSeverity


class EventLinks(BaseModel):
    """HAL-style links for an event."""

    self_: str = Field(serialization_alias="self")
    execution: str | None = None
    job: str | None = None
    action: str | None = None


class EventResponse(BaseModel):
    """Event representation in API responses."""

    event_id: str
    type: str
    severity: EventSeverity
    summary: str
    requires_action: bool
    created_at: datetime
    trace_id: str | None = None
    detail: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    actor_id: str | None = None
    actor_type: str | None = None
    links: EventLinks = Field(serialization_alias="_links")


class EventListResponse(BaseModel):
    """Paginated list of events."""

    data: list[EventResponse]
    has_more: bool
    next_cursor: str | None = None
