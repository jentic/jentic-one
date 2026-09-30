"""Execution record service."""

from __future__ import annotations

from typing import Any

from jentic_one.admin.repos import ExecutionRecordRepository
from jentic_one.admin.services._support.pagination import Page, decode_cursor, encode_cursor
from jentic_one.admin.services.errors import ExecutionNotFoundError
from jentic_one.admin.services.schemas.executions import (
    ApiInfo,
    ExecutionFilter,
    ExecutionView,
)
from jentic_one.shared.context import Context


class ExecutionService:
    """Manages execution record queries."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_all(
        self,
        filter: ExecutionFilter,
        cursor: str | None = None,
        limit: int = 25,
    ) -> Page[ExecutionView]:
        cursor_ts: Any = None
        cursor_id: str | None = None
        if cursor is not None:
            cursor_ts, cursor_id = decode_cursor(cursor)

        async with self._ctx.admin_db.session() as session:
            records = await ExecutionRecordRepository.list_all(
                session,
                limit=limit + 1,
                cursor_ts=cursor_ts,
                cursor_id=cursor_id,
                toolkit_id=filter.toolkit_id,
                trace_id=filter.trace_id,
                status=filter.status,
                from_=filter.from_,
                to=filter.to,
                api_vendor=filter.api_vendor,
                api_name=filter.api_name,
                api_version=filter.api_version,
                actor_id=filter.actor_id,
                origin=filter.origin,
            )

        has_more = len(records) > limit
        if has_more:
            records = records[:limit]

        views = [self._to_view(r) for r in records]
        next_cursor = None
        if has_more and records:
            next_cursor = encode_cursor(records[-1].started_at, records[-1].id)

        return Page(data=views, has_more=has_more, next_cursor=next_cursor)

    async def get_by_id(self, execution_id: str) -> ExecutionView:
        async with self._ctx.admin_db.session() as session:
            record = await ExecutionRecordRepository.get_by_id(session, execution_id)
        if record is None:
            raise ExecutionNotFoundError(execution_id)

        return self._to_view(record)

    @staticmethod
    def _to_view(record: Any) -> ExecutionView:
        api = None
        if record.api_vendor and record.api_name and record.api_version:
            api = ApiInfo(
                vendor=record.api_vendor,
                name=record.api_name,
                version=record.api_version,
                host=getattr(record, "api_host", None),
            )

        return ExecutionView(
            id=record.id,
            toolkit_id=record.toolkit_id,
            # Denormalized at flattening time (theme-5 Phase 6b) — the
            # control ``toolkits`` table this used to be resolved from is
            # gone. NULL for rows whose toolkit was already deleted, exactly
            # as the old read-time resolver reported them.
            toolkit_name=record.toolkit_name,
            trace_id=record.trace_id,
            started_at=record.started_at,
            duration_ms=record.duration_ms,
            status=record.status,
            operation_id=record.operation_id,
            api=api,
            pinned_revisions=record.pinned_revisions,
            http_status=record.http_status,
            error=record.error,
            created_at=record.created_at,
            actor_id=record.actor_id,
            actor_type=record.actor_type,
            origin=record.origin,
            credential_id=record.credential_id,
            credential_name=record.credential_name,
        )
