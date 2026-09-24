"""Note service — CRUD operations for registry resource annotations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from jentic_one.registry.core.schema.notes import Note
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.note_repo import UNSET as UNSET
from jentic_one.registry.repos.note_repo import NoteRepository, _Unset
from jentic_one.registry.repos.operation_repo import OperationRepository as OpRepo
from jentic_one.registry.scoping.filters import build_access_filters
from jentic_one.registry.services.errors import (
    InvalidNoteResourceError,
    NoteNotFoundError,
    NotePreconditionFailedError,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.pagination import decode_cursor_str, encode_cursor


@dataclass(frozen=True)
class NoteView:
    """Resolved view of a single note."""

    id: str
    resource_api_id: uuid.UUID | None
    resource_api_vendor: str | None
    resource_api_name: str | None
    resource_api_version: str | None
    resource_operation_id: str | None
    resource_execution_id: str | None
    resource_credential_id: str | None
    type: str | None
    body: str
    confidence: str | None
    confidence_source: str
    source: str | None
    created_by: str
    related_execution_id: str | None
    revision: int
    created_at: datetime
    updated_at: datetime | None


class NotePage(BaseModel):
    """Paginated result of notes."""

    data: list[NoteView]
    has_more: bool
    next_cursor: str | None = None


class NoteService:
    """Operations for note CRUD with optimistic concurrency."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def create(
        self,
        *,
        resource_api: tuple[str, str, str] | None = None,
        resource_operation_id: str | None = None,
        resource_execution_id: str | None = None,
        resource_credential_id: str | None = None,
        type: str | None = None,
        body: str,
        confidence: str | None = None,
        confidence_source: str = "client",
        source: str | None = None,
        identity: Identity,
        related_execution_id: str | None = None,
    ) -> NoteView:
        resource_fields = [
            resource_api,
            resource_operation_id,
            resource_execution_id,
            resource_credential_id,
        ]
        populated = sum(1 for f in resource_fields if f is not None)
        if populated == 0:
            raise InvalidNoteResourceError("Exactly one resource field must be provided")
        if populated > 1:
            raise InvalidNoteResourceError("Only one resource field may be provided")

        api_uuid: uuid.UUID | None = None
        if resource_api is not None:
            vendor, name, version = resource_api
            async with self._ctx.registry_db.session() as session:
                api = await ApiRepository.get_by_identifier(session, vendor, name, version)
            if api is None:
                raise InvalidNoteResourceError(f"API '{vendor}/{name}/{version}' not found")
            api_uuid = api.id

        if resource_operation_id is not None:
            async with self._ctx.registry_db.session() as session:
                # Notes link to a registry operation by its primary key only. We
                # deliberately do NOT use get_by_id_for_inspect here: its spec
                # operationId fallback is an inspect-only convenience and would
                # let a note attach to an ambiguous, cross-API spec id while
                # persisting the unresolved input string (see #670 review).
                found = await OpRepo.get_by_ids(session, {resource_operation_id})
            if not found:
                raise InvalidNoteResourceError(f"Operation '{resource_operation_id}' not found")

        async with self._ctx.registry_db.transaction() as session:
            note = await NoteRepository.create(
                session,
                resource_api_id=api_uuid,
                resource_operation_id=resource_operation_id,
                resource_execution_id=resource_execution_id,
                resource_credential_id=resource_credential_id,
                type=type,
                body=body,
                confidence=confidence,
                confidence_source=confidence_source,
                source=source,
                created_by=identity.sub,
                related_execution_id=related_execution_id,
            )

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.NOTE,
            target_id=note.id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            origin=identity.origin.value,
        )
        return _to_view(note)

    async def get(self, note_id: str, *, identity: Identity | None = None) -> NoteView:
        if not note_id.startswith("note_"):
            raise NoteNotFoundError(note_id)

        access_filters = build_access_filters(identity, Note) if identity is not None else None

        async with self._ctx.registry_db.session() as session:
            note = await NoteRepository.get_by_id(session, note_id, filters=access_filters)

        if note is None:
            raise NoteNotFoundError(note_id)

        return _to_view(note)

    async def list_page(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        api: str | None = None,
        operation_id: str | None = None,
        execution_id: str | None = None,
        credential_id: str | None = None,
        type: str | None = None,
        created_by: str | None = None,
        identity: Identity | None = None,
    ) -> NotePage:
        cursor_created_at: datetime | None = None
        cursor_id: str | None = None
        if cursor is not None:
            cursor_created_at, cursor_id = decode_cursor_str(cursor)

        api_ids: list[uuid.UUID] | None = None
        if api is not None:
            parts = api.split(":")
            vendor = parts[0]
            name = parts[1] if len(parts) > 1 and parts[1] else None
            version = parts[2] if len(parts) > 2 and parts[2] else None
            if not vendor:
                # An empty/blank api filter resolves to nothing rather than
                # silently returning every note.
                return NotePage(data=[], has_more=False, next_cursor=None)
            async with self._ctx.registry_db.session() as session:
                api_ids = await ApiRepository.resolve_ids(
                    session, vendor=vendor, name=name, version=version
                )
            if not api_ids:
                return NotePage(data=[], has_more=False, next_cursor=None)

        access_filters = build_access_filters(identity, Note) if identity is not None else None

        async with self._ctx.registry_db.session() as session:
            rows = await NoteRepository.list_page(
                session,
                limit=limit + 1,
                cursor_created_at=cursor_created_at,
                cursor_id=cursor_id,
                api_ids=api_ids,
                operation_id=operation_id,
                execution_id=execution_id,
                credential_id=credential_id,
                type=type,
                created_by=created_by,
                filters=access_filters,
            )

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        items = [_to_view(row) for row in rows]

        next_cursor: str | None = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(last.created_at, last.id)

        return NotePage(data=items, has_more=has_more, next_cursor=next_cursor)

    async def update(
        self,
        note_id: str,
        *,
        if_match: int | None = None,
        body: str | _Unset = UNSET,
        type: str | _Unset | None = UNSET,
        confidence: str | _Unset | None = UNSET,
        source: str | _Unset | None = UNSET,
        related_execution_id: str | _Unset | None = UNSET,
        identity: Identity,
    ) -> NoteView:
        if not note_id.startswith("note_"):
            raise NoteNotFoundError(note_id)

        async with self._ctx.registry_db.transaction() as session:
            note = await NoteRepository.get_by_id(session, note_id)
            if note is None:
                raise NoteNotFoundError(note_id)

            if if_match is not None and if_match != note.revision:
                raise NotePreconditionFailedError(note_id, if_match, note.revision)

            kwargs: dict[str, Any] = {}
            if not isinstance(body, _Unset):
                kwargs["body"] = body
            if not isinstance(type, _Unset):
                kwargs["type"] = type
            if not isinstance(confidence, _Unset):
                kwargs["confidence"] = confidence
            if not isinstance(source, _Unset):
                kwargs["source"] = source
            if not isinstance(related_execution_id, _Unset):
                kwargs["related_execution_id"] = related_execution_id

            updated = await NoteRepository.update_fields(session, note, **kwargs)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.NOTE,
            target_id=note_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            origin=identity.origin.value,
        )
        return _to_view(updated)

    async def delete(
        self, note_id: str, *, if_match: int | None = None, identity: Identity
    ) -> None:
        if not note_id.startswith("note_"):
            raise NoteNotFoundError(note_id)

        async with self._ctx.registry_db.transaction() as session:
            note = await NoteRepository.get_by_id(session, note_id)
            if note is None:
                raise NoteNotFoundError(note_id)

            if if_match is not None and if_match != note.revision:
                raise NotePreconditionFailedError(note_id, if_match, note.revision)

            await NoteRepository.delete(session, note)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.DELETE,
            target_type=AuditTargetType.NOTE,
            target_id=note_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            origin=identity.origin.value,
        )


def _to_view(note: Note) -> NoteView:
    api = note.api
    return NoteView(
        id=note.id,
        resource_api_id=note.resource_api_id,
        resource_api_vendor=api.vendor if api is not None else None,
        resource_api_name=api.name if api is not None else None,
        resource_api_version=api.version if api is not None else None,
        resource_operation_id=note.resource_operation_id,
        resource_execution_id=note.resource_execution_id,
        resource_credential_id=note.resource_credential_id,
        type=note.type,
        body=note.body,
        confidence=note.confidence,
        confidence_source=note.confidence_source,
        source=note.source,
        created_by=note.created_by,
        related_execution_id=note.related_execution_id,
        revision=note.revision,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )
