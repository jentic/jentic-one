"""Integration tests for worker job durability (§09 E4.2) against a real DB.

Covers the visibility-timeout recovery, retry-with-backoff requeue, dead-letter
budget, and that a healthy claim stamps a future visibility deadline + attempts.
Per the repo's no-DB-mocking rule these run against the real admin DB.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.jobs import Job
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.jobs.errors import RetryLaterJobError, TerminalJobError
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models import JobKind, JobStatus

pytestmark = pytest.mark.integration


class _OkHandler:
    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        return JobResultPayload(body={"ok": True})


class _BoomHandler:
    """Always fails — drives the retry/dead-letter path."""

    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        raise RuntimeError("handler boom")


def _registry(handler: Any) -> JobHandlerRegistry:
    reg = JobHandlerRegistry()
    reg.register(JobKind.EXECUTION, handler)
    return reg


@pytest.fixture()
async def clean_jobs(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.session() as session:
        await session.execute(delete(Job))
        await session.commit()
    yield
    async with admin_db.session() as session:
        await session.execute(delete(Job))
        await session.commit()


async def _insert_job(admin_db: DatabaseSession, **kwargs: Any) -> str:
    kwargs.setdefault("status", JobStatus.QUEUED)
    job = Job(kind=JobKind.EXECUTION, **kwargs)
    async with admin_db.session() as session:
        session.add(job)
        await session.commit()
        return job.id


async def _get(admin_db: DatabaseSession, job_id: str) -> Job:
    async with admin_db.session() as session:
        result = await session.execute(select(Job).where(Job.id == job_id))
        return result.scalar_one()


async def test_claim_stamps_visibility_and_attempts(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A healthy claim flips QUEUED→RUNNING, stamps visible_at, attempts=1."""
    job_id = await _insert_job(admin_db)
    worker = WorkerLoop(admin_db, _registry(_OkHandler()), worker_config=WorkerConfig())

    claimed = await worker._claim_next()

    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.status == JobStatus.RUNNING
    assert claimed.attempts == 1
    assert claimed.visible_at is not None


async def test_stale_running_job_is_reclaimed(admin_db: DatabaseSession, clean_jobs: None) -> None:
    """A RUNNING job whose visible_at has passed (dead worker) is claimable again."""
    past = datetime.now(UTC) - timedelta(seconds=1)
    job_id = await _insert_job(admin_db, status=JobStatus.RUNNING, visible_at=past, attempts=1)
    worker = WorkerLoop(admin_db, _registry(_OkHandler()), worker_config=WorkerConfig())

    claimed = await worker._claim_next()

    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.attempts == 2  # re-claim increments


async def test_running_job_within_visibility_is_not_reclaimed(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A RUNNING job still inside its visibility window is invisible (no double-processing)."""
    future = datetime.now(UTC) + timedelta(seconds=300)
    await _insert_job(admin_db, status=JobStatus.RUNNING, visible_at=future, attempts=1)
    worker = WorkerLoop(admin_db, _registry(_OkHandler()), worker_config=WorkerConfig())

    assert await worker._claim_next() is None


async def test_failure_requeues_with_backoff_under_budget(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A failing job under the attempt budget goes back to QUEUED with a future visible_at."""
    job_id = await _insert_job(admin_db)
    cfg = WorkerConfig(max_attempts=3, retry_backoff_base_s=5.0)
    worker = WorkerLoop(admin_db, _registry(_BoomHandler()), worker_config=cfg)

    processed = await worker._tick()

    assert processed is True
    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.QUEUED
    assert job.attempts == 1
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    assert job.visible_at is not None and job.visible_at.replace(tzinfo=None) > now_naive
    assert "boom" in (job.error or "")


async def test_requeued_job_with_future_visible_at_not_claimable(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A QUEUED job with a future visible_at (backoff delay) must not be claimed."""
    future = datetime.now(UTC) + timedelta(seconds=60)
    await _insert_job(admin_db, status=JobStatus.QUEUED, visible_at=future, attempts=1)
    worker = WorkerLoop(admin_db, _registry(_OkHandler()), worker_config=WorkerConfig())

    assert await worker._claim_next() is None


async def test_failure_dead_letters_at_budget(admin_db: DatabaseSession, clean_jobs: None) -> None:
    """Once attempts reaches max_attempts a failing job is dead-lettered, not requeued."""
    # attempts=2 in the DB; the claim bumps it to 3 which == max_attempts.
    job_id = await _insert_job(admin_db, attempts=2)
    cfg = WorkerConfig(max_attempts=3)
    worker = WorkerLoop(admin_db, _registry(_BoomHandler()), worker_config=cfg)

    await worker._tick()

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.DEAD_LETTER
    assert job.visible_at is None


async def test_completion_clears_visibility(admin_db: DatabaseSession, clean_jobs: None) -> None:
    """A completed job clears its visibility deadline so it is never reclaimed."""
    job_id = await _insert_job(admin_db)
    worker = WorkerLoop(admin_db, _registry(_OkHandler()), worker_config=WorkerConfig())

    await worker._tick()

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.COMPLETED
    assert job.visible_at is None


class _RaiseHandler:
    """Raises the given exception — drives the handler-steered retry paths."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        raise self._exc


async def test_terminal_job_error_fails_without_retry(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A ``TerminalJobError`` fails the job at once, with budget left."""
    job_id = await _insert_job(admin_db)
    cfg = WorkerConfig(max_attempts=5)
    worker = WorkerLoop(
        admin_db, _registry(_RaiseHandler(TerminalJobError("spec is gone"))), worker_config=cfg
    )

    await worker._tick()

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.FAILED
    assert job.attempts == 1
    assert "spec is gone" in (job.error or "")


async def test_retry_later_job_error_waits_at_least_its_retry_after(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A ``RetryLaterJobError`` requeues no sooner than ``retry_after_s``."""
    job_id = await _insert_job(admin_db)
    cfg = WorkerConfig(max_attempts=3, retry_backoff_base_s=1.0)
    exc = RetryLaterJobError("source throttled", retry_after_s=120.0)
    worker = WorkerLoop(admin_db, _registry(_RaiseHandler(exc)), worker_config=cfg)

    await worker._tick()

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.QUEUED
    assert job.visible_at is not None
    wait = job.visible_at.replace(tzinfo=None) - datetime.now(UTC).replace(tzinfo=None)
    assert wait > timedelta(seconds=100)
