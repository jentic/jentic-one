"""Job result service."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos import JobRepository, JobResultRepository
from jentic_one.admin.scoping.filters import build_access_filters
from jentic_one.admin.services.errors import (
    JobNotCompletedError,
    JobNotFoundError,
    JobResultExpiredError,
)
from jentic_one.admin.services.schemas.jobs import JobResultView
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import JobStatus


class JobResultService:
    """Manages job result retrieval.

    A result is visible exactly when its parent job is: the job lookup is
    scoped to the caller (``build_access_filters`` on ``Job``), so another
    actor's result is reported as a missing job (``JobNotFoundError`` -> 404).
    """

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def get(self, job_id: str, *, identity: Identity) -> JobResultView:
        access_filters = build_access_filters(identity, Job)
        async with self._ctx.admin_db.session() as session:
            job = await JobRepository.get_by_id(session, job_id, filters=access_filters)
            if job is None:
                raise JobNotFoundError(job_id)

            if job.status != JobStatus.COMPLETED:
                raise JobNotCompletedError(job_id)

            result = await JobResultRepository.get_by_job_id(session, job_id)
            if result is None:
                raise JobResultExpiredError(job_id)

            if result.available_until is not None and result.available_until < datetime.now(UTC):
                raise JobResultExpiredError(job_id)

        return self._to_view(result)

    @staticmethod
    def _to_view(result: Any) -> JobResultView:
        raw_body: bytes | None = None
        if hasattr(result, "content_type") and result.content_type:
            raw_body = json.dumps(result.body).encode() if result.body else b""
        return JobResultView(
            id=result.id,
            job_id=result.job_id,
            kind=result.kind,
            body=result.body,
            content_type=getattr(result, "content_type", None),
            raw_body=raw_body,
            available_until=result.available_until,
            created_at=result.created_at,
        )
