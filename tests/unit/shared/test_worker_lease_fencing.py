"""Unit tests for job lease fencing and the lease heartbeat (#1845).

Run against a real file-backed SQLite admin DB (no DB mocking). Each claim's
``attempts`` value is its fencing token: once another claim (or a cancel) has
taken the job, every write the stale claim attempts — completion, failure,
requeue, dead-letter, lease renewal — must match no row and change nothing.
"""

from __future__ import annotations

import asyncio
from collections.abc import MutableMapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog
from pydantic import ValidationError
from sqlalchemy import func, select, update

from jentic_one.admin.core.schema.held_executions import HeldExecution
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos.job_repo import JobRepository
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.db.utils import db_now
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.jobs.worker import WorkerLoop, _heartbeat_interval_s
from jentic_one.shared.models.jobs import JobKind, JobStatus


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


class _ReclaimedMidRunHandler:
    """Simulates another worker reclaiming the job while this handler runs."""

    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        await session.execute(update(Job).where(Job.id == job_id).values(attempts=Job.attempts + 1))
        return JobResultPayload(body={"stale": True})


def _worker(db: DatabaseSession, handler: Any = None, **cfg: Any) -> WorkerLoop:
    registry = JobHandlerRegistry()
    registry.register(JobKind.EXECUTION, handler or _OkHandler())
    return WorkerLoop(db, registry, worker_config=WorkerConfig(**cfg))


async def _insert_job(db: DatabaseSession) -> str:
    async with db.transaction() as session:
        job = Job(kind=JobKind.EXECUTION, status=JobStatus.QUEUED, attempts=0, actor_type="user")
        session.add(job)
        await session.flush()
        return job.id


async def _get(db: DatabaseSession, job_id: str) -> Job:
    async with db.session() as session:
        return (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()


async def _result_count(db: DatabaseSession, job_id: str) -> int:
    async with db.session() as session:
        stmt = select(func.count()).select_from(JobResult).where(JobResult.job_id == job_id)
        return int((await session.execute(stmt)).scalar_one())


async def _set_visible_at(db: DatabaseSession, job_id: str, when: datetime) -> None:
    async with db.transaction() as session:
        await session.execute(update(Job).where(Job.id == job_id).values(visible_at=when))


async def _db_clock(db: DatabaseSession) -> datetime:
    async with db.session() as session:
        return await db_now(session)


async def _stale_and_current_claims(db: DatabaseSession) -> tuple[Any, Any]:
    """Claim a job, let its lease lapse, and reclaim it: (stale claim, current claim)."""
    return await _stale_and_current_claims_of(db, await _insert_job(db))


async def _stale_and_current_claims_of(db: DatabaseSession, job_id: str) -> tuple[Any, Any]:
    """Claim the queued ``job_id``, let its lease lapse, and reclaim it."""
    stale = await _worker(db)._claim_next()
    await _set_visible_at(db, job_id, await _db_clock(db) - timedelta(seconds=5))
    current = await _worker(db)._claim_next()
    assert stale.id == current.id == job_id
    assert (stale.attempts, current.attempts) == (1, 2)
    return stale, current


def _events(logs: list[MutableMapping[str, Any]], name: str) -> list[MutableMapping[str, Any]]:
    return [entry for entry in logs if entry["event"] == name]


@pytest.mark.asyncio
async def test_claim_stamps_lease_on_db_clock(jobs_db: DatabaseSession) -> None:
    """The claim's visibility deadline is the DB clock plus the visibility timeout."""
    job_id = await _insert_job(jobs_db)
    before = await _db_clock(jobs_db)
    claimed = await _worker(jobs_db, visibility_timeout_s=60.0)._claim_next()
    after = await _db_clock(jobs_db)

    assert claimed.id == job_id and claimed.attempts == 1
    lease = (await _get(jobs_db, job_id)).visible_at
    assert lease is not None
    assert before + timedelta(seconds=60) <= lease <= after + timedelta(seconds=60)


@pytest.mark.asyncio
async def test_stale_claim_cannot_complete_reclaimed_job(jobs_db: DatabaseSession) -> None:
    """A stale worker's completion writes nothing; the current claim's still lands."""
    stale, current = await _stale_and_current_claims(jobs_db)
    worker = _worker(jobs_db)

    with structlog.testing.capture_logs() as logs:
        assert await worker._complete_job(stale, JobResultPayload(body={"from": "stale"})) is False

    job = await _get(jobs_db, stale.id)
    assert (job.status, job.attempts) == (JobStatus.RUNNING, 2)
    assert await _result_count(jobs_db, stale.id) == 0
    [lost] = _events(logs, "job_lease_lost")
    assert lost["log_level"] == "warning"
    assert (lost["stage"], lost["claim"], lost["current_claim"]) == ("complete", 1, 2)

    assert await worker._complete_job(current, JobResultPayload(body={"from": "current"})) is True
    async with jobs_db.session() as session:
        result = (
            await session.execute(select(JobResult).where(JobResult.job_id == current.id))
        ).scalar_one()
    assert result.body == {"from": "current"}
    assert (await _get(jobs_db, current.id)).status == JobStatus.COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("max_attempts", "stage"),
    [(5, "requeue"), (1, str(JobStatus.DEAD_LETTER))],
    ids=["requeue", "dead_letter"],
)
async def test_stale_claim_cannot_fail_or_requeue_reclaimed_job(
    jobs_db: DatabaseSession, max_attempts: int, stage: str
) -> None:
    """A stale worker's failure handling neither requeues nor finalises the job."""
    stale, _current = await _stale_and_current_claims(jobs_db)
    lease_before = (await _get(jobs_db, stale.id)).visible_at
    worker = _worker(jobs_db, max_attempts=max_attempts)

    with structlog.testing.capture_logs() as logs:
        await worker._handle_failure(stale, "boom")

    job = await _get(jobs_db, stale.id)
    assert (job.status, job.attempts, job.error) == (JobStatus.RUNNING, 2, None)
    assert job.visible_at == lease_before
    assert [e["stage"] for e in _events(logs, "job_lease_lost")] == [stage]
    assert not _events(logs, "job_requeued")
    assert not _events(logs, "job_dead_lettered")


