"""Job enqueue function — thin wrapper usable from any surface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jentic_one.admin.core.schema.jobs import Job
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.jobs import JobKind, JobStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def enqueue_job(
    session: AsyncSession,
    kind: JobKind,
    *,
    created_by: str,
    actor_type: ActorType = ActorType.USER,
    parent_job_id: str | None = None,
    execution_id: str | None = None,
    payload: dict[str, Any] | None = None,
    status: JobStatus = JobStatus.QUEUED,
) -> str:
    """Create a new queued job and return its ID.

    ``created_by`` is the subject (``identity.sub``) that triggered the job and
    ``actor_type`` its kind; both are persisted on the job row so the deferred
    worker can attribute the audit entry to a real actor.

    ``status`` defaults to QUEUED. The hold path passes HELD so the worker skips
    the job until the approval surface flips it back to QUEUED.
    """
    job: Any = Job(
        kind=kind,
        status=status,
        parent_job_id=parent_job_id,
        execution_id=execution_id,
        payload=dict(payload or {}),
        created_by=created_by,
        actor_type=str(actor_type),
    )
    session.add(job)
    await session.flush()
    return str(job.id)
