"""Integration tests for job lease fencing and the lease heartbeat (#1845).

A job that outlives its visibility timeout must not be run by a second worker
while its first worker is alive, and a worker whose lease lapsed must not
overwrite the outcome of the claim that superseded it.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import delete, func, select, update

from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.db import db_now
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models import JobKind, JobStatus

pytestmark = pytest.mark.integration

_ON_SQLITE = os.environ.get("JENTIC_TEST_BACKEND", "postgres").lower() == "sqlite"


class _SlowHandler:
    """Runs for ``duration_s`` and counts how many times it was started."""

    def __init__(self, duration_s: float) -> None:
        self.duration_s = duration_s
        self.runs = 0

    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        self.runs += 1
        await asyncio.sleep(self.duration_s)
        return JobResultPayload(body={"run": self.runs})


def _worker(db: DatabaseSession, handler: Any, **cfg: Any) -> WorkerLoop:
    registry = JobHandlerRegistry()
    registry.register(JobKind.EXECUTION, handler)
    return WorkerLoop(db, registry, worker_config=WorkerConfig(**cfg))


@pytest.fixture()
async def clean_jobs(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.transaction() as session:
        await session.execute(delete(Job))
    yield
    async with admin_db.transaction() as session:
        await session.execute(delete(Job))


async def _insert_job(admin_db: DatabaseSession) -> str:
    async with admin_db.transaction() as session:
        job = Job(kind=JobKind.EXECUTION, status=JobStatus.QUEUED)
        session.add(job)
        await session.flush()
        return job.id


async def _get(admin_db: DatabaseSession, job_id: str) -> Job:
    async with admin_db.session() as session:
        return (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()


async def _results(admin_db: DatabaseSession, job_id: str) -> list[JobResult]:
    async with admin_db.session() as session:
        rows = await session.execute(select(JobResult).where(JobResult.job_id == job_id))
        return list(rows.scalars().all())


@pytest.mark.skipif(_ON_SQLITE, reason="SQLite holds the write lock for the run; no heartbeat")
async def test_job_outliving_visibility_timeout_is_not_run_twice(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """The heartbeat keeps a slow job's lease alive, so a second worker never reclaims it."""
    job_id = await _insert_job(admin_db)
    handler = _SlowHandler(duration_s=2.5)
    first = _worker(admin_db, handler, visibility_timeout_s=1.0)
    second = _worker(admin_db, handler, visibility_timeout_s=1.0)

    running = asyncio.create_task(first._tick())
    reclaimed: list[Any] = []
    while not running.done():
        await asyncio.sleep(0.2)
        claimed = await second._claim_next()
        if claimed is not None:
            reclaimed.append(claimed)
    assert await running is True

    assert reclaimed == []
    assert handler.runs == 1
    job = await _get(admin_db, job_id)
    assert (job.status, job.attempts) == (JobStatus.COMPLETED, 1)
    assert [r.body for r in await _results(admin_db, job_id)] == [{"run": 1}]


async def test_stale_worker_cannot_overwrite_newer_claims_result(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """After a lapsed lease is reclaimed and completed, the stale worker writes nothing."""
    job_id = await _insert_job(admin_db)
    handler = _SlowHandler(duration_s=0)
    stale_worker = _worker(admin_db, handler)
    stale = await stale_worker._claim_next()
    async with admin_db.transaction() as session:
        lapsed = await db_now(session) - timedelta(seconds=5)
        await session.execute(update(Job).where(Job.id == job_id).values(visible_at=lapsed))

    current_worker = _worker(admin_db, handler)
    assert await current_worker._tick() is True

    assert await stale_worker._complete_job(stale, JobResultPayload(body={"run": 0})) is False
    await stale_worker._handle_failure(stale, "stale failure")

    job = await _get(admin_db, job_id)
    assert (job.status, job.attempts, job.error) == (JobStatus.COMPLETED, 2, None)
    assert job.visible_at is None
    assert [r.body for r in await _results(admin_db, job_id)] == [{"run": 1}]


async def test_stale_worker_cannot_requeue_job_held_by_newer_claim(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A stale failure leaves the running newer claim (and its lease) untouched."""
    job_id = await _insert_job(admin_db)
    worker = _worker(admin_db, _SlowHandler(duration_s=0), max_attempts=5)
    stale = await worker._claim_next()
    async with admin_db.transaction() as session:
        lapsed = await db_now(session) - timedelta(seconds=5)
        await session.execute(update(Job).where(Job.id == job_id).values(visible_at=lapsed))
    current = await worker._claim_next()
    lease = (await _get(admin_db, job_id)).visible_at

    await worker._handle_failure(stale, "stale failure")

    job = await _get(admin_db, job_id)
    assert (job.status, job.attempts, job.error) == (JobStatus.RUNNING, current.attempts, None)
    assert job.visible_at == lease
    async with admin_db.session() as session:
        count = await session.execute(
            select(func.count()).select_from(JobResult).where(JobResult.job_id == job_id)
        )
        assert count.scalar_one() == 0


async def test_claim_and_renewal_use_database_clock(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """Claim and renewal deadlines are DB-now plus the visibility timeout."""
    job_id = await _insert_job(admin_db)
    worker = _worker(admin_db, _SlowHandler(duration_s=0), visibility_timeout_s=60.0)
    async with admin_db.session() as session:
        before = await db_now(session)
    claimed = await worker._claim_next()
    lease = (await _get(admin_db, job_id)).visible_at
    assert lease is not None and lease >= before + timedelta(seconds=60)

    async with admin_db.transaction() as session:
        lapsed = await db_now(session) - timedelta(seconds=30)
        await session.execute(update(Job).where(Job.id == job_id).values(visible_at=lapsed))
    assert await worker._renew_lease(claimed) is True
    async with admin_db.session() as session:
        now = await db_now(session)
    renewed = (await _get(admin_db, job_id)).visible_at
    assert renewed is not None and renewed >= now + timedelta(seconds=59)
