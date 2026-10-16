"""Integration tests: concurrent job claiming and in-process wake-up on a real DB.

Runs on both backends (``JENTIC_TEST_BACKEND=postgres|sqlite``). Pins that a
job is claimed by exactly one claimer — whether the claimers are slots of one
``WorkerLoop`` or loops on separate connection pools standing in for separate
processes — and that an in-process ``enqueue_job`` wakes a worker parked on a
long poll interval as soon as the enqueuing transaction commits.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.shared.config import AppConfig, WorkerConfig
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.jobs.enqueue import enqueue_job
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.jobs.wakeup import JobWakeup, job_wakeup
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models import JobKind, JobStatus

pytestmark = pytest.mark.integration

# Upper bound for "promptly"; tests wait on events, never fixed sleeps.
_PROMPT_S = 10.0


class _RecordingHandler:
    """Records every job id it executes; optionally rendezvous on a barrier."""

    def __init__(self, *, barrier: asyncio.Barrier | None = None, expected: int = 0) -> None:
        self.seen: list[str] = []
        self.all_seen = asyncio.Event()
        self._barrier = barrier
        self._expected = expected

    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        if self._barrier is not None:
            # Only passes once ``parties`` handlers run at the same time, so a
            # worker that serialised jobs would time out here.
            await asyncio.wait_for(self._barrier.wait(), timeout=_PROMPT_S)
        self.seen.append(job_id)
        if self._expected and len(self.seen) >= self._expected:
            self.all_seen.set()
        return JobResultPayload(body={"ok": True})


def _registry(handler: Any) -> JobHandlerRegistry:
    reg = JobHandlerRegistry()
    reg.register(JobKind.EXECUTION, handler)
    return reg


async def _purge(admin_db: DatabaseSession) -> None:
    async with admin_db.transaction() as session:
        await session.execute(delete(JobResult))
        await session.execute(delete(Job))


@pytest.fixture()
async def clean_jobs(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    await _purge(admin_db)
    yield
    await _purge(admin_db)


@pytest.fixture()
async def second_admin_db(
    integration_config: AppConfig,
) -> AsyncGenerator[DatabaseSession, None]:
    """A second admin connection pool — a stand-in for another worker process."""
    db = DatabaseSession(integration_config.databases.admin)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


async def _insert_jobs(admin_db: DatabaseSession, count: int, **kwargs: Any) -> list[str]:
    kwargs.setdefault("status", JobStatus.QUEUED)
    jobs = [Job(kind=JobKind.EXECUTION, **kwargs) for _ in range(count)]
    async with admin_db.transaction() as session:
        session.add_all(jobs)
        await session.flush()
        return [j.id for j in jobs]


async def _statuses(admin_db: DatabaseSession, ids: list[str]) -> Counter[str]:
    async with admin_db.session() as session:
        rows = await session.execute(select(Job.status).where(Job.id.in_(ids)))
        return Counter(str(s) for s in rows.scalars())


def _is_sqlite(db: DatabaseSession) -> bool:
    return db.backend.dialect_name == "sqlite"


async def _stop(worker: WorkerLoop, task: asyncio.Task[None]) -> None:
    worker.stop()
    await asyncio.wait_for(task, timeout=_PROMPT_S)


async def test_racing_claims_hand_each_job_to_exactly_one_claimer(
    admin_db: DatabaseSession, second_admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """Concurrent claims from two pools never return the same job twice."""
    ids = await _insert_jobs(admin_db, 12)
    handler = _RecordingHandler()
    loops = [
        WorkerLoop(db, _registry(handler), worker_config=WorkerConfig(), wakeup=JobWakeup())
        for db in (admin_db, second_admin_db)
    ]

    claims = await asyncio.gather(*(loops[i % 2]._claim_next() for i in range(20)))

    claimed = [job.id for job in claims if job is not None]
    assert len(claimed) == len(set(claimed)), "a job was claimed twice"
    assert sorted(claimed) == sorted(ids)
    assert all(job.attempts == 1 for job in claims if job is not None)


async def test_concurrency_runs_n_jobs_in_parallel(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """``worker.concurrency=N`` runs N handlers at once (they rendezvous on a barrier)."""
    if _is_sqlite(admin_db):
        pytest.skip(
            "SQLite serialises writers and the handler runs inside a write "
            "transaction, so jobs cannot overlap on this backend"
        )
    concurrency = 4
    ids = await _insert_jobs(admin_db, concurrency * 2)
    handler = _RecordingHandler(barrier=asyncio.Barrier(concurrency), expected=len(ids))
    worker = WorkerLoop(
        admin_db,
        _registry(handler),
        worker_config=WorkerConfig(concurrency=concurrency, poll_interval_s=60.0),
        wakeup=JobWakeup(),
    )
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(handler.all_seen.wait(), timeout=_PROMPT_S)
    finally:
        await _stop(worker, task)

    assert sorted(handler.seen) == sorted(ids)
    assert await _statuses(admin_db, ids) == Counter({str(JobStatus.COMPLETED): len(ids)})


async def test_two_concurrent_loops_process_every_job_exactly_once(
    admin_db: DatabaseSession, second_admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """Two multi-slot loops on separate pools (separate processes) share one queue safely."""
    ids = await _insert_jobs(admin_db, 24)
    handler = _RecordingHandler(expected=len(ids))
    cfg = WorkerConfig(concurrency=3, poll_interval_s=0.05)
    loops = [
        WorkerLoop(db, _registry(handler), worker_config=cfg, wakeup=JobWakeup())
        for db in (admin_db, second_admin_db)
    ]
    tasks = [asyncio.create_task(loop.run()) for loop in loops]
    try:
        await asyncio.wait_for(handler.all_seen.wait(), timeout=_PROMPT_S * 3)
    finally:
        for loop, task in zip(loops, tasks, strict=True):
            await _stop(loop, task)

    assert Counter(handler.seen) == Counter(dict.fromkeys(ids, 1))
    assert await _statuses(admin_db, ids) == Counter({str(JobStatus.COMPLETED): len(ids)})


async def test_concurrent_loop_still_reclaims_visibility_expired_job(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A RUNNING job orphaned past its visibility deadline is reclaimed and completed."""
    past = datetime.now(UTC) - timedelta(seconds=1)
    [orphan] = await _insert_jobs(
        admin_db, 1, status=JobStatus.RUNNING, visible_at=past, attempts=1
    )
    handler = _RecordingHandler(expected=1)
    worker = WorkerLoop(
        admin_db,
        _registry(handler),
        worker_config=WorkerConfig(concurrency=3, poll_interval_s=60.0),
        wakeup=JobWakeup(),
    )
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(handler.all_seen.wait(), timeout=_PROMPT_S)
    finally:
        await _stop(worker, task)

    assert handler.seen == [orphan]
    assert await _statuses(admin_db, [orphan]) == Counter({str(JobStatus.COMPLETED): 1})


