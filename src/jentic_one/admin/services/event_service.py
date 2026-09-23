"""Event management service."""

from __future__ import annotations

from typing import Any

from jentic_one.admin.repos import EventRepository
from jentic_one.admin.services._support.pagination import Page, decode_cursor, encode_cursor
from jentic_one.admin.services.errors import EventNotFoundError
from jentic_one.admin.services.schemas.events import (
    EventFilter,
    EventView,
)
from jentic_one.shared.context import Context


class EventService:
    """Manages event queries."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_all(
        self,
        filter: EventFilter,
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
                from_dt=filter.from_dt,
                to_dt=filter.to_dt,
                trace_id=filter.trace_id,
                actor_id=filter.actor_id,
                actor_type=filter.actor_type,
            )

        has_more = len(events) > limit
        if has_more:
            events = events[:limit]

        views = [self._to_view(e) for e in events]
        next_cursor = None
        if has_more and events:
            next_cursor = encode_cursor(events[-1].created_at, events[-1].id)

        return Page(data=views, has_more=has_more, next_cursor=next_cursor)

    async def get_by_id(self, event_id: str) -> EventView:
        async with self._ctx.admin_db.session() as session:
            event = await EventRepository.get_by_id(session, event_id)
        if event is None:
            raise EventNotFoundError(event_id)
        return self._to_view(event)

    @staticmethod
    def _to_view(event: Any) -> EventView:
        return EventView(
            id=event.id,
            type=event.type,
            severity=event.severity,
            summary=event.summary,
            requires_action=event.requires_action,
            trace_id=event.trace_id,
            detail=event.detail,
            data=event.data,
            execution_id=event.execution_id,
            job_id=event.job_id,
            actor_id=event.actor_id,
            actor_type=event.actor_type,
            created_at=event.created_at,
        )
