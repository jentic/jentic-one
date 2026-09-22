"""Events router — list, get, and SSE stream."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from jentic_one.admin.services.errors import InvalidInputError
from jentic_one.admin.services.event_service import EventService
from jentic_one.admin.services.event_stream_service import EventStreamService
from jentic_one.admin.services.schemas.events import (
    EventFilter,
    EventView,
    Heartbeat,
)
from jentic_one.admin.web.deps import (
    get_event_service,
    get_event_stream_service,
)
from jentic_one.admin.web.schemas.events import (
    EventLinks,
    EventListResponse,
    EventResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.links import build_link

_LAST_EVENT_ID_RE = re.compile(r"^evt_[0-9a-f]{24}$")

router = APIRouter()


def _resolve_action_link(view: EventView, request: Request) -> str | None:
    """Return the canonical action URL for actionable event types, or None."""
    if view.type == EventType.ACCESS_REQUEST_FILED and view.data.get("request_id"):
        return build_link(request, f"/access-requests/{view.data['request_id']}:decide")
    return None


def _event_response(view: EventView, request: Request) -> EventResponse:
    """Project an EventView to an EventResponse."""
    links = EventLinks(
        self_=build_link(request, f"/events/{view.id}"),
        execution=build_link(request, f"/executions/{view.execution_id}")
        if view.execution_id
        else None,
        job=build_link(request, f"/jobs/{view.job_id}") if view.job_id else None,
        action=_resolve_action_link(view, request),
    )
    return EventResponse(
        event_id=view.id,
        type=view.type,
        severity=EventSeverity(view.severity),
        summary=view.summary,
        requires_action=view.requires_action,
        created_at=view.created_at,
        trace_id=view.trace_id,
        detail=view.detail,
        data=view.data,
        actor_id=view.actor_id,
        actor_type=view.actor_type,
        links=links,
    )


@router.get("/events")
async def list_events(
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["events:read"]),
    event_svc: EventService = Depends(get_event_service),
    event_type: list[str] | None = Query(default=None, alias="event_type"),
    severity: list[EventSeverity] | None = Query(default=None),
    requires_action: bool | None = None,
    from_dt: datetime | None = Query(default=None, alias="from"),
    to_dt: datetime | None = Query(default=None, alias="to"),
    trace_id: str | None = None,
    actor_id: str | None = None,
    actor_type: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> EventListResponse:
    """List events with optional filters."""
    page = await event_svc.list_all(
        filter=EventFilter(
            event_type=event_type,
            severity=severity,
            requires_action=requires_action,
            from_dt=from_dt,
            to_dt=to_dt,
            trace_id=trace_id,
            actor_id=actor_id,
            actor_type=actor_type,
        ),
        cursor=cursor,
        limit=limit,
    )
    return EventListResponse(
        data=[_event_response(e, request) for e in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


@router.get("/events/stream")
async def stream_events(
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["events:read"]),
    stream_svc: EventStreamService = Depends(get_event_stream_service),
    since: datetime | None = None,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    event_type: list[str] | None = Query(default=None, alias="event_type"),
    severity: list[EventSeverity] | None = Query(default=None),
    requires_action: bool | None = None,
    trace_id: str | None = None,
    actor_id: str | None = None,
    actor_type: str | None = None,
) -> StreamingResponse:
    """Stream events as Server-Sent Events."""
    if last_event_id is not None and not _LAST_EVENT_ID_RE.match(last_event_id):
        raise InvalidInputError("Last-Event-ID must match format evt_<24 hex chars>")

    async def generate() -> AsyncIterator[str]:
        # The ``async for`` protocol calls ``aclose()`` on the underlying stream
        # generator when we ``break`` or when this generator is cancelled/closed
        # on client disconnect, so the per-poll DB session is always unwound and
        # the pooled connection returned (#627). The ``finally`` only logs — it
        # must never touch the DB.
        log = structlog.get_logger(__name__)
        try:
            async for item in stream_svc.stream(
                since=since,
                last_event_id=last_event_id,
                event_type=event_type,
                severity=[str(s) for s in severity] if severity else None,
                requires_action=requires_action,
                trace_id=trace_id,
                actor_id=actor_id,
                actor_type=actor_type,
            ):
                if await request.is_disconnected():
                    break
                if isinstance(item, Heartbeat):
                    yield f"event: heartbeat\ndata: {item.model_dump_json()}\n\n"
                elif isinstance(item, EventView):
                    resp = _event_response(item, request)
                    yield (
                        f"event: {resp.type}\n"
                        f"id: {resp.event_id}\n"
                        f"data: {resp.model_dump_json(by_alias=True)}\n\n"
                    )
        finally:
            log.debug("sse_stream_closed")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/events/{event_id}")
async def get_event(
    event_id: str,
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["events:read"]),
    event_svc: EventService = Depends(get_event_service),
) -> EventResponse:
    """Get an event by ID."""
    view = await event_svc.get_by_id(event_id)
    return _event_response(view, request)
