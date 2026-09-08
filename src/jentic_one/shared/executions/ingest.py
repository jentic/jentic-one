"""Execution record persistence — thin wrapper usable from any surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.shared.models import ExecutionStatus


async def record_execution(
    session: AsyncSession,
    *,
    execution_id: str,
    toolkit_id: str,
    trace_id: str,
    started_at: datetime,
    status: ExecutionStatus,
    duration_ms: int | None = None,
    operation_id: str | None = None,
    api_vendor: str | None = None,
    api_name: str | None = None,
    api_version: str | None = None,
    api_host: str | None = None,
    http_status: int | None = None,
    error: str | None = None,
    pinned_revisions: dict[str, Any] | None = None,
    actor_id: str,
    actor_type: str,
    origin: str | None = None,
    credential_id: str | None = None,
    credential_name: str | None = None,
) -> str:
    """Persist a terminal execution record. Returns the record ID."""
    if status not in tuple(ExecutionStatus):
        raise ValueError(f"Only terminal statuses allowed, got: {status!r}")

    record = ExecutionRecord(
        id=execution_id,
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
        http_status=http_status,
        error=error,
        pinned_revisions=pinned_revisions,
        actor_id=actor_id,
        actor_type=actor_type,
        origin=origin,
        credential_id=credential_id,
        credential_name=credential_name,
    )
    session.add(record)
    await session.flush()
    return str(record.id)
