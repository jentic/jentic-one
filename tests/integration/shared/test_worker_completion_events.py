"""Integration tests for the worker's job-completion event against the real admin DB.

``import.completed`` (and the spec-import telemetry derived from it) means a
spec was imported, so the worker emits it for import jobs only. Execution jobs
complete through the same path and must not produce it.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models import JobKind, JobStatus
from jentic_one.shared.models.events import EventType

pytestmark = pytest.mark.integration

_ACTOR = "usr_worker_events"


class _OkHandler:
    """Completes any job with a fixed result body."""

    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        return JobResultPayload(body={"status": "ok"})


@pytest.fixture()
async def clean_jobs(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _cleanup() -> None:
        async with admin_db.session() as session:
            await session.execute(delete(Event).where(Event.actor_id == _ACTOR))
            await session.execute(delete(JobResult))
            await session.execute(delete(Job))
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


async def _run_one_job(admin_db: DatabaseSession, kind: JobKind) -> str:
    """Queue one job of ``kind``, let the worker complete it, and return its id."""
    job = Job(kind=kind, status=JobStatus.QUEUED, created_by=_ACTOR, actor_type="user")
    async with admin_db.session() as session:
        session.add(job)
        await session.commit()
        job_id = job.id

    registry = JobHandlerRegistry()
    registry.register(kind, _OkHandler())
    worker = WorkerLoop(admin_db, registry, worker_config=WorkerConfig())
    assert await worker._tick() is True

    async with admin_db.session() as session:
        stored = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert stored.status == JobStatus.COMPLETED
    return job_id


async def _event_types_for_job(admin_db: DatabaseSession, job_id: str) -> list[str]:
    async with admin_db.session() as session:
        rows = await session.execute(select(Event.type).where(Event.job_id == job_id))
        return [row[0] for row in rows]


async def test_execution_job_completion_emits_no_import_event(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A completed execution job leaves no ``import.completed`` event."""
    job_id = await _run_one_job(admin_db, JobKind.EXECUTION)

    assert EventType.IMPORT_COMPLETED not in await _event_types_for_job(admin_db, job_id)


async def test_import_job_completion_emits_import_event(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A completed import job emits one ``import.completed`` event, attributed to its actor."""
    job_id = await _run_one_job(admin_db, JobKind.IMPORT)

    async with admin_db.session() as session:
        events = (
            (
                await session.execute(
                    select(Event).where(
                        Event.job_id == job_id,
                        Event.type == EventType.IMPORT_COMPLETED,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert events[0].actor_id == _ACTOR
