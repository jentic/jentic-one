"""Integration tests for worker job durability (§09 E4.2) against a real DB.

Covers the visibility-timeout recovery, retry-with-backoff requeue, dead-letter
budget, that a healthy claim stamps a future visibility deadline + attempts, and
that a failure (or a completion whose commit outcome is unknown) never
overwrites a job this claim no longer holds.
Per the repo's no-DB-mocking rule these run against the real admin DB.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog.testing
from sqlalchemy import delete, select, update
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.db.errors import DatabaseCommitOutcomeUnknownError
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.events import emit_event
from jentic_one.shared.executions import record_execution
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.jobs.worker import _COMPLETION_MAX_RETRIES, WorkerLoop
from jentic_one.shared.models import ExecutionStatus, JobKind, JobStatus
from jentic_one.shared.models.events import EventSeverity, EventType

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
    async def _clean() -> None:
        async with admin_db.session() as session:
            await session.execute(delete(JobResult))
            await session.execute(delete(Event))
            await session.execute(delete(ExecutionRecord))
            await session.execute(delete(Job))
            await session.commit()

    await _clean()
    yield
    await _clean()


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

    with structlog.testing.capture_logs() as logs:
        await worker._handle_failure(claimed, "interrupted: database connection lost")

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.COMPLETED
    assert job.error is None
    _assert_only_skip_logged(logs)


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

    with structlog.testing.capture_logs() as logs:
        await worker._handle_failure(stale, "handler boom")

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.RUNNING
    assert job.attempts == attempts + 1
    assert job.error is None
    _assert_only_skip_logged(logs)


class _ApprovedWorker(WorkerLoop):
    """Treats every execution as approved, so a failure takes the no-retry path."""

    async def _is_approved_execution(self, session: Any, job_id: str) -> bool:
        return True


async def test_approved_failure_skipped_for_a_lost_claim_logs_no_failure(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """An approved job reclaimed elsewhere is untouched and not reported as failed."""
    job_id = await _insert_job(admin_db, status=JobStatus.RUNNING, attempts=2)
    worker = _ApprovedWorker(admin_db, _registry(_OkHandler()), worker_config=WorkerConfig())
    stale = await _get(admin_db, job_id)
    stale.attempts = 1

    with structlog.testing.capture_logs() as logs:
        await worker._handle_failure(stale, "handler boom")

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.RUNNING
    _assert_only_skip_logged(logs)


async def test_dead_letter_under_the_claim_logs_job_dead_lettered(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """When the transition applies, the dead-letter is still reported at error level."""
    job_id = await _insert_job(admin_db, status=JobStatus.RUNNING, attempts=3)
    worker = WorkerLoop(
        admin_db, _registry(_OkHandler()), worker_config=WorkerConfig(max_attempts=3)
    )
    claimed = await _get(admin_db, job_id)

    with structlog.testing.capture_logs() as logs:
        await worker._handle_failure(claimed, "handler boom")

    assert (await _get(admin_db, job_id)).status == JobStatus.DEAD_LETTER
    [entry] = [e for e in logs if e["event"] == "job_dead_lettered"]
    assert entry["log_level"] == "error"
    assert not [e for e in logs if e["event"].endswith("_skipped_claim_lost")]


def _assert_only_skip_logged(logs: list[Any]) -> None:
    """A skipped transition logs one info-level skip line and no failure outcome."""
    events = {e["event"]: e for e in logs}
    for outcome in ("job_dead_lettered", "approved_job_failed_no_retry", "job_requeued"):
        assert outcome not in events
    skips = [e for e in logs if e["event"].endswith("_skipped_claim_lost")]
    assert len(skips) == 1
    assert skips[0]["log_level"] == "info"
    assert all(e["log_level"] != "error" for e in logs)


class _FlakyCompleteWorker(WorkerLoop):
    """Fails ``_complete_job`` ``failures`` times with a pool timeout, then delegates."""

    def __init__(self, admin_db: DatabaseSession, handler: Any, *, failures: int) -> None:
        super().__init__(admin_db, _registry(handler), worker_config=WorkerConfig())
        self._failures = failures
        self.complete_calls = 0
        self.sleeps: list[float] = []

    async def _complete_job(self, job_id: str, kind: str, result: JobResultPayload) -> None:
        self.complete_calls += 1
        if self.complete_calls <= self._failures:
            raise PoolTimeoutError("QueuePool limit reached")
        await super()._complete_job(job_id, kind, result)

    async def _sleep(self, delay_s: float) -> None:
        self.sleeps.append(delay_s)


async def test_completion_write_failure_retries_and_completes_without_rerunning_handler(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """Pins #1800: a transient ``_complete_job`` failure after the handler returns
    is retried in place, writes the ``JobResult``, and never requeues or re-runs
    the handler."""
    job_id = await _insert_job(admin_db)
    handler = _CountingOkHandler()
    worker = _FlakyCompleteWorker(admin_db, handler, failures=2)

    assert await worker._tick() is True
    assert await worker._tick() is False

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.COMPLETED
    assert job.visible_at is None
    assert handler.calls == 1
    assert worker.complete_calls == 3
    assert worker.sleeps == [0.05, 0.1]
    async with admin_db.session() as session:
        result = (
            await session.execute(select(JobResult).where(JobResult.job_id == job_id))
        ).scalar_one()
    assert result.body == {"ok": True}


async def test_completion_write_failure_past_retries_never_requeues_and_reclaim_refuses_rerun(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """Pins #1800: when ``_complete_job`` fails across all retries, the job is
    not requeued, and a visibility-timeout reclaim on a fresh worker refuses to
    re-dispatch the execution."""
    job_id = await _insert_job(admin_db)
    handler = _CountingOkHandler()
    worker = _FlakyCompleteWorker(admin_db, handler, failures=_COMPLETION_MAX_RETRIES + 1)

    assert await worker._tick() is True
    assert await worker._tick() is False

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.RUNNING
    assert job.attempts == 1
    assert handler.calls == 1
    assert worker.complete_calls == _COMPLETION_MAX_RETRIES + 1

    past = datetime.now(UTC) - timedelta(seconds=1)
    async with admin_db.transaction() as session:
        await session.execute(update(Job).where(Job.id == job_id).values(visible_at=past))

    fresh_worker = WorkerLoop(admin_db, _registry(handler), worker_config=WorkerConfig())
    with structlog.testing.capture_logs() as logs:
        assert await fresh_worker._tick() is True

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.FAILED
    assert job.visible_at is None
    assert job.error == "execution interrupted after dispatch; not re-run"
    assert handler.calls == 1
    assert [e for e in logs if e["event"] == "execution_job_reclaim_refused"]


async def test_completion_outcome_unknown_not_landed_is_refused_on_reclaim(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """Pins #1800: a job left RUNNING after an unknown completion commit is
    failed on visibility-timeout reclaim rather than re-dispatched."""
    job_id = await _insert_job(admin_db)
    handler = _CountingOkHandler()
    worker = _CompletionOutcomeUnknownWorker(admin_db, handler, landed=False)

    assert await worker._tick() is True
    assert handler.calls == 1

    past = datetime.now(UTC) - timedelta(seconds=1)
    async with admin_db.transaction() as session:
        await session.execute(update(Job).where(Job.id == job_id).values(visible_at=past))

    fresh_worker = WorkerLoop(admin_db, _registry(handler), worker_config=WorkerConfig())
    assert await fresh_worker._tick() is True

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.FAILED
    assert job.visible_at is None
    assert handler.calls == 1


@pytest.mark.parametrize("signal", ["execution_record", "credential_accessed", "job_event"])
async def test_reclaim_refuses_execution_with_persisted_dispatch_evidence(
    admin_db: DatabaseSession, clean_jobs: None, signal: str
) -> None:
    """Pins #1800: a RUNNING execution job reclaimed after a worker died mid-run
    is refused when the earlier attempt already wrote ``credential.accessed``,
    the ``ExecutionRecord``, or the lifecycle ``Event``."""
    trace_id = "a" * 32
    execution_id = f"exec_{signal[:12]}"
    past = datetime.now(UTC) - timedelta(seconds=1)
    job_id = await _insert_job(
        admin_db,
        status=JobStatus.RUNNING,
        visible_at=past,
        attempts=1,
        execution_id=execution_id,
        payload={"execution_id": execution_id, "trace_id": trace_id},
        created_by="agnt_test",
        actor_type="agent",
    )
    async with admin_db.transaction() as session:
        if signal == "execution_record":
            await record_execution(
                session,
                execution_id=execution_id,
                toolkit_id=None,
                trace_id=trace_id,
                started_at=datetime.now(UTC),
                status=ExecutionStatus.COMPLETED,
                actor_id="agnt_test",
                actor_type="agent",
            )
        elif signal == "credential_accessed":
            await emit_event(
                session,
                type=EventType.CREDENTIAL_ACCESSED,
                severity=EventSeverity.INFO,
                summary="Credential accessed",
                created_by="agnt_test",
                trace_id=trace_id,
                actor_id="agnt_test",
                actor_type="agent",
            )
        else:
            await emit_event(
                session,
                type=EventType.EXECUTION_COMPLETED,
                severity=EventSeverity.INFO,
                summary="Execution completed",
                created_by="agnt_test",
                job_id=job_id,
                execution_id=execution_id,
                actor_id="agnt_test",
                actor_type="agent",
            )

    handler = _CountingOkHandler()
    worker = WorkerLoop(admin_db, _registry(handler), worker_config=WorkerConfig())
    with structlog.testing.capture_logs() as logs:
        assert await worker._tick() is True

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.FAILED
    assert job.visible_at is None
    assert job.error == "execution interrupted after dispatch; not re-run"
    assert handler.calls == 0
    assert [e for e in logs if e["event"] == "execution_job_reclaim_refused"]


async def test_completion_retry_after_commit_landed_is_idempotent(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """Pins #1800: if ``_complete_job`` committed ``COMPLETED`` and ``JobResult``
    on attempt 1 before raising a post-commit error, retry 2 sees
    ``job.status != JobStatus.RUNNING`` and exits cleanly without inserting a
    duplicate ``JobResult`` row."""

    class _LandedThenRaisedWorker(WorkerLoop):
        def __init__(self, db: DatabaseSession, handler: Any) -> None:
            super().__init__(db, _registry(handler), worker_config=WorkerConfig())
            self.complete_calls = 0

        async def _complete_job(self, job_id: str, kind: str, result: JobResultPayload) -> None:
            self.complete_calls += 1
            await super()._complete_job(job_id, kind, result)
            if self.complete_calls == 1:
                raise PoolTimeoutError("post-commit pool error")

        async def _sleep(self, delay_s: float) -> None:
            return None

    job_id = await _insert_job(admin_db)
    handler = _CountingOkHandler()
    worker = _LandedThenRaisedWorker(admin_db, handler)

    assert await worker._tick() is True

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.COMPLETED
    assert job.visible_at is None
    assert handler.calls == 1
    assert worker.complete_calls == 2


async def test_pre_dispatch_retry_not_poisoned_by_earlier_or_other_actor_credential_accessed(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """Pins #1800: an earlier request in the same W3C trace (before ``job.created_at``)
    or a ``credential.accessed`` event from another actor must not cause a
    pre-dispatch retry (``attempts == 2``) to be refused as already dispatched."""
    trace_id = "b" * 32
    execution_id = "exec_predispatch"
    async with admin_db.transaction() as session:
        await emit_event(
            session,
            type=EventType.CREDENTIAL_ACCESSED,
            severity=EventSeverity.INFO,
            summary="Earlier call in same trace",
            created_by="agnt_test",
            trace_id=trace_id,
            actor_id="agnt_test",
            actor_type="agent",
        )
    future_job_time = datetime.now(UTC) + timedelta(seconds=5)
    job_id = await _insert_job(
        admin_db,
        status=JobStatus.QUEUED,
        attempts=1,
        execution_id=execution_id,
        payload={"execution_id": execution_id, "trace_id": trace_id},
        created_by="agnt_test",
        actor_type="agent",
        created_at=future_job_time,
    )
    async with admin_db.transaction() as session:
        await emit_event(
            session,
            type=EventType.CREDENTIAL_ACCESSED,
            severity=EventSeverity.INFO,
            summary="Other actor with same trace_id",
            created_by="agnt_other",
            trace_id=trace_id,
            actor_id="agnt_other",
            actor_type="agent",
        )

    handler = _CountingOkHandler()
    worker = WorkerLoop(admin_db, _registry(handler), worker_config=WorkerConfig())
    assert await worker._tick() is True

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.COMPLETED
    assert job.attempts == 2
    assert handler.calls == 1


async def test_reclaim_refusal_skips_stale_claim_attempts_mismatch(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """Pins #1800: a stale worker holding ``attempts=2`` in ``_refuse_approved_reclaim``
    must not fail a job that has already been reclaimed under ``attempts=3``."""
    execution_id = "exec_stale_reclaim"
    job_id = await _insert_job(
        admin_db,
        status=JobStatus.RUNNING,
        attempts=3,
        execution_id=execution_id,
        payload={"execution_id": execution_id, "_dispatched": True},
    )
    stale = await _get(admin_db, job_id)
    stale.attempts = 2

    worker = WorkerLoop(admin_db, _registry(_CountingOkHandler()), worker_config=WorkerConfig())
    with structlog.testing.capture_logs() as logs:
        assert await worker._refuse_approved_reclaim(stale) is True

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.RUNNING
    assert job.attempts == 3
    assert job.error is None
    assert not [e for e in logs if e["event"] == "execution_job_reclaim_refused"]


async def test_completion_failure_stamps_dispatched_before_backoff_sleep(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """Pins #1800: ``_mark_dispatched_best_effort`` runs on the first completion
    failure before ``_sleep``, so cancelling the worker during backoff still
    leaves ``_dispatched`` on the DB row for a fresh worker's reclaim."""

    class _CancelOnCompletionSleepWorker(_FlakyCompleteWorker):
        async def _sleep(self, delay_s: float) -> None:
            raise asyncio.CancelledError

    job_id = await _insert_job(admin_db)
    handler = _CountingOkHandler()
    worker = _CancelOnCompletionSleepWorker(admin_db, handler, failures=1)

    with pytest.raises(asyncio.CancelledError):
        await worker._tick()

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.RUNNING
    assert job.payload == {"_dispatched": True}
    assert handler.calls == 1

    past = datetime.now(UTC) - timedelta(seconds=1)
    async with admin_db.transaction() as session:
        await session.execute(update(Job).where(Job.id == job_id).values(visible_at=past))

    fresh_worker = WorkerLoop(admin_db, _registry(handler), worker_config=WorkerConfig())
    assert await fresh_worker._tick() is True

    job = await _get(admin_db, job_id)
    assert job.status == JobStatus.FAILED
    assert handler.calls == 1
