"""Repository for JobResult CRUD."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from jentic_one.admin.core.schema.job_results import JobResult


class JobResultRepository:
    """Data access layer for JobResult entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        job_id: str,
        kind: str,
        body: dict,  # type: ignore[type-arg]
        content_type: str | None = None,
        available_until: datetime | None = None,
        created_by: str,
    ) -> JobResult:
        job_result = JobResult(
            job_id=job_id,
            kind=kind,
            body=body,
            content_type=content_type,
            available_until=available_until,
            created_by=created_by,
        )
        session.add(job_result)
        await session.flush()
        return job_result

    @staticmethod
    async def get_by_job_id(session: AsyncSession, job_id: str) -> JobResult | None:
        stmt = select(JobResult).where(JobResult.job_id == job_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def available_job_ids(
        session: AsyncSession,
        job_ids: Sequence[str],
        *,
        now: datetime,
    ) -> set[str]:
        """Return the subset of ``job_ids`` that carry an unexpired ``JobResult``."""
        if not job_ids:
            return set()
        stmt = select(JobResult.job_id).where(
            JobResult.job_id.in_(job_ids),
            or_(JobResult.available_until.is_(None), JobResult.available_until > now),
        )
        result = await session.execute(stmt)
        return {str(job_id) for job_id in result.scalars().all()}

    @staticmethod
    async def delete_expired(session: AsyncSession) -> int:
        """Delete job results past their retention window. Returns count of deleted rows."""
        stmt = delete(JobResult).where(
            JobResult.available_until.is_not(None),
            JobResult.available_until <= func.now(),
        )
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]
