"""Repository for Job CRUD and parent/child queries."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.services.errors import JobNotFoundError
from jentic_one.shared.models import JobKind, JobStatus


class JobRepository:
    """Data access layer for Job entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        kind: JobKind,
        status: JobStatus = JobStatus.QUEUED,
        parent_job_id: str | None = None,
        execution_id: str | None = None,
        error: str | None = None,
        created_by: str,
    ) -> Job:
        job = Job(
            kind=kind,
            status=status,
            parent_job_id=parent_job_id,
            execution_id=execution_id,
            error=error,
            created_by=created_by,
        )
        session.add(job)
        await session.flush()
        return job

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        job_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> Job | None:
        if filters is None:
            return await session.get(Job, job_id)
        stmt = select(Job).where(Job.id == job_id)
        for f in filters:
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def update(
        session: AsyncSession,
        job_id: str,
        *,
        status: JobStatus | None = None,
        error: str | None = None,
        execution_id: str | None = None,
    ) -> Job:
        """Update a job using write-or-keep semantics; cannot clear fields to None."""
        job = await session.get(Job, job_id)
        if job is None:
            raise JobNotFoundError(job_id)

        if status is not None:
            job.status = status
        if error is not None:
            job.error = error
        if execution_id is not None:
            job.execution_id = execution_id

        await session.flush()
        return job

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        limit: int = 25,
        cursor: datetime | None = None,
        cursor_id: str | None = None,
        kind: str | None = None,
        status: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[Job]:
        stmt = select(Job).order_by(Job.created_at.desc(), Job.id.desc()).limit(limit)
        if cursor is not None:
            if cursor_id is not None:
                stmt = stmt.where(
                    or_(
                        Job.created_at < cursor,
                        and_(Job.created_at == cursor, Job.id < cursor_id),
                    )
                )
            else:
                stmt = stmt.where(Job.created_at < cursor)
        if kind is not None:
            stmt = stmt.where(Job.kind == kind)
        if status:
            stmt = stmt.where(Job.status.in_(status))
        if since is not None:
            stmt = stmt.where(Job.created_at >= since)
        if until is not None:
            stmt = stmt.where(Job.created_at < until)
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def cancel_if_active(
        session: AsyncSession,
        job_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> Job | None:
        """Cancel a job if active (queued or running).

        Returns None if already terminal or not matched by ``filters``.
        """
        stmt = (
            update(Job)
            .where(Job.id == job_id, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
            .values(status=JobStatus.CANCELLED)
            .returning(Job)
        )
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is not None:
            await session.flush()
        return row

    @staticmethod
    async def get_child_job_ids(session: AsyncSession, parent_job_id: str) -> list[str]:
        result = await session.execute(
            select(Job.id).where(Job.parent_job_id == parent_job_id).order_by(Job.created_at.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_child_status_counts(
        session: AsyncSession, parent_job_id: str
    ) -> dict[JobStatus, int]:
        result = await session.execute(
            select(Job.status, func.count())
            .where(Job.parent_job_id == parent_job_id)
            .group_by(Job.status)
        )
        return {JobStatus(row[0]): row[1] for row in result.all()}
