"""Repository for Event CRUD."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.services.errors import EventNotFoundError


class EventRepository:
    """Data access layer for Event entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        type: str,
        severity: str,
        summary: str,
        requires_action: bool = False,
        trace_id: str | None = None,
        detail: str | None = None,
        data: dict[str, Any] | None = None,
        execution_id: str | None = None,
        job_id: str | None = None,
        created_by: str | None,
        actor_id: str | None = None,
        actor_type: str | None = None,
    ) -> Event:
        event = Event(
            type=type,
            severity=severity,
            summary=summary,
            requires_action=requires_action,
            trace_id=trace_id,
            detail=detail,
            data=data if data is not None else {},
            execution_id=execution_id,
            job_id=job_id,
            created_by=created_by,
            actor_id=actor_id,
            actor_type=actor_type,
        )
        session.add(event)
        await session.flush()
        return event

    @staticmethod
    async def get_by_id(session: AsyncSession, event_id: str) -> Event | None:
        return await session.get(Event, event_id)

    @staticmethod
    async def exists_with_data_value(
        session: AsyncSession,
        *,
        event_type: str,
        key: str,
        value: str,
    ) -> bool:
        """Return whether any event of ``event_type`` has ``data[key] == value``.

        One narrow lookup used for table-backed dedupe (e.g. one
        ``mcp.session_started`` per ``data.session_id`` across workers and
        surfaces — a pair additionally *enforced* by the partial unique index
        ``uq_events_mcp_session_started_session``; this lookup is the cheap
        first check, the index is the guarantee). On Postgres that same index
        covers the ``(type, data->>'session_id')`` read here. For other
        type/key pairs the ``type`` predicate rides ``ix_events_type_created``
        and the JSON comparison filters the (small) per-type slice. On SQLite
        the JSON comparison compiles to a ``json_extract`` path form that does
        not match the index expression, so the lookup stays a per-type scan —
        acceptable for the embedded single-file target.
        """
        stmt = (
            select(Event.id)
            .where(Event.type == event_type)
            .where(Event.data[key].as_string() == value)
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        limit: int = 25,
        cursor: tuple[datetime, str] | None = None,
        event_type: list[str] | None = None,
        severity: list[str] | None = None,
        requires_action: bool | None = None,
        acknowledged: bool | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        trace_id: str | None = None,
        actor_id: str | None = None,
        actor_type: str | None = None,
    ) -> list[Event]:
        stmt = select(Event).order_by(Event.created_at.desc(), Event.id.desc()).limit(limit)
        if cursor is not None:
            cursor_dt, cursor_id = cursor
            stmt = stmt.where(
                or_(
                    Event.created_at < cursor_dt,
                    and_(Event.created_at == cursor_dt, Event.id < cursor_id),
                )
            )
        if event_type is not None:
            stmt = stmt.where(Event.type.in_(event_type))
        if severity is not None:
            stmt = stmt.where(Event.severity.in_(severity))
        if requires_action is not None:
            stmt = stmt.where(Event.requires_action == requires_action)
        if acknowledged is not None:
            stmt = stmt.where(Event.acknowledged == acknowledged)
        if from_dt is not None:
            stmt = stmt.where(Event.created_at >= from_dt)
        if to_dt is not None:
            stmt = stmt.where(Event.created_at < to_dt)
        if trace_id is not None:
            stmt = stmt.where(Event.trace_id == trace_id)
        if actor_id is not None:
            stmt = stmt.where(Event.actor_id == actor_id)
        if actor_type is not None:
            stmt = stmt.where(Event.actor_type == actor_type)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def acknowledge(
        session: AsyncSession,
        event_id: str,
        *,
        acknowledged_by: str,
        acknowledgement_note: str | None = None,
    ) -> Event:
        event = await session.get(Event, event_id)
        if event is None:
            raise EventNotFoundError(event_id)
        event.acknowledged = True
        event.acknowledged_at = datetime.now(UTC)
        event.acknowledged_by = acknowledged_by
        event.acknowledgement_note = acknowledgement_note
        await session.flush()
        return event

    @staticmethod
    async def list_after_cursor(
        session: AsyncSession,
        cursor: tuple[datetime, str],
        *,
        limit: int = 100,
        event_type: list[str] | None = None,
        severity: list[str] | None = None,
        requires_action: bool | None = None,
        trace_id: str | None = None,
        actor_id: str | None = None,
        actor_type: str | None = None,
    ) -> list[Event]:
        """Return events after the (created_at, id) cursor using two-tuple comparison.

        Unlike pure KSUID ordering, this correctly handles events created within
        the same second whose random payload yields out-of-order IDs.
        """
        cursor_dt, cursor_id = cursor
        stmt = (
            select(Event)
            .where(
                or_(
                    Event.created_at > cursor_dt,
                    and_(Event.created_at == cursor_dt, Event.id > cursor_id),
                )
            )
            .order_by(Event.created_at.asc(), Event.id.asc())
            .limit(limit)
        )
        if event_type is not None:
            stmt = stmt.where(Event.type.in_(event_type))
        if severity is not None:
            stmt = stmt.where(Event.severity.in_(severity))
        if requires_action is not None:
            stmt = stmt.where(Event.requires_action == requires_action)
        if trace_id is not None:
            stmt = stmt.where(Event.trace_id == trace_id)
        if actor_id is not None:
            stmt = stmt.where(Event.actor_id == actor_id)
        if actor_type is not None:
            stmt = stmt.where(Event.actor_type == actor_type)
        result = await session.execute(stmt)
        return list(result.scalars().all())
