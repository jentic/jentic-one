"""Repository for ExecutionRecord CRUD."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.execution_records import ExecutionRecord


class ExecutionRecordRepository:
    """Data access layer for ExecutionRecord entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        toolkit_id: str,
        trace_id: str,
        started_at: datetime,
        status: str,
        duration_ms: int | None = None,
        operation_id: str | None = None,
        api_vendor: str | None = None,
        api_name: str | None = None,
        api_version: str | None = None,
        api_host: str | None = None,
        pinned_revisions: dict | None = None,  # type: ignore[type-arg]
        http_status: int | None = None,
        error: str | None = None,
        created_by: str,
        actor_id: str,
        actor_type: str,
        credential_id: str | None = None,
        credential_name: str | None = None,
    ) -> ExecutionRecord:
        record = ExecutionRecord(
            toolkit_id=toolkit_id,
            trace_id=trace_id,
            started_at=started_at,
            status=status,
            duration_ms=duration_ms,
            operation_id=operation_id,
            api_vendor=api_vendor,
            api_name=api_name,
            api_version=api_version,
            api_host=api_host,
            pinned_revisions=pinned_revisions,
            http_status=http_status,
            error=error,
            created_by=created_by,
            actor_id=actor_id,
            actor_type=actor_type,
            credential_id=credential_id,
            credential_name=credential_name,
        )
        session.add(record)
        await session.flush()
        return record

    @staticmethod
    async def get_by_id(session: AsyncSession, record_id: str) -> ExecutionRecord | None:
        return await session.get(ExecutionRecord, record_id)

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        limit: int = 50,
        cursor_ts: datetime | None = None,
        cursor_id: str | None = None,
        toolkit_id: str | None = None,
        trace_id: str | None = None,
        status: list[str] | None = None,
        from_: datetime | None = None,
        to: datetime | None = None,
        api_vendor: str | None = None,
        api_name: str | None = None,
        api_version: str | None = None,
        actor_id: str | None = None,
        origin: str | None = None,
    ) -> list[ExecutionRecord]:
        stmt = (
            select(ExecutionRecord)
            .order_by(ExecutionRecord.started_at.desc(), ExecutionRecord.id.desc())
            .limit(limit)
        )
        if cursor_ts is not None and cursor_id is not None:
            stmt = stmt.where(
                tuple_(ExecutionRecord.started_at, ExecutionRecord.id)
                < tuple_(literal(cursor_ts), literal(cursor_id))
            )
        elif cursor_ts is not None:
            stmt = stmt.where(ExecutionRecord.started_at < cursor_ts)
        if toolkit_id is not None:
            stmt = stmt.where(ExecutionRecord.toolkit_id == toolkit_id)
        if trace_id is not None:
            stmt = stmt.where(ExecutionRecord.trace_id == trace_id)
        if status is not None:
            stmt = stmt.where(ExecutionRecord.status.in_(status))
        if from_ is not None:
            stmt = stmt.where(ExecutionRecord.started_at >= from_)
        if to is not None:
            stmt = stmt.where(ExecutionRecord.started_at < to)
        if api_vendor is not None:
            stmt = stmt.where(ExecutionRecord.api_vendor == api_vendor)
        if api_name is not None:
            stmt = stmt.where(ExecutionRecord.api_name == api_name)
        if api_version is not None:
            stmt = stmt.where(ExecutionRecord.api_version == api_version)
        if actor_id is not None:
            stmt = stmt.where(ExecutionRecord.actor_id == actor_id)
        if origin is not None:
            stmt = stmt.where(ExecutionRecord.origin == origin)
        result = await session.execute(stmt)
        return list(result.scalars().all())
