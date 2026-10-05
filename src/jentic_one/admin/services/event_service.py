"""Event management service."""

from __future__ import annotations

from typing import Any

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.repos import AuditRepository, EventRepository
from jentic_one.admin.scoping.filters import build_access_filters
from jentic_one.admin.services._support.pagination import Page, decode_cursor, encode_cursor
from jentic_one.admin.services.errors import EventNotFoundError, InvalidInputError
from jentic_one.admin.services.metrics import audit_events_counter
from jentic_one.admin.services.schemas.events import (
    EventAcknowledgePayload,
    EventFilter,
    EventView,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditAction, AuditTargetType


class EventService:
    """Manages event queries and acknowledgement.

    Reads and acknowledgement are owner-scoped: an event is visible to the actor it
    names (``actor_id`` or ``created_by``), to the human owner of that agent, and to
    ``org:admin``. System events with no subject are visible only to ``org:admin``.
    To any other caller an event is indistinguishable from a missing one.
    """

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_all(
        self,
        filter: EventFilter,
        *,
        identity: Identity,
        cursor: str | None = None,
        limit: int = 25,
    ) -> Page[EventView]:
        cursor_tuple = None
        if cursor is not None:
            cursor_dt, cursor_id = decode_cursor(cursor)
            cursor_tuple = (cursor_dt, cursor_id)

        async with self._ctx.admin_db.session() as session:
            events = await EventRepository.list_all(
                session,
                limit=limit + 1,
                cursor=cursor_tuple,
                event_type=filter.event_type,
                severity=[str(s) for s in filter.severity] if filter.severity else None,
                requires_action=filter.requires_action,
                acknowledged=filter.acknowledged,
                from_dt=filter.from_dt,
                to_dt=filter.to_dt,
                trace_id=filter.trace_id,
                actor_id=filter.actor_id,
                actor_type=filter.actor_type,
                filters=build_access_filters(identity, Event),
            )

        has_more = len(events) > limit
        if has_more:
            events = events[:limit]

        views = [self._to_view(e) for e in events]
        next_cursor = None
        if has_more and events:
            next_cursor = encode_cursor(events[-1].created_at, events[-1].id)

        return Page(data=views, has_more=has_more, next_cursor=next_cursor)

    async def get_by_id(self, event_id: str, *, identity: Identity) -> EventView:
        async with self._ctx.admin_db.session() as session:
            event = await EventRepository.get_by_id(
                session, event_id, filters=build_access_filters(identity, Event)
            )
        if event is None:
            raise EventNotFoundError(event_id)
        return self._to_view(event)

    async def acknowledge(
        self,
        event_id: str,
        payload: EventAcknowledgePayload,
        *,
        identity: Identity,
    ) -> EventView:
        if not payload.acknowledged:
            raise InvalidInputError("acknowledged must be true")

        async with self._ctx.admin_db.transaction() as session:
            event = await EventRepository.get_by_id(
                session, event_id, filters=build_access_filters(identity, Event)
            )
            if event is None:
                raise EventNotFoundError(event_id)

            if event.acknowledged:
                return self._to_view(event)

            event = await EventRepository.acknowledge(
                session,
                event_id,
                acknowledged_by=identity.sub,
                acknowledgement_note=payload.note,
            )

            await AuditRepository.record(
                session,
                action=AuditAction.UPDATE,
                target_type=AuditTargetType.EVENT,
                target_id=event_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
            )
            audit_events_counter.add(
                1, {"action": AuditAction.UPDATE, "target_type": AuditTargetType.EVENT}
            )

        return self._to_view(event)

    @staticmethod
    def _to_view(event: Any) -> EventView:
        return EventView(
            id=event.id,
            type=event.type,
            severity=event.severity,
            summary=event.summary,
            requires_action=event.requires_action,
            acknowledged=event.acknowledged,
            acknowledged_at=event.acknowledged_at,
            acknowledged_by=event.acknowledged_by,
            trace_id=event.trace_id,
            detail=event.detail,
            data=event.data,
            execution_id=event.execution_id,
            job_id=event.job_id,
            actor_id=event.actor_id,
            actor_type=event.actor_type,
            created_at=event.created_at,
        )
