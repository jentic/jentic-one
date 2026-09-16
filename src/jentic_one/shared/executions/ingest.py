"""Execution record persistence — thin wrapper usable from any surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.schemas import OperationInfo


async def record_execution(
    session: AsyncSession,
    *,
    execution_id: str,
    toolkit_id: str | None,
    trace_id: str,
    started_at: datetime,
    status: ExecutionStatus,
    duration_ms: int | None = None,
    operation: OperationInfo | None = None,
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
    """Persist a terminal execution record. Returns the record ID.

    ``toolkit_id`` is nullable-legacy (theme-5 Phase 2): the legacy toolkit
    path records its mediating toolkit; direct-binding executions pass ``None``
    (their consumer attribution is ``credential_id``).

    ``operation`` carries the resolved operation's identity as one object;
    it is flattened onto the record's ``operation_id`` / ``operation_name`` /
    ``operation_method`` columns here (the DB shape stays flat).
    """
    if status not in tuple(ExecutionStatus):
        raise ValueError(f"Only terminal statuses allowed, got: {status!r}")

    # Registry path templates are unbounded ``Text`` while the record column is
    # ``String(512)`` — truncate defensively so an oversized template can't fail
    # the flush and lose the whole record (the value is display-only; the join
    # key stays ``operation_id``). Postgres rejects oversize; SQLite silently
    # accepts it, so this is the only cross-backend guard.
    operation_name = operation.name[:512] if operation and operation.name else None

    record = ExecutionRecord(
        id=execution_id,
        toolkit_id=toolkit_id or None,
        trace_id=trace_id,
        started_at=started_at,
        status=status,
        duration_ms=duration_ms,
        operation_id=operation.id if operation else None,
        operation_name=operation_name,
        operation_method=operation.method if operation else None,
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
