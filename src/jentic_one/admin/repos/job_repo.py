"""Repository for Job CRUD and parent/child queries."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.held_executions import HeldExecution
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
        """Cancel a job if active (queued, running or held).

        Returns None if already terminal or not matched by ``filters``. Callers
        refuse held executions (which await approval) before calling this.
        """
        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.HELD]),
            )
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
    async def transition(
        session: AsyncSession,
        job_id: str,
        *,
        from_status: JobStatus,
        to_status: JobStatus,
    ) -> bool:
        """Move a job ``from_status`` → ``to_status``; False when it is in another status."""
        stmt = (
            update(Job)
            .where(Job.id == job_id, Job.status == from_status)
            .values(status=to_status, visible_at=None)
            .returning(Job.id)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

    @staticmethod
    def _held_execution_filter() -> ColumnElement[bool]:
        """The one predicate the holds surface reads jobs through.

        A job matches only if it is an execution job that a require-approval
        hold filed: an approval row names it, filed by the agent that created
        the job. The holds routes need no ``jobs:*`` permission, so this keeps
        them to the held execution behind each hold and never any other job.
        """
        return and_(
            Job.kind == JobKind.EXECUTION.value,
            exists().where(
                HeldExecution.job_id == Job.id,
                HeldExecution.agent_id == Job.created_by,
            ),
        )

    @staticmethod
    async def get_held_execution(
        session: AsyncSession,
        job_id: str,
        *,
        filters: Sequence[ColumnElement[bool]],
    ) -> Job | None:
        """The held execution job ``job_id``, or ``None`` if it is not one or not visible."""
        stmt = select(Job).where(Job.id == job_id, JobRepository._held_execution_filter(), *filters)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def held_execution_statuses(
        session: AsyncSession,
        job_ids: Sequence[str],
        *,
        filters: Sequence[ColumnElement[bool]],
    ) -> dict[str, str]:
        """Each visible held execution job's status, keyed by job id; others are absent."""
        if not job_ids:
            return {}
        stmt = select(Job.id, Job.status).where(
            Job.id.in_(job_ids), JobRepository._held_execution_filter(), *filters
        )
        result = await session.execute(stmt)
        return {job_id: str(status) for job_id, status in result.all()}

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

    @staticmethod
    async def cancel_active_for_actor(session: AsyncSession, actor_id: str) -> int:
        """Cancel every queued or running job the actor enqueued; returns the row count.

        The agent-purge sweep. Matches on ``created_by`` alone: the id prefix
        names the actor, and legacy agent jobs carry ``actor_type='user'``. A
        cancelled job is never claimed again, and the worker skips the
        completion and requeue writes for one it holds. The rows stay as history.
        """
        stmt = (
            update(Job)
            .where(
                Job.created_by == actor_id,
                Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
            )
            .values(status=JobStatus.CANCELLED.value, visible_at=None)
        )
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]