@pytest.mark.asyncio
async def test_stale_claim_cannot_fail_job_terminally(jobs_db: DatabaseSession) -> None:
    """The non-retryable failure path (e.g. no handler) is fenced too."""
    stale, _current = await _stale_and_current_claims(jobs_db)
    await _worker(jobs_db)._fail_job(stale, "no handler for kind=execution")

    job = await _get(jobs_db, stale.id)
    assert (job.status, job.attempts, job.error) == (JobStatus.RUNNING, 2, None)


@pytest.mark.asyncio
async def test_completion_of_cancelled_job_is_dropped_without_warning(
    jobs_db: DatabaseSession,
) -> None:
    """A job cancelled mid-run keeps its cancel; the drop is logged as a cancel, not a loss."""
    job_id = await _insert_job(jobs_db)
    worker = _worker(jobs_db)
    claimed = await worker._claim_next()
    async with jobs_db.transaction() as session:
        assert await JobRepository.cancel_if_active(session, job_id) is not None

    with structlog.testing.capture_logs() as logs:
        assert await worker._complete_job(claimed, JobResultPayload(body={})) is False

    assert (await _get(jobs_db, job_id)).status == JobStatus.CANCELLED
    assert await _result_count(jobs_db, job_id) == 0
    assert [e["stage"] for e in _events(logs, "job_cancelled_while_running")] == ["complete"]
    assert not _events(logs, "job_lease_lost")


@pytest.mark.asyncio
async def test_tick_drops_result_and_skips_requeue_when_lease_lost_mid_run(
    jobs_db: DatabaseSession,
) -> None:
    """Reclaimed while the handler ran: no result, no requeue, the new claim keeps the job."""
    job_id = await _insert_job(jobs_db)
    worker = _worker(jobs_db, _ReclaimedMidRunHandler())

    with structlog.testing.capture_logs() as logs:
        assert await worker._tick() is True

    job = await _get(jobs_db, job_id)
    assert (job.status, job.attempts) == (JobStatus.RUNNING, 2)
    assert await _result_count(jobs_db, job_id) == 0
    assert [e["stage"] for e in _events(logs, "job_lease_lost")] == ["complete"]
    assert not _events(logs, "job_requeued")
    assert not _events(logs, "job_failed")


