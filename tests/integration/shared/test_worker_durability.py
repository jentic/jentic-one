"""Integration tests for worker job durability (§09 E4.2) against a real DB.

Covers the visibility-timeout recovery, retry-with-backoff requeue, dead-letter
budget, that a healthy claim stamps a future visibility deadline + attempts, and
that a failure (or a completion whose commit outcome is unknown) never
overwrites a job this claim no longer holds.
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
from jentic_one.shared.db.errors import DatabaseCommitOutcomeUnknownError
from jentic_one.shared.db.session import DatabaseSession
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


class _CountingOkHandler(_OkHandler):
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, job_id: str, session: Any, **kwargs: Any) -> JobResultPayload:
        self.calls += 1
        return await super().execute(job_id, session, **kwargs)


class _CompletionOutcomeUnknownWorker(WorkerLoop):
    """Its completion raises ``DatabaseCommitOutcomeUnknownError``.

    With ``landed`` the completion is written first, modelling a COMMIT the
    server applied before the connection dropped; without it nothing is written.
    """

    def __init__(self, admin_db: DatabaseSession, handler: Any, *, landed: bool) -> None:
        super().__init__(admin_db, _registry(handler), worker_config=WorkerConfig())
        self._landed = landed

    async def _complete_job(self, job_id: str, kind: str, result: JobResultPayload) -> None:
        if self._landed:
            await super()._complete_job(job_id, kind, result)
        raise DatabaseCommitOutcomeUnknownError("connection is closed")


async def test_completion_outcome_unknown_after_commit_landed_is_not_requeued(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A completion that landed stays COMPLETED and the handler never runs again."""
    job_id = await _insert_job(admin_db)
    handler = _CountingOkHandler()
    worker = _CompletionOutcomeUnknownWorker(admin_db, handler, landed=True)

    assert await worker._tick() is True
    assert await worker._tick() is False

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.COMPLETED
    assert job.visible_at is None
    assert handler.calls == 1


async def test_completion_outcome_unknown_not_landed_is_left_for_reclaim(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A completion that did not land leaves the job RUNNING for the visibility reclaim."""
    job_id = await _insert_job(admin_db)
    handler = _CountingOkHandler()
    worker = _CompletionOutcomeUnknownWorker(admin_db, handler, landed=False)

    assert await worker._tick() is True

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.RUNNING
    assert job.attempts == 1
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    assert job.visible_at is not None and job.visible_at.replace(tzinfo=None) > now_naive
    assert handler.calls == 1


@pytest.mark.parametrize("attempts", [1, 3], ids=["requeue", "dead_letter"])
async def test_failure_never_overwrites_a_completed_job(
    admin_db: DatabaseSession, clean_jobs: None, attempts: int
) -> None:
    """Requeue and dead-letter apply only while the job is still RUNNING."""
    job_id = await _insert_job(admin_db, status=JobStatus.COMPLETED, attempts=attempts)
    worker = WorkerLoop(
        admin_db, _registry(_OkHandler()), worker_config=WorkerConfig(max_attempts=3)
    )
    claimed = await _get(admin_db, job_id)

    await worker._handle_failure(claimed, "interrupted: database connection lost")

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.COMPLETED
    assert job.error is None


@pytest.mark.parametrize("attempts", [1, 3], ids=["requeue", "dead_letter"])
async def test_failure_leaves_a_job_reclaimed_by_another_claim(
    admin_db: DatabaseSession, clean_jobs: None, attempts: int
) -> None:
    """A RUNNING row stamped by a later claim (higher attempts) is not this worker's."""
    job_id = await _insert_job(admin_db, status=JobStatus.RUNNING, attempts=attempts + 1)
    worker = WorkerLoop(
        admin_db, _registry(_OkHandler()), worker_config=WorkerConfig(max_attempts=3)
    )
    stale = await _get(admin_db, job_id)
    stale.attempts = attempts

    await worker._handle_failure(stale, "handler boom")

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.RUNNING
    assert job.attempts == attempts + 1
    assert job.error is None
