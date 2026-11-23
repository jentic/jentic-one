"""Caller-scoped job status and result service for the broker data plane (#1807)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from jentic_one.broker.core.errors import (
    JobNotCompletedError,
    JobNotFoundError,
    JobResultExpiredError,
)
from jentic_one.broker.repos.job_repo import BrokerJobRepository, BrokerJobRow
from jentic_one.broker.services.jobs.schemas import BrokerJobResultView, BrokerJobView
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.actors import actor_type_label_from_id
from jentic_one.shared.models.jobs import JobStatus

#: Terminal job statuses that may carry a stored ``JobResult`` (#2030).
_RESULT_STATUSES = frozenset({JobStatus.COMPLETED.value, JobStatus.FAILED.value})


def _job_actor_type(created_by: str | None, stored: str) -> str:
    """Resolve the enqueuing actor's type, preferring a recognized id prefix."""
    if created_by:
        try:
            return actor_type_label_from_id(created_by)
        except ValueError:
            pass
    return stored


class BrokerJobService:
    """Reads caller-owned broker execution jobs and stored results.

    Every lookup requires ``created_by == identity.sub``, ``kind == 'execution'``,
    and a matching actor type; any mismatch raises :class:`JobNotFoundError` (404)
    so foreign or non-execution jobs are indistinguishable from missing ones.
    """

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def get_by_id(
        self,
        job_id: str,
        *,
        identity: Identity,
        instance: str | None = None,
    ) -> BrokerJobView:
        if not identity.sub:
            raise JobNotFoundError(job_id, instance=instance)

        async with self._ctx.admin_db.session() as session:
            job = await BrokerJobRepository.get_execution_job_for_actor(
                session, job_id, created_by=identity.sub
            )
            if job is None:
                raise JobNotFoundError(job_id, instance=instance)
            effective_actor_type = _job_actor_type(job.created_by, job.actor_type)
            if effective_actor_type != identity.actor_type.value:
                raise JobNotFoundError(job_id, instance=instance)

            result_available = False
            if job.status in _RESULT_STATUSES:
                result_available = await BrokerJobRepository.has_available_result(
                    session, job.id, now=datetime.now(UTC)
                )

        return self._to_view(
            job,
            actor_type=effective_actor_type,
            result_available=result_available,
        )

    async def get_result(
        self,
        job_id: str,
        *,
        identity: Identity,
        instance: str | None = None,
    ) -> BrokerJobResultView:
        if not identity.sub:
            raise JobNotFoundError(job_id, instance=instance)

        now = datetime.now(UTC)
        async with self._ctx.admin_db.session() as session:
            job = await BrokerJobRepository.get_execution_job_for_actor(
                session, job_id, created_by=identity.sub
            )
            if job is None:
                raise JobNotFoundError(job_id, instance=instance)
            effective_actor_type = _job_actor_type(job.created_by, job.actor_type)
            if effective_actor_type != identity.actor_type.value:
                raise JobNotFoundError(job_id, instance=instance)

            if job.status not in _RESULT_STATUSES:
                raise JobNotCompletedError(job_id, instance=instance)

            result = await BrokerJobRepository.get_result_by_job_id(session, job_id)
            if result is None:
                if job.status == JobStatus.FAILED.value:
                    raise JobNotCompletedError(job_id, instance=instance)
                raise JobResultExpiredError(job_id, instance=instance)

            if result.available_until is not None and result.available_until <= now:
                raise JobResultExpiredError(job_id, instance=instance)

        raw_body: bytes | None = None
        if result.content_type:
            raw_body = json.dumps(result.body).encode() if result.body else b""
        return BrokerJobResultView(
            id=result.id,
            job_id=result.job_id,
            kind=result.kind,
            body=result.body,
            content_type=result.content_type,
            raw_body=raw_body,
            available_until=result.available_until,
            created_at=result.created_at,
        )

    @staticmethod
    def _to_view(
        job: BrokerJobRow,
        *,
        actor_type: str,
        result_available: bool = False,
    ) -> BrokerJobView:
        return BrokerJobView(
            id=job.id,
            kind=job.kind,
            status=job.status,
            parent_job_id=job.parent_job_id,
            execution_id=job.execution_id,
            error=job.error,
            result_available=result_available,
            created_at=job.created_at,
            updated_at=job.updated_at,
            actor_id=job.created_by,
            actor_type=actor_type,
        )
