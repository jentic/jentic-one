"""Events router — list, get, and SSE stream."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from datetime import datetime
from typing import Annotated, Final

import structlog
from fastapi import APIRouter, Depends, Header, Path, Query, Request
from fastapi.responses import StreamingResponse

from jentic_one.admin.services.errors import InvalidInputError
from jentic_one.admin.services.event_service import EventService
from jentic_one.admin.services.event_stream_service import EventStreamService
from jentic_one.admin.services.schemas.events import (
    EventFilter,
    EventView,
    Heartbeat,
    StreamClosed,
    StreamClosedReason,
)
from jentic_one.admin.web.deps import (
    get_event_service,
    get_event_stream_service,
    get_stream_clock,
    get_stream_recheck_seconds,
)
from jentic_one.admin.web.schemas.events import (
    EventLinks,
    EventListResponse,
    EventResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.db.errors import DatabaseUnavailableError
from jentic_one.shared.models.events import EventSeverity
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.deps import (
    CredentialRecheck,
    CredentialRecheckResult,
    get_credential_recheck,
)
from jentic_one.shared.web.links import build_link

_LAST_EVENT_ID_RE = re.compile(r"^evt_[0-9a-f]{24}$")

router = APIRouter()


def _event_response(view: EventView, request: Request) -> EventResponse:
    """Project an EventView to an EventResponse.

    ``view.type`` is a plain string, not an ``EventType`` member: the events
    table is append-only history, so rows may carry retired kinds (e.g. the
    theme-7 ``access_request.*`` family) that no longer exist as constants.
    Projection must never parse the stored kind through a closed enum.
    """
    links = EventLinks(
        self_=build_link(request, f"/events/{view.id}"),
        execution=build_link(request, f"/executions/{view.execution_id}")
        if view.execution_id
        else None,
        job=build_link(request, f"/jobs/{view.job_id}") if view.job_id else None,
        action=None,
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
    event_type: list[str] | None = Query(
        default=None,
        alias="event_type",
        description="Only return events of one of these types; repeat the parameter for several.",
        openapi_examples=ex.example(["import.completed"]),
    ),
    severity: list[EventSeverity] | None = Query(
        default=None,
        description=(
            "Only return events with one of these severities; repeat the parameter for several."
        ),
        openapi_examples=ex.example(["warning", "error"]),
    ),
    requires_action: bool | None = Query(
        default=None,
        description="Only return events whose `requires_action` flag matches.",
        openapi_examples=ex.example(True),
    ),
    from_dt: datetime | None = Query(
        default=None,
        alias="from",
        description="Only return events created at or after this time (RFC 3339).",
        openapi_examples=ex.example(ex.DATETIME_FROM),
    ),
    to_dt: datetime | None = Query(
        default=None,
        alias="to",
        description="Only return events created before this time (RFC 3339).",
        openapi_examples=ex.example(ex.DATETIME_TO),
    ),
    trace_id: str | None = Query(
        default=None,
        description="Only return events with this trace ID.",
        openapi_examples=ex.example(ex.TRACE_ID),
    ),
    actor_id: str | None = Query(
        default=None,
        description="Only return events caused by this actor.",
        openapi_examples=ex.example(ex.AGENT_ID),
    ),
    actor_type: str | None = Query(
        default=None,
        description="Only return events caused by this kind of actor, e.g. `user` or `agent`.",
        openapi_examples=ex.example("agent"),
    ),
    cursor: str | None = Query(
        default=None,
        description="Opaque cursor from a previous page's `next_cursor`; omit for the first page.",
        openapi_examples=ex.example(ex.CURSOR),
    ),
    limit: int = Query(
        default=25,
        ge=1,
        le=100,
        description="Maximum number of events to return (1-100, default 25).",
        openapi_examples=ex.example(25),
    ),
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
        identity=identity,
        cursor=cursor,
        limit=limit,
    )
    return EventListResponse(
        data=[_event_response(e, request) for e in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


#: What admits a caller to ``/events/stream``. The route gate and the open
#: stream's re-verification both read this one constant, so they cannot drift.
_STREAM_PERMISSIONS: Final = ("events:read",)

#: How a failed re-verification reads in the ``stream_closed`` frame. Anything
#: not listed (including a ``VALID`` outcome with no identity) fails closed.
_CLOSED_REASONS: Final[dict[CredentialRecheckResult, StreamClosedReason]] = {
    CredentialRecheckResult.REFUSED: "refused",
    CredentialRecheckResult.FORBIDDEN: "forbidden",
    CredentialRecheckResult.UNAVAILABLE: "unavailable",
}


@router.get("/events/stream")
async def stream_events(
    request: Request,
    identity: Identity = get_current_identity(required_permissions=_STREAM_PERMISSIONS),
    stream_svc: EventStreamService = Depends(get_event_stream_service),
    recheck_credential: CredentialRecheck = Depends(get_credential_recheck),
    recheck_seconds: float = Depends(get_stream_recheck_seconds),
    clock: Callable[[], float] = Depends(get_stream_clock),
    since: datetime | None = Query(
        default=None,
        description=(
            "Stream events created after this time (RFC 3339); defaults to now, and a future "
            "time is treated as now."
        ),
        openapi_examples=ex.example(ex.DATETIME_FROM),
    ),
    last_event_id: str | None = Header(
        default=None,
        alias="Last-Event-ID",
        description=(
            "ID of the last event received (`evt_` plus 24 hex characters), to resume a stream "
            "after it; ignored if the event is not visible to the caller."
        ),
        openapi_examples=ex.example(ex.EVENT_ID),
    ),
    event_type: list[str] | None = Query(
        default=None,
        alias="event_type",
        description="Only return events of one of these types; repeat the parameter for several.",
        openapi_examples=ex.example(["import.completed"]),
    ),
    severity: list[EventSeverity] | None = Query(
        default=None,
        description=(
            "Only return events with one of these severities; repeat the parameter for several."
        ),
        openapi_examples=ex.example(["warning", "error"]),
    ),
    requires_action: bool | None = Query(
        default=None,
        description="Only return events whose `requires_action` flag matches.",
        openapi_examples=ex.example(True),
    ),
    trace_id: str | None = Query(
        default=None,
        description="Only return events with this trace ID.",
        openapi_examples=ex.example(ex.TRACE_ID),
    ),
    actor_id: str | None = Query(
        default=None,
        description="Only return events caused by this actor.",
        openapi_examples=ex.example(ex.AGENT_ID),
    ),
    actor_type: str | None = Query(
        default=None,
        description="Only return events caused by this kind of actor, e.g. `user` or `agent`.",
        openapi_examples=ex.example("agent"),
    ),
) -> StreamingResponse:
    """Stream events as Server-Sent Events.

    The open stream re-verifies the caller at the first poll or page after each
    `admin.events.stream_recheck_seconds` interval (default 5 s) elapses. Each
    page is scoped by the caller's current permissions, so a narrower
    permission set narrows the stream. Permissions are those the credential
    carries: API keys and agent `at_` access tokens resolve them live, while
    other tokens (a login session token, for one) carry the set they were
    issued with and reflect a change only once refreshed. The stream ends
    with a `stream_closed` frame (`{"type": "stream_closed", "reason": ...}`)
    once the credential stops verifying (`refused`: expired, revoked, or its
    user or agent disabled or deleted), the caller no longer holds
    `events:read` or `org:admin` or must rotate their password (`forbidden`),
    or the check or the event store cannot be answered (`unavailable`).
    Reconnecting then returns the matching 401, 403 or 503.
    """
    if last_event_id is not None and not _LAST_EVENT_ID_RE.match(last_event_id):
        raise InvalidInputError("Last-Event-ID must match format evt_<24 hex chars>")

    log = structlog.get_logger(__name__)
    current = identity
    last_verified = clock()
    closed_reason: StreamClosedReason | None = None

    async def refresh_identity() -> Identity | None:
        # The stream outlives the verification that opened it. Before each page,
        # re-verify at most once per interval (#1139, #1836) and hand the
        # service the fresh identity, so a dead credential or a revoked
        # permission ends the stream and a narrower permission set narrows it.
        nonlocal current, last_verified, closed_reason
        now = clock()
        if now - last_verified < recheck_seconds:
            return current
        outcome = await recheck_credential(required_permissions=_STREAM_PERMISSIONS)
        if outcome.result is not CredentialRecheckResult.VALID or outcome.identity is None:
            closed_reason = _CLOSED_REASONS.get(outcome.result, "refused")
            log.info(
                "sse_stream_credential_unverified",
                sub=identity.sub,
                reason=closed_reason,
            )
            return None
        current = outcome.identity
        last_verified = now
        return current

    async def generate() -> AsyncIterator[str]:
        nonlocal closed_reason
        # ``aclosing`` closes the service generator on every exit — a ``return``
        # on disconnect, an exception, or cancellation of this generator — so
        # its per-page DB session is unwound and the pooled connection returned
        # (#627). The ``finally`` only logs — it must never touch the DB.
        try:
            items = stream_svc.stream(
                identity=identity,
                since=since,
                last_event_id=last_event_id,
                event_type=event_type,
                severity=[str(s) for s in severity] if severity else None,
                requires_action=requires_action,
                trace_id=trace_id,
                actor_id=actor_id,
                actor_type=actor_type,
                refresh_identity=refresh_identity,
            )
            try:
                async with aclosing(items):
                    async for item in items:
                        if await request.is_disconnected():
                            return
                        if isinstance(item, Heartbeat):
                            yield f"event: heartbeat\ndata: {item.model_dump_json()}\n\n"
                        elif isinstance(item, EventView):
                            resp = _event_response(item, request)
                            yield (
                                f"event: {resp.type}\n"
                                f"id: {resp.event_id}\n"
                                f"data: {resp.model_dump_json(by_alias=True)}\n\n"
                            )
            except DatabaseUnavailableError:
                # The 200 and its headers are already sent, so the error
                # handler cannot answer with a 503; the frame tells the client
                # instead, and a reconnect gets the 503.
                log.warning("sse_stream_store_unavailable", sub=identity.sub)
                closed_reason = "unavailable"
            if closed_reason is not None:
                closed = StreamClosed(reason=closed_reason)
                yield f"event: stream_closed\ndata: {closed.model_dump_json()}\n\n"
        finally:
            log.debug("sse_stream_closed")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/events/{event_id}")
async def get_event(
    event_id: Annotated[
        str, Path(description="ID of the event.", openapi_examples=ex.example(ex.EVENT_ID))
    ],
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["events:read"]),
    event_svc: EventService = Depends(get_event_service),
) -> EventResponse:
    """Get an event by ID."""
    view = await event_svc.get_by_id(event_id, identity=identity)
    return _event_response(view, request)
