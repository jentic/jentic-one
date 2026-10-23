"""Operation service — listing operations for API revisions."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from pydantic import BaseModel

from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.operation_repo import OperationRepository
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.registry.services.errors import (
    ApiNotFoundError,
    NoCurrentRevisionError,
    RevisionNotFoundError,
)
from jentic_one.shared.context import Context
from jentic_one.shared.pagination import decode_cursor, encode_cursor

if TYPE_CHECKING:
    from jentic_one.registry.core.schema.api_revisions import ApiRevision


class OperationSummaryItem(BaseModel):
    """View model for a single operation in a paginated list."""

    id: str
    method: str
    path: str
    name: str | None
    description: str | None
    tags: list[str]
    deprecated: bool
    revision_id: uuid.UUID
    host: str | None


class OperationSummaryPage(BaseModel):
    """Paginated result of operations."""

    data: list[OperationSummaryItem]
    has_more: bool
    next_cursor: str | None = None
    vendor: str
    name: str
    version: str


class OperationService:
    """Read operations for API operations."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_for_revision(
        self,
        *,
        vendor: str,
        name: str,
        version: str,
        revision_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> OperationSummaryPage:
        try:
            revision_uuid = uuid.UUID(revision_id)
        except ValueError:
            raise RevisionNotFoundError(revision_id, vendor, name, version) from None

        async with self._ctx.registry_db.session() as session:
            api = await ApiRepository.get_by_identifier(session, vendor, name, version)
            if api is None:
                raise ApiNotFoundError(vendor, name, version)

            revision = await ApiRevisionRepository.get_for_api(session, api.id, revision_uuid)
            if revision is None:
                raise RevisionNotFoundError(revision_id, vendor, name, version)

            return await self._list_for_resolved_revision(
                session, revision, cursor, limit, vendor, name, version
            )

    async def list_for_live_revision(
        self,
        *,
        vendor: str,
        name: str,
        version: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> OperationSummaryPage:
        async with self._ctx.registry_db.session() as session:
            api = await ApiRepository.get_by_identifier(session, vendor, name, version)
            if api is None:
                raise ApiNotFoundError(vendor, name, version)

            if api.current_revision_id is None:
                raise NoCurrentRevisionError(vendor, name, version)

            revision = await ApiRevisionRepository.get_for_api(
                session, api.id, api.current_revision_id
            )
            if revision is None:
                raise NoCurrentRevisionError(vendor, name, version)

            return await self._list_for_resolved_revision(
                session, revision, cursor, limit, vendor, name, version
            )

    async def _list_for_resolved_revision(
        self,
        session: Any,
        revision: ApiRevision,
        cursor: str | None,
        limit: int,
        vendor: str,
        name: str,
        version: str,
    ) -> OperationSummaryPage:
        cursor_created_at = None
        cursor_id: str | None = None
        if cursor is not None:
            cursor_created_at, cursor_id = decode_cursor(cursor, id_kind="str")

        rows = await OperationRepository.list_page_for_revision(
            session,
            revision_id=revision.id,
            limit=limit + 1,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
        )

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        host: str | None = None
        if revision.servers:
            parsed = urlparse(revision.servers[0].url)
            host = parsed.hostname

        items: list[OperationSummaryItem] = []
        for row in rows:
            items.append(
                OperationSummaryItem(
                    id=row.id,
                    method=row.method,
                    path=row.path,
                    name=row.summary,
                    description=row.description,
                    tags=row.tags or [],
                    deprecated=row.deprecated,
                    revision_id=revision.id,
                    host=host,
                )
            )

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(last.created_at, last.id)

        return OperationSummaryPage(
            data=items,
            has_more=has_more,
            next_cursor=next_cursor,
            vendor=vendor,
            name=name,
            version=version,
        )
