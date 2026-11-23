"""Read-only repository for broker-issued execution jobs and their results (#1807).

Queries the ``jobs`` and ``job_results`` tables in the admin database directly
via parameterized SQL so the broker never imports the ``admin`` ORM. Scoping is
enforced on ``kind = 'execution'`` and ``created_by = :created_by`` so only
execution jobs enqueued by the caller can be loaded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, Text, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.db.types import UTCDateTime, json_variant
from jentic_one.shared.models.jobs import JobKind

_SELECT_EXECUTION_JOB = text(
    "SELECT j.id, j.parent_job_id, j.kind, j.status, j.actor_type,"
    " j.error, j.execution_id, j.created_by, j.created_at, j.updated_at"
    " FROM jobs j"
    " WHERE j.id = :job_id AND j.kind = :kind AND j.created_by = :created_by"
).columns(
    id=String,
    parent_job_id=String,
    kind=String,
    status=String,
    actor_type=String,
    error=Text,
    execution_id=String,
    created_by=String,
    created_at=UTCDateTime(),
    updated_at=UTCDateTime(),
)

_SELECT_JOB_RESULT = text(
    "SELECT r.id, r.job_id, r.kind, r.content_type, r.body, r.available_until, r.created_at"
    " FROM job_results r"
    " WHERE r.job_id = :job_id"
).columns(
    id=String,
    job_id=String,
    kind=String,
    content_type=String,
    body=json_variant(),
    available_until=UTCDateTime(),
    created_at=UTCDateTime(),
)

_SELECT_JOB_RESULT_AVAILABILITY = text(
    "SELECT r.available_until FROM job_results r WHERE r.job_id = :job_id"
).columns(available_until=UTCDateTime())


def _as_aware_datetime(value: datetime | str) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _as_optional_aware_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    return _as_aware_datetime(value)


def _as_body_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    return {}


@dataclass(frozen=True, slots=True)
class BrokerJobRow:
    """Read-only projection of a ``jobs`` row for the broker polling surface."""

    id: str
    parent_job_id: str | None
    kind: str
    status: str
    actor_type: str
    error: str | None
    execution_id: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class BrokerJobResultRow:
    """Read-only projection of a ``job_results`` row for the broker polling surface."""

    id: str
    job_id: str
    kind: str
    content_type: str | None
    body: dict[str, Any]
    available_until: datetime | None
    created_at: datetime


class BrokerJobRepository:
    """Read-only data access for caller-owned execution jobs and results."""

    @staticmethod
    async def get_execution_job_for_actor(
        session: AsyncSession,
        job_id: str,
        *,
        created_by: str,
    ) -> BrokerJobRow | None:
        row = (
            await session.execute(
                _SELECT_EXECUTION_JOB,
                {
                    "job_id": job_id,
                    "kind": JobKind.EXECUTION.value,
                    "created_by": created_by,
                },
            )
        ).one_or_none()
        if row is None:
            return None
        return BrokerJobRow(
            id=str(row.id),
            parent_job_id=str(row.parent_job_id) if row.parent_job_id is not None else None,
            kind=str(row.kind),
            status=str(row.status),
            actor_type=str(row.actor_type),
            error=str(row.error) if row.error is not None else None,
            execution_id=str(row.execution_id) if row.execution_id is not None else None,
            created_by=str(row.created_by) if row.created_by is not None else None,
            created_at=_as_aware_datetime(row.created_at),
            updated_at=_as_optional_aware_datetime(row.updated_at),
        )

    @staticmethod
    async def has_available_result(
        session: AsyncSession,
        job_id: str,
        *,
        now: datetime,
    ) -> bool:
        row = (
            await session.execute(_SELECT_JOB_RESULT_AVAILABILITY, {"job_id": job_id})
        ).one_or_none()
        if row is None:
            return False
        available_until = _as_optional_aware_datetime(row.available_until)
        return available_until is None or available_until > now

    @staticmethod
    async def get_result_by_job_id(
        session: AsyncSession,
        job_id: str,
    ) -> BrokerJobResultRow | None:
        row = (await session.execute(_SELECT_JOB_RESULT, {"job_id": job_id})).one_or_none()
        if row is None:
            return None
        return BrokerJobResultRow(
            id=str(row.id),
            job_id=str(row.job_id),
            kind=str(row.kind),
            content_type=str(row.content_type) if row.content_type is not None else None,
            body=_as_body_dict(row.body),
            available_until=_as_optional_aware_datetime(row.available_until),
            created_at=_as_aware_datetime(row.created_at),
        )