@pytest.mark.asyncio
async def test_renew_lease_extends_deadline_on_db_clock(jobs_db: DatabaseSession) -> None:
    """A renewal pushes the deadline to DB-now + timeout; a stale claim renews nothing."""
    stale, current = await _stale_and_current_claims(jobs_db)
    worker = _worker(jobs_db, visibility_timeout_s=60.0)
    lapsed = await _db_clock(jobs_db) - timedelta(seconds=30)
    await _set_visible_at(jobs_db, current.id, lapsed)

    assert await worker._renew_lease(current) is True
    renewed = (await _get(jobs_db, current.id)).visible_at
    assert renewed is not None
    assert renewed >= await _db_clock(jobs_db) + timedelta(seconds=59)

    with structlog.testing.capture_logs() as logs:
        assert await worker._renew_lease(stale) is False
    assert (await _get(jobs_db, current.id)).visible_at == renewed
    assert [e["stage"] for e in _events(logs, "job_lease_lost")] == ["heartbeat"]


def test_heartbeat_interval_is_well_under_visibility_timeout() -> None:
    assert _heartbeat_interval_s(WorkerConfig(visibility_timeout_s=120.0)) == 30.0


@pytest.mark.parametrize("timeout_s", [0.0, -1.0])
def test_visibility_timeout_must_be_positive(timeout_s: float) -> None:
    """A non-positive timeout would make the heartbeat renew in a tight loop."""
    with pytest.raises(ValidationError):
        WorkerConfig(visibility_timeout_s=timeout_s)


@pytest.mark.asyncio
async def test_heartbeat_keeps_renewing_until_cancelled(jobs_db: DatabaseSession) -> None:
    """The heartbeat renews the lease on every beat while it runs."""
    job_id = await _insert_job(jobs_db)
    worker = _worker(jobs_db, visibility_timeout_s=0.2)
    claimed = await worker._claim_next()
    lapsed = await _db_clock(jobs_db) - timedelta(hours=1)

    heartbeat = asyncio.create_task(worker._heartbeat(claimed))
    try:
        for _ in range(2):
            await _set_visible_at(jobs_db, job_id, lapsed)
            for _ in range(100):
                await asyncio.sleep(0.02)
                lease = (await _get(jobs_db, job_id)).visible_at
                if lease is not None and lease > lapsed:
                    break
            assert lease is not None and lease > lapsed + timedelta(minutes=30)
        assert not heartbeat.done()
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


@pytest.mark.asyncio
async def test_heartbeat_stops_once_lease_is_lost(jobs_db: DatabaseSession) -> None:
    """A heartbeat that finds its claim superseded logs the loss once and exits."""
    stale, _current = await _stale_and_current_claims(jobs_db)
    worker = _worker(jobs_db, visibility_timeout_s=0.2)

    with structlog.testing.capture_logs() as logs:
        await asyncio.wait_for(worker._heartbeat(stale), timeout=2.0)

    assert [e["stage"] for e in _events(logs, "job_lease_lost")] == ["heartbeat"]
    assert (await _get(jobs_db, stale.id)).attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "stall"])
async def test_heartbeat_survives_a_failed_renewal(jobs_db: DatabaseSession, failure: str) -> None:
    """A renewal that errors (logged without SQL) or stalls past a beat is retried next beat."""

    class _FlakyRenewWorker(WorkerLoop):
        calls = 0

        async def _renew_lease(self, job: Any) -> bool:
            self.calls += 1
            if self.calls == 1:
                if failure == "stall":
                    await asyncio.Event().wait()
                raise RuntimeError("renew failed [SQL: UPDATE jobs SET visible_at = ?]")
            return False

    worker = _FlakyRenewWorker(
        jobs_db, JobHandlerRegistry(), worker_config=WorkerConfig(visibility_timeout_s=0.2)
    )
    claimed = type("_Claimed", (), {"id": "job_x", "attempts": 1})()

    with structlog.testing.capture_logs() as logs:
        await asyncio.wait_for(worker._heartbeat(claimed), timeout=2.0)

    assert worker.calls == 2
    [failed] = _events(logs, "job_lease_renew_failed")
    assert failed["log_level"] == "warning"
    if failure == "stall":
        assert failed["error_type"] == "TimeoutError"
    else:
        assert failed["error_type"] == "RuntimeError"
        assert failed["error"] == "renew failed"


