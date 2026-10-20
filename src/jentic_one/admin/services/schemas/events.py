"""Event schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.shared.models.events import EventSeverity


class EventView(BaseModel):
    """Public event representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    severity: EventSeverity
    summary: str
    requires_action: bool
    trace_id: str | None = None
    detail: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    execution_id: str | None = None
    job_id: str | None = None
    actor_id: str | None = None
    actor_type: str | None = None
    created_at: datetime


class EventFilter(BaseModel):
    """Filter parameters for listing events."""

    event_type: list[str] | None = None
    severity: list[EventSeverity] | None = None
    requires_action: bool | None = None
    from_dt: datetime | None = None
    to_dt: datetime | None = None
    trace_id: str | None = None
    actor_id: str | None = None
    actor_type: str | None = None


class Heartbeat(BaseModel):
    """Heartbeat message for event streams."""

    type: str = "heartbeat"
    sent_at: datetime


#: Why the server ended an event stream; see :class:`StreamClosed`.
StreamClosedReason = Literal["refused", "forbidden", "unavailable"]


class StreamClosed(BaseModel):
    """Terminal message sent when the server ends an event stream.

    ``reason`` is why the caller may no longer receive events: ``refused``
    (the credential no longer verifies), ``forbidden`` (it verifies but lacks
    the stream's permission, or its user must rotate their password) or
    ``unavailable`` (it could not be re-verified, or the event store could not
    be read).
    """

    type: str = "stream_closed"
    reason: StreamClosedReason
