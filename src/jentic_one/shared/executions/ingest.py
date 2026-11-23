"""Execution record persistence — thin wrapper usable from any surface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.shared.db.utils import insert_append_only_row
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.schemas import OperationInfo


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionRecordInput:
    """Complete terminal execution row snapshot passed to :func:`record_execution`."""

    execution_id: str
    toolkit_id: str | None
    trace_id: str
    started_at: datetime
    status: ExecutionStatus
    duration_ms: int | None = None
    operation: OperationInfo | None = None
    api_vendor: str | None = None
    api_name: str | None = None
    api_version: str | None = None
    api_host: str | None = None
    http_status: int | None = None
    error: str | None = None
    pinned_revisions: dict[str, Any] | None = None
    actor_id: str
    actor_type: str
    origin: str | None = None
    credential_id: str | None = None
    credential_name: str | None = None


async def record_execution(session: AsyncSession, record: ExecutionRecordInput) -> str:
    """Persist a terminal execution record. Returns the record ID.

    ``record.toolkit_id`` is nullable-legacy: the toolkit path (deleted in
    theme-5 Phase 6b) recorded its mediating toolkit; every execution now passes
    ``None`` (its consumer attribution is ``credential_id``). Historical rows
    keep theirs.

    ``record.operation`` carries the resolved operation's identity as one
    object; it is flattened onto the record's ``operation_id`` /
    ``operation_path`` / ``operation_method`` columns here (the DB shape stays
    flat).

    The row is append-only and the caller supplies its id, so it is written
    without an ORM instance and nothing is read back (see
    :func:`insert_append_only_row`). ``created_at`` and ``updated_at`` come
    from their column defaults.
    """
    if record.status not in tuple(ExecutionStatus):
        raise ValueError(f"Only terminal statuses allowed, got: {record.status!r}")

    values: dict[str, Any] = {
        "id": record.execution_id,
        "toolkit_id": record.toolkit_id or None,
        "trace_id": record.trace_id,
        "started_at": record.started_at,
        "status": record.status,
        "duration_ms": record.duration_ms,
        "operation_id": record.operation.id if record.operation else None,
        "operation_path": record.operation.path if record.operation else None,
        "operation_method": record.operation.method if record.operation else None,
        "api_vendor": record.api_vendor,
        "api_name": record.api_name,
        "api_version": record.api_version,
        "api_host": record.api_host,
        "http_status": record.http_status,
        "error": record.error,
        "pinned_revisions": record.pinned_revisions,
        "actor_id": record.actor_id,
        "actor_type": record.actor_type,
        "origin": record.origin,
        "credential_id": record.credential_id,
        "credential_name": record.credential_name,
    }
    await insert_append_only_row(session, ExecutionRecord, values)
    return record.execution_id