@pytest.mark.asyncio
async def test_sqlite_runs_handler_without_heartbeat(jobs_db: DatabaseSession) -> None:
    """On SQLite the handler's transaction holds the write lock, so no heartbeat runs."""
    started: list[str] = []

    class _RecordingWorker(WorkerLoop):
        async def _heartbeat(self, job: Any) -> None:
            started.append(job.id)

    registry = JobHandlerRegistry()
    registry.register(JobKind.EXECUTION, _OkHandler())
    worker = _RecordingWorker(jobs_db, registry, worker_config=WorkerConfig())
    job_id = await _insert_job(jobs_db)

    assert worker._renews_leases() is False
    assert await worker._tick() is True
    assert started == []
    assert (await _get(jobs_db, job_id)).status == JobStatus.COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_raises", [False, True], ids=["returns", "raises"])
async def test_heartbeat_runs_alongside_handler_and_is_cancelled_after(
    jobs_db: DatabaseSession, handler_raises: bool
) -> None:
    """Where leases are renewed, the heartbeat starts with the handler and ends with it."""
    seen: list[str] = []

    class _BeatingWorker(WorkerLoop):
        def _renews_leases(self) -> bool:
            return True

        async def _heartbeat(self, job: Any) -> None:
            seen.append("started")
            try:
                await asyncio.Event().wait()
            finally:
                seen.append("cancelled")

    class _YieldingHandler(_OkHandler):
        async def execute(self, job_id: str, session: Any, **kwargs: Any) -> JobResultPayload:
            await asyncio.sleep(0)
            seen.append("handler")
            if handler_raises:
                raise RuntimeError("handler failed")
            return await super().execute(job_id, session, **kwargs)

    registry = JobHandlerRegistry()
    registry.register(JobKind.EXECUTION, _YieldingHandler())
    worker = _BeatingWorker(jobs_db, registry, worker_config=WorkerConfig())
    job_id = await _insert_job(jobs_db)

    assert await worker._tick() is True

    assert seen == ["started", "handler", "cancelled"]
    expected = JobStatus.QUEUED if handler_raises else JobStatus.COMPLETED
    assert (await _get(jobs_db, job_id)).status == expected


@pytest.mark.asyncio
async def test_stale_claim_cannot_refuse_reclaimed_approved_job(jobs_db: DatabaseSession) -> None:
    """The approved-reclaim refusal is fenced: a cancelled claim writes no failure or result."""
    job_id = await _insert_job(jobs_db)
    async with jobs_db.transaction() as session:
        session.add(
            HeldExecution(
                job_id=job_id,
                agent_id="agnt_worker",
                credential_id="cred_worker",
                api_vendor="api.example.com",
                api_name="main",
                api_version="1",
                method="POST",
                path="/v1/x",
                request_fingerprint=job_id.ljust(64, "0"),
                state="approved",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    _first, reclaim = await _stale_and_current_claims_of(jobs_db, job_id)
    async with jobs_db.transaction() as session:
        assert await JobRepository.cancel_if_active(session, job_id) is not None

    with structlog.testing.capture_logs() as logs:
        assert await _worker(jobs_db)._refuse_approved_reclaim(reclaim) is True

    assert (await _get(jobs_db, job_id)).status == JobStatus.CANCELLED
    assert await _result_count(jobs_db, job_id) == 0
    assert [e["stage"] for e in _events(logs, "job_cancelled_while_running")] == ["refuse_reclaim"]
    assert not _events(logs, "approved_job_reclaim_refused")
