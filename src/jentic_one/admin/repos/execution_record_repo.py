"""Repository for ExecutionRecord CRUD."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.shared.db.keyset import apply_desc_keyset
from jentic_one.shared.pagination import Cursor


class ApiLastUseRow(NamedTuple):
    """Latest execution by one actor through one credential against one API."""

    credential_id: str
    api_vendor: str | None
    api_name: str | None
    last_used_at: datetime


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
        operation_path: str | None = None,
        operation_method: str | None = None,
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
            operation_path=operation_path,
            operation_method=operation_method,
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
    async def get_by_id(
        session: AsyncSession,
        record_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> ExecutionRecord | None:
        if filters is None:
            return await session.get(ExecutionRecord, record_id)
        stmt = select(ExecutionRecord).where(ExecutionRecord.id == record_id)
        for f in filters:
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        limit: int = 50,
        cursor: Cursor | None = None,
        trace_id: str | None = None,
        status: list[str] | None = None,
        from_: datetime | None = None,
        to: datetime | None = None,
        api_vendor: str | None = None,
        api_name: str | None = None,
        api_version: str | None = None,
        actor_id: str | None = None,
        origin: str | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[ExecutionRecord]:
        stmt = (
            select(ExecutionRecord)
            .order_by(ExecutionRecord.started_at.desc(), ExecutionRecord.id.desc())
            .limit(limit)
        )
        stmt = apply_desc_keyset(stmt, ExecutionRecord.started_at, ExecutionRecord.id, cursor)
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
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def last_used_by_api(
        session: AsyncSession, *, actor_id: str, credential_ids: Sequence[str]
    ) -> list[ApiLastUseRow]:
        """Latest ``started_at`` per credential and API for one actor's executions.

        "Last used" for the actor's credential bindings: any recorded call
        through the credential counts, whatever its outcome. Calls whose API
        was never resolved come back with ``api_vendor`` None. A credential
        with no recorded call has no row.
        """
        if not credential_ids:
            return []
        stmt = (
            select(
                ExecutionRecord.credential_id,
                ExecutionRecord.api_vendor,
                ExecutionRecord.api_name,
                func.max(ExecutionRecord.started_at),
            )
            .where(
                ExecutionRecord.actor_id == actor_id,
                ExecutionRecord.credential_id.in_(list(credential_ids)),
            )
            .group_by(
                ExecutionRecord.credential_id, ExecutionRecord.api_vendor, ExecutionRecord.api_name
            )
        )
        result = await session.execute(stmt)
        return [
            ApiLastUseRow(credential_id=str(cid), api_vendor=vendor, api_name=name, last_used_at=ts)
            for cid, vendor, name, ts in result.all()
            if cid is not None and ts is not None
        ]
