"""Integration tests for how a job's retry history shows in its final row.

Pins #1980: a job that fails an attempt and then succeeds reports no error once
``completed``; a handler failure marked non-retryable ends the job ``failed`` on
that attempt with one ``import.failed`` event; any other failure keeps the
requeue-with-backoff budget.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.jobs.handlers import (
    JobHandlerRegistry,
    JobResultPayload,
    NonRetryableJobError,
)
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models import JobKind, JobStatus
from jentic_one.shared.models.events import EventType

pytestmark = pytest.mark.integration

_ACTOR = "usr_worker_retry_outcomes"


class _FailOnceHandler:
    """Fails its first call, then succeeds."""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient hiccup")
        return JobResultPayload(body={"ok": True})


class _RaisingHandler:
    """Raises a fixed exception on every call."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        self.calls += 1
        raise self._exc


@pytest.fixture()
async def clean_jobs(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _clean() -> None:
        async with admin_db.session() as session:
            await session.execute(delete(Event).where(Event.actor_id == _ACTOR))
            await session.execute(delete(JobResult))
            await session.execute(delete(Job))
            await session.commit()

    await _clean()
    yield
    await _clean()


async def _insert_job(admin_db: DatabaseSession, kind: JobKind = JobKind.IMPORT) -> str:
    job = Job(kind=kind, status=JobStatus.QUEUED, created_by=_ACTOR, actor_type="user")
    async with admin_db.session() as session:
        session.add(job)
        await session.commit()
        return job.id


async def _get(admin_db: DatabaseSession, job_id: str) -> Job:
    async with admin_db.session() as session:
        return (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()


async def _make_visible(admin_db: DatabaseSession, job_id: str) -> None:
    """Skip the requeue backoff so the next tick claims the job."""
    past = datetime.now(UTC) - timedelta(seconds=1)
    async with admin_db.transaction() as session:
        await session.execute(update(Job).where(Job.id == job_id).values(visible_at=past))


def _registry(kind: JobKind, handler: Any) -> JobHandlerRegistry:
    reg = JobHandlerRegistry()
    reg.register(kind, handler)
    return reg


async def test_completed_retry_clears_the_failed_attempts_error(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A job that fails once and then succeeds ends ``completed`` with no error."""
    job_id = await _insert_job(admin_db)
    handler = _FailOnceHandler()
    worker = WorkerLoop(admin_db, _registry(JobKind.IMPORT, handler), worker_config=WorkerConfig())

    assert await worker._tick() is True
    requeued = await _get(admin_db, job_id)
    assert requeued.status == JobStatus.QUEUED
    assert requeued.error == "transient hiccup"

    await _make_visible(admin_db, job_id)
    assert await worker._tick() is True

    job = await _get(admin_db, job_id)
    assert handler.calls == 2
    assert job.status == JobStatus.COMPLETED
    assert job.attempts == 2
    assert job.error is None


async def _event_types(admin_db: DatabaseSession, job_id: str) -> list[str]:
    async with admin_db.session() as session:
        rows = await session.execute(select(Event.type).where(Event.job_id == job_id))
        return [str(t) for t in rows.scalars().all()]


@pytest.mark.parametrize(
    ("kind", "expected_event"),
    [
        pytest.param(JobKind.IMPORT, EventType.IMPORT_FAILED, id="import"),
        pytest.param(JobKind.EXECUTION, EventType.JOB_FAILED_PERMANENTLY, id="execution"),
    ],
)
async def test_non_retryable_failure_fails_on_first_attempt(
    admin_db: DatabaseSession, clean_jobs: None, kind: JobKind, expected_event: str
) -> None:
    """A ``NonRetryableJobError`` ends the job ``failed`` at once, with one event."""
    job_id = await _insert_job(admin_db, kind)
    handler = _RaisingHandler(NonRetryableJobError("spec is missing its vendor"))
    worker = WorkerLoop(
        admin_db, _registry(kind, handler), worker_config=WorkerConfig(max_attempts=5)
    )

    assert await worker._tick() is True

    job = await _get(admin_db, job_id)
    assert handler.calls == 1
    assert job.status == JobStatus.FAILED
    assert job.attempts == 1
    assert job.visible_at is None
    assert job.error == "spec is missing its vendor"
    assert await _event_types(admin_db, job_id) == [expected_event]


async def test_other_failure_keeps_the_retry_budget(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """Any exception that is not ``NonRetryableJobError`` is requeued with backoff."""
    job_id = await _insert_job(admin_db)
    handler = _RaisingHandler(RuntimeError("upstream refused"))
    worker = WorkerLoop(
        admin_db, _registry(JobKind.IMPORT, handler), worker_config=WorkerConfig(max_attempts=5)
    )

    assert await worker._tick() is True

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.QUEUED
    assert job.attempts == 1
    assert job.visible_at is not None
    assert await _event_types(admin_db, job_id) == []
