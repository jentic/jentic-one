"""Repository for Note entities."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from enum import Enum, auto

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.registry.core.schema.notes import Note
from jentic_one.shared.db.utils import utcnow


class _Unset(Enum):
    TOKEN = auto()


UNSET = _Unset.TOKEN


class NoteRepository:
    """Data access layer for Note entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        resource_api_id: uuid.UUID | None = None,
        resource_operation_id: str | None = None,
        resource_execution_id: str | None = None,
        resource_credential_id: str | None = None,
        type: str | None = None,
        body: str,
        confidence: str | None = None,
        confidence_source: str = "client",
        source: str | None = None,
        created_by: str,
        related_execution_id: str | None = None,
    ) -> Note:
        note = Note(
            resource_api_id=resource_api_id,
            resource_operation_id=resource_operation_id,
            resource_execution_id=resource_execution_id,
            resource_credential_id=resource_credential_id,
            type=type,
            body=body,
            confidence=confidence,
            confidence_source=confidence_source,
            source=source,
            created_by=created_by,
            related_execution_id=related_execution_id,
        )
        session.add(note)
        await session.flush()
        # Re-load with the api relationship eager-loaded so callers can map it
        # without tripping the lazy="raise" guard after the session closes.
        reloaded = await NoteRepository.get_by_id(session, note.id)
        assert reloaded is not None
        return reloaded

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        note_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> Note | None:
        stmt = select(Note).where(Note.id == note_id).options(selectinload(Note.api))
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_page(
        session: AsyncSession,
        *,
        limit: int = 50,
        cursor_created_at: datetime | None = None,
        cursor_id: str | None = None,
        api_ids: list[uuid.UUID] | None = None,
        operation_id: str | None = None,
        execution_id: str | None = None,
        credential_id: str | None = None,
        type: str | None = None,
        created_by: str | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[Note]:
        stmt = (
            select(Note)
            .options(selectinload(Note.api))
            .order_by(Note.created_at.desc(), Note.id.desc())
            .limit(limit)
        )

        if cursor_created_at is not None and cursor_id is not None:
            stmt = stmt.where(
                or_(
                    Note.created_at < cursor_created_at,
                    and_(Note.created_at == cursor_created_at, Note.id < cursor_id),
                )
            )
        if api_ids is not None:
            stmt = stmt.where(Note.resource_api_id.in_(api_ids))
        if operation_id is not None:
            stmt = stmt.where(Note.resource_operation_id == operation_id)
        if execution_id is not None:
            stmt = stmt.where(Note.resource_execution_id == execution_id)
        if credential_id is not None:
            stmt = stmt.where(Note.resource_credential_id == credential_id)
        if type is not None:
            stmt = stmt.where(Note.type == type)
        if created_by is not None:
            stmt = stmt.where(Note.created_by == created_by)
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)

        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_fields(
        session: AsyncSession,
        note: Note,
        *,
        body: str | _Unset = UNSET,
        type: str | _Unset | None = UNSET,
        confidence: str | _Unset | None = UNSET,
        source: str | _Unset | None = UNSET,
        related_execution_id: str | _Unset | None = UNSET,
    ) -> Note:
        if not isinstance(body, _Unset):
            note.body = body
        if not isinstance(type, _Unset):
            note.type = type
        if not isinstance(confidence, _Unset):
            note.confidence = confidence
        if not isinstance(source, _Unset):
            note.source = source
        if not isinstance(related_execution_id, _Unset):
            note.related_execution_id = related_execution_id
        note.revision += 1
        note.updated_at = utcnow()
        await session.flush()
        reloaded = await NoteRepository.get_by_id(session, note.id)
        assert reloaded is not None
        return reloaded

    @staticmethod
    async def delete(session: AsyncSession, note: Note) -> None:
        await session.execute(delete(Note).where(Note.id == note.id))
        await session.flush()
