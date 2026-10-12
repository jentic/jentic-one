"""Job management service."""

from __future__ import annotations

from typing import Any

from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos import AuditRepository, JobRepository
from jentic_one.admin.scoping.filters import build_access_filters
from jentic_one.admin.services._support.pagination import Page, decode_cursor, encode_cursor
from jentic_one.admin.services.errors import JobNotFoundError
from jentic_one.admin.services.metrics import audit_events_counter
from jentic_one.admin.services.schemas.jobs import JobFilter, JobView
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.actors import actor_type_label_from_id
from jentic_one.shared.models.audit import AuditAction, AuditTargetType


def _job_actor_type(created_by: str | None, stored: str) -> str:
    """The enqueuing actor's type, preferring the id prefix over the stored column.

    Rows written before migration ``r7s8t9u0v1w2`` got the column's ``'user'``
    server default regardless of who enqueued them, so an agent-created legacy
    job reads ``actor_type='user'``. The ``created_by`` prefix (``usr_`` /
    ``agnt_`` / retired ``sva_``) is authoritative where it is recognisable;
    the stored value is the fallback for anything else.
    """
    if created_by:
        try:
            return actor_type_label_from_id(created_by)
        except ValueError:
            pass
    return stored


class JobService:
    """Manages job queries and cancellation.

    Every read and the cancel are scoped to the caller (``build_access_filters``
    on ``Job``): a job outside the caller's visibility is indistinguishable from
    a missing one (``JobNotFoundError`` -> 404).
    """

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_all(
        self,
        filter: JobFilter,
        *,
        identity: Identity,
        cursor: str | None = None,
        limit: int = 25,
    ) -> Page[JobView]:
        access_filters = build_access_filters(identity, Job)
        cursor_dt = None
        cursor_id: str | None = None
        if cursor is not None:
            cursor_dt, cursor_id = decode_cursor(cursor)

        async with self._ctx.admin_db.session() as session:
            jobs = await JobRepository.list_all(
                session,
                limit=limit + 1,
                cursor=cursor_dt,
                cursor_id=cursor_id,
                kind=filter.kind,
                status=filter.status,
                since=filter.from_,
                until=filter.to,
                filters=access_filters,
            )

        has_more = len(jobs) > limit
        if has_more:
            jobs = jobs[:limit]

        views = [self._to_view(j) for j in jobs]
        next_cursor = None
        if has_more and jobs:
            next_cursor = encode_cursor(jobs[-1].created_at, jobs[-1].id)

        return Page(data=views, has_more=has_more, next_cursor=next_cursor)

    async def get_by_id(self, job_id: str, *, identity: Identity) -> JobView:
        access_filters = build_access_filters(identity, Job)
        async with self._ctx.admin_db.session() as session:
            job = await JobRepository.get_by_id(session, job_id, filters=access_filters)
        if job is None:
            raise JobNotFoundError(job_id)
        return self._to_view(job)

    async def cancel(self, job_id: str, *, identity: Identity) -> JobView:
        access_filters = build_access_filters(identity, Job)
        async with self._ctx.admin_db.transaction() as session:
            job = await JobRepository.get_by_id(session, job_id, filters=access_filters)
            if job is None:
                raise JobNotFoundError(job_id)

            cancelled = await JobRepository.cancel_if_active(
                session, job_id, filters=access_filters
            )
            if cancelled is None:
                return self._to_view(job)

            await AuditRepository.record(
                session,
                action=AuditAction.UPDATE,
                target_type=AuditTargetType.JOB,
                target_id=job_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
            )
            audit_events_counter.add(
                1, {"action": AuditAction.UPDATE, "target_type": AuditTargetType.JOB}
            )

        return await self.get_by_id(job_id, identity=identity)

    @staticmethod
    def _to_view(job: Any) -> JobView:
        return JobView(
            id=job.id,
            kind=job.kind,
            status=job.status,
            parent_job_id=job.parent_job_id,
            execution_id=job.execution_id,
            error=job.error,
            created_at=job.created_at,
            updated_at=job.updated_at,
            actor_id=job.created_by,
            actor_type=_job_actor_type(job.created_by, job.actor_type),
        )