async def test_enqueue_wakes_the_worker_only_after_commit(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """``enqueue_job`` signals in-process workers on commit, not at flush."""
    wake = asyncio.Event()
    job_wakeup.subscribe(wake)
    try:
        async with admin_db.transaction() as session:
            await enqueue_job(session, JobKind.EXECUTION, created_by="user-1")
            await asyncio.sleep(0)
            assert not wake.is_set(), "wake-up fired before the job was committed"
        await asyncio.wait_for(wake.wait(), timeout=_PROMPT_S)
    finally:
        job_wakeup.unsubscribe(wake)


async def test_held_enqueue_does_not_wake_the_worker(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A HELD job is not claimable, so enqueuing one arms no wake-up."""
    wake = asyncio.Event()
    job_wakeup.subscribe(wake)
    try:
        async with admin_db.transaction() as session:
            await enqueue_job(
                session, JobKind.EXECUTION, created_by="user-1", status=JobStatus.HELD
            )
        for _ in range(5):
            await asyncio.sleep(0)
        assert not wake.is_set()
    finally:
        job_wakeup.unsubscribe(wake)


async def test_in_process_enqueue_starts_job_well_under_the_poll_interval(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A worker parked on a 60 s poll picks up an in-process enqueue immediately."""
    first_empty_poll = asyncio.Event()

    class _ObservedWorker(WorkerLoop):
        async def _claim_next(self) -> Any:
            job = await super()._claim_next()
            if job is None:
                first_empty_poll.set()
            return job

    handler = _RecordingHandler(expected=1)
    worker = _ObservedWorker(
        admin_db,
        _registry(handler),
        worker_config=WorkerConfig(poll_interval_s=60.0),
    )
    task = asyncio.create_task(worker.run())
    try:
        # The queue is empty, so the worker is now parked on its 60 s wait.
        await asyncio.wait_for(first_empty_poll.wait(), timeout=_PROMPT_S)
        async with admin_db.transaction() as session:
            job_id = await enqueue_job(session, JobKind.EXECUTION, created_by="user-1")
        await asyncio.wait_for(handler.all_seen.wait(), timeout=_PROMPT_S)
    finally:
        await _stop(worker, task)

    assert handler.seen == [job_id]
