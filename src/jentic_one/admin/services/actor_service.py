"""Unified actor directory service."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from jentic_one.admin.repos import ActorDirectoryRepository
from jentic_one.admin.services._support.pagination import Page, decode_cursor, encode_cursor
from jentic_one.admin.services.schemas.actors import ActorView
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType


class ActorService:
    """Provides a unified read-only view across all actor types."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_all(
        self,
        cursor: str | None = None,
        limit: int = 1000,
    ) -> Page[ActorView]:
        cursor_dt = None
        cursor_id: str | None = None
        if cursor is not None:
            cursor_dt, cursor_id = decode_cursor(cursor)

        async with self._ctx.admin_db.session() as session:
            rows = await ActorDirectoryRepository.list_all(
                session, limit=limit + 1, cursor_ts=cursor_dt, cursor_id=cursor_id
            )

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        views = [_to_view(row) for row in rows]

        next_cursor: str | None = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(last.created_at, last.id)

        return Page(data=views, has_more=has_more, next_cursor=next_cursor)

    async def lookup(self, ids: Sequence[str]) -> list[ActorView]:
        """Resolve the given actor ids; ids that match no user or agent are omitted."""
        unique_ids = list(dict.fromkeys(ids))
        async with self._ctx.admin_db.session() as session:
            rows = await ActorDirectoryRepository.get_by_ids(session, unique_ids)
        return [_to_view(row) for row in rows]


def _to_view(row: Any) -> ActorView:
    return ActorView(
        id=row.id,
        actor_type=ActorType(row.actor_type),
        name=row.name,
        active=bool(row.active),
        created_at=row.created_at,
    )
