"""Asyncio worker loop that claims and processes queued jobs."""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import delete, or_, select, update
from sqlalchemy.sql import func

from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos.access_token_repo import AccessTokenRepository
from jentic_one.admin.repos.refresh_token_repo import RefreshTokenRepository
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.db.connectivity import (
    connection_loss_cause,
    describe_connection_loss,
    is_connection_loss,
    redact_sql,
)
from jentic_one.shared.events import emit_event
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.jobs.hold import (
    PROBLEM_CONTENT_TYPE,
    approval_resume_failed_problem,
    expire_lapsed_approvals,
    get_approved_by_job_id,
    set_execution_id_for_job,
)
from jentic_one.shared.jobs.wakeup import JobWakeup, job_wakeup
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.jobs import JobKind, JobStatus

if TYPE_CHECKING:
    from jentic_one.shared.db.session import DatabaseSession

logger = structlog.get_logger(__name__)

# Fallback approved-execution result retention when no config is supplied.
_DEFAULT_APPROVED_RESULT_RETENTION_SECONDS = 86_400
# Post-expiry grace before an access/refresh token row is deleted by the sweep.
# Expired tokens never verify, but keeping them briefly aids introspection and
# refresh-token reuse forensics.
_TOKEN_RETENTION_DAYS = 7
_ERROR_MAX_LEN = 128
# Persisted on the job (and its event) when an attempt is cut short by a lost
# database connection; the details are logged server-side only.
_DB_CONNECTION_LOST_ERROR = "interrupted: database connection lost"


def _backoff_s(attempts: int, cfg: WorkerConfig) -> float:
    """Exponential backoff for the ``attempts``-th claim, capped at the max."""
    raw = cfg.retry_backoff_base_s * (2 ** max(0, attempts - 1))
    return float(min(raw, cfg.retry_backoff_max_s))


# Reconnect backoff while the database is unreachable: base * 2**(n-1), capped,
# then "equal jitter" (a uniform pick in the upper half) so workers sharing a
# database don't retry in lockstep. Base and cap come from
# ``worker.reconnect_backoff_base_s`` / ``worker.reconnect_backoff_max_s``.
def _reconnect_backoff_s(attempt: int, jitter: float, cfg: WorkerConfig) -> float:
    """Delay before reconnect ``attempt`` (1-based); ``jitter`` is in ``[0, 1)``.

    The result lies in ``[capped / 2, capped]`` where
    ``capped = min(base * 2**(attempt-1), max)``, so it never exceeds the cap.
    """
    capped = min(
        cfg.reconnect_backoff_base_s * float(2 ** max(0, attempt - 1)),
        cfg.reconnect_backoff_max_s,
    )
    return capped / 2 + (capped / 2) * jitter


class WorkerLoop:
    """Background worker that claims queued jobs and dispatches to handlers.

    Runs up to ``worker.concurrency`` jobs at once as children of one
    ``TaskGroup``; each claim is atomic (see :meth:`_claim_next`), so several
    loops — in one process or many — can share the queue. It polls every
    ``worker.poll_interval_s`` while idle, re-polls as soon as a running job
    frees a slot, and is woken early by :data:`~jentic_one.shared.jobs.wakeup.job_wakeup`
    when a job is enqueued in the same process.
    """

    def __init__(
        self,
        db: DatabaseSession,
        handler_registry: JobHandlerRegistry,
        *,
        worker_config: WorkerConfig | None = None,
        approved_result_retention_seconds: int = _DEFAULT_APPROVED_RESULT_RETENTION_SECONDS,
        wakeup: JobWakeup | None = None,
    ) -> None:
        self._db = db
        self._handlers = handler_registry
        self._config = worker_config or WorkerConfig()
        self._poll_interval = self._config.poll_interval_s
        self._concurrency = self._config.concurrency
        if self._concurrency > 1 and db.backend.dialect_name == "sqlite":
            # The handler's BEGIN IMMEDIATE transaction spans the upstream
            # call, so a second slot's claim or outcome write waits on that
            # lock past busy_timeout and fails — losing a finished job's
            # outcome and re-running it on reclaim. AppConfig refuses this
            # combination; the clamp covers a loop built without it.
            logger.warning(
                "worker_concurrency_clamped_sqlite",
                requested=self._concurrency,
                effective=1,
            )
            self._concurrency = 1
        self._approved_result_retention_seconds = approved_result_retention_seconds
        self._wakeup = wakeup or job_wakeup
        self._running = False
        self._draining = False
        self._last_sweep = time.monotonic()
        # Set by an in-process enqueue, a finished job freeing a slot, stop()
        # and drain() — anything that makes polling (or exiting) worthwhile
        # before the poll interval is up.
        self._wake = asyncio.Event()
        self._in_flight: set[asyncio.Task[None]] = set()
        # Set while no claim is in flight and no claimed job is being
        # processed — so drain() waits both for running jobs and for a claim
        # that may yet hand it one.
        self._claiming = False
        self._idle = asyncio.Event()
        self._idle.set()

    async def run(self) -> None:
        """Main loop — poll for jobs until stopped or cancelled.

        A single poll must never kill the loop. Per-job failures are contained
        in :meth:`_run_job`, but the poll's own bookkeeping (claiming jobs, the
        retention sweep) issues DB transactions that can raise transient
        errors — most notably during cold start before the database is fully
        reachable. Such an exception escaping ``run`` would terminate the
        worker permanently, leaving every queued job stuck forever. So we catch
        and log non-cancellation errors here and keep polling.

        A lost or unreachable database (see
        :func:`~jentic_one.shared.db.connectivity.is_connection_loss`) is an
        expected condition — e.g. after a laptop's Docker VM sleeps — so it is
        logged as a single warning per outage (traceback at debug only), retried
        with capped, jittered exponential backoff, and reported once at info
        when a poll succeeds again. Any other error keeps the full traceback.

        On :meth:`stop`/:meth:`drain` the loop stops claiming and the
        ``TaskGroup`` waits for in-flight jobs; on cancellation it cancels them
        (they stay ``RUNNING`` and are reclaimed after their visibility timeout).
        """
        self._running = True
        self._wakeup.subscribe(self._wake)
        logger.info(
            "worker_loop_started",
            poll_interval_s=self._poll_interval,
            concurrency=self._concurrency,
        )
        outage_attempts = 0
        outage_started = 0.0
        try:
            async with asyncio.TaskGroup() as tg:
                while self._running:
                    # Clear before polling: a wake-up landing mid-poll stays set
                    # and short-circuits the wait below, so none is lost.
                    self._wake.clear()
                    try:
                        more = await self._poll(tg)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        cause = connection_loss_cause(exc)
                        if cause is None:
                            logger.exception("worker_loop_tick_error")
                            more = False
                            # The database answered (or the failure is unrelated),
                            # so the outage is over; a later drop warns afresh.
                            outage_attempts = 0
                        else:
                            outage_attempts += 1
                            delay_s = _reconnect_backoff_s(
                                outage_attempts, self._jitter(), self._config
                            )
                            if outage_attempts == 1:
                                outage_started = time.monotonic()
                                logger.warning(
                                    "worker_db_connection_lost",
                                    error_type=type(cause).__name__,
                                    error=describe_connection_loss(cause),
                                    retry_in_s=round(delay_s, 2),
                                )
                            logger.debug(
                                "worker_db_reconnect_backoff",
                                attempt=outage_attempts,
                                retry_in_s=round(delay_s, 2),
                                exc_info=True,
                            )
                            await self._sleep(delay_s)
                            continue
                    else:
                        if outage_attempts:
                            logger.info(
                                "worker_db_connection_recovered",
                                failed_attempts=outage_attempts,
                                outage_s=round(time.monotonic() - outage_started, 2),
                            )
                            outage_attempts = 0
                    # Re-poll at once while jobs keep coming; otherwise wait
                    # for a wake-up (enqueue, freed slot, stop) or the interval.
                    if not more:
                        await self._wait_for_wake()
        except asyncio.CancelledError:
            logger.info("worker_loop_cancelled")
            raise
        finally:
            self._wakeup.unsubscribe(self._wake)
            self._running = False
            logger.info("worker_loop_stopped")

    async def _sleep(self, delay_s: float) -> None:
        """Reconnect backoff wait (a seam so tests can observe delays without waiting)."""
        await asyncio.sleep(delay_s)

    def _jitter(self) -> float:
        """Uniform sample in ``[0, 1)`` used to spread reconnect attempts."""
        return random.random()

    async def _poll(self, tg: asyncio.TaskGroup) -> bool:
        """Claim jobs into free slots and start them.

        Returns True when it started jobs without finding the queue empty —
        polling again at once may find more. False means nothing was
        claimable (or no slot was free), so the caller waits for a wake-up.
        """
        await self._maybe_sweep()
        started = 0
        while self._running and not self._draining and len(self._in_flight) < self._concurrency:
            job = await self._claim_tracked()
            if job is None:
                return False
            # Started even if drain()/stop() landed mid-claim: the job is
            # already RUNNING with ``attempts`` bumped, so dropping it here
            # would strand it until reclaim — and an approved execution is
            # refused on reclaim, so its approved call would never run.
            self._start_job(tg, job)
            started += 1
        return started > 0

    async def _claim_tracked(self) -> Any:
        """Claim the next job with the loop marked busy for the whole claim.

        ``_idle`` stays clear from before the claim's first ``await`` until a
        claimed job is tracked in ``_in_flight`` (or the claim found nothing),
        so :meth:`drain` cannot report idle while a claim is in flight.
        """
        self._claiming = True
        self._idle.clear()
        job = None
        try:
            job = await self._claim_next()
            return job
        finally:
            self._claiming = False
            # A claimed job keeps the loop busy until _start_job tracks it;
            # setting _idle here, even briefly, would release a drain() waiter.
            if job is None:
                self._mark_idle_if_quiet()

    def _mark_idle_if_quiet(self) -> None:
        if not self._claiming and not self._in_flight:
            self._idle.set()

    def _start_job(self, tg: asyncio.TaskGroup, job: Any) -> None:
        """Run ``job`` as a child task, tracked for the concurrency bound and drain."""
        self._idle.clear()
        task = tg.create_task(self._run_job(job), name=f"job-{job.id}")
        self._in_flight.add(task)
        task.add_done_callback(self._on_job_done)

    def _on_job_done(self, task: asyncio.Task[None]) -> None:
        self._in_flight.discard(task)
        self._mark_idle_if_quiet()
        # A freed slot is worth an immediate poll.
        self._wake.set()

    async def _run_job(self, job: Any) -> None:
        """Process one job, isolating its failures from siblings and the loop.

        An exception escaping a ``TaskGroup`` child would cancel every other
        in-flight job, so anything :meth:`_process_job` doesn't already handle
        (e.g. a DB error while recording the outcome) is logged here; the job
        stays ``RUNNING`` and is reclaimed after its visibility timeout.
        """
        try:
            await self._process_job(job)
        except Exception as exc:
            cause = connection_loss_cause(exc)
            if cause is None:
                logger.exception("job_processing_error", job_id=job.id)
            else:
                # The database is still unreachable after the attempt failed;
                # the run loop's next poll owns the outage warning and backoff.
                logger.warning(
                    "job_outcome_unrecorded_db_connection_lost",
                    job_id=job.id,
                    error_type=type(cause).__name__,
                    error=describe_connection_loss(cause),
                )

    async def _wait_for_wake(self) -> None:
        """Sleep until woken or the poll interval elapses, whichever is first."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(self._poll_interval):
                await self._wake.wait()

    async def _maybe_sweep(self) -> None:
        """Run the retention sweep once ``worker.sweep_interval_s`` has elapsed.

        The interval restarts only after a sweep returns: a sweep cut short by
        a lost connection re-raises, and the next poll after recovery retries
        it rather than waiting out a full interval.
        """
        now = time.monotonic()
        if now - self._last_sweep < self._config.sweep_interval_s:
            return
        await self._sweep_expired()
        self._last_sweep = now

    async def _tick(self) -> bool:
        """Claim one job and process it inline. Returns True if a job was claimed.

        A single synchronous step of the loop (no concurrency) that drives the
        worker deterministically one job at a time. It shares the claim
        bookkeeping and per-job error handling of :meth:`_poll`.
        """
        await self._maybe_sweep()

        # While draining we stop *claiming* new work.
        if self._draining:
            return False

        job = await self._claim_tracked()
        if job is None:
            return False
        try:
            await self._run_job(job)
        finally:
            self._mark_idle_if_quiet()
        return True

    async def _process_job(self, job: Any) -> None:
        """Dispatch a claimed job to its handler and record the outcome."""
        if await self._refuse_approved_reclaim(job):
            return
        kind = JobKind(job.kind)
        handler = self._handlers.get(kind)
        if handler is None:
            logger.error("job_no_handler", kind=kind, job_id=job.id)
            await self._fail_job(job.id, f"no handler for kind={kind}")
            return

        try:
            result = await self._execute_handler(handler, job)
            await self._complete_job(job.id, job.kind, result)
        except Exception as exc:
            cause = connection_loss_cause(exc)
            if cause is not None:
                # Infrastructure, not the job: no traceback, and only a
                # generic reason is persisted (the raw error carries SQL).
                logger.warning(
                    "job_interrupted_db_connection_lost",
                    job_id=job.id,
                    attempts=job.attempts,
                    error_type=type(cause).__name__,
                    error=describe_connection_loss(cause),
                )
                error = _DB_CONNECTION_LOST_ERROR
            else:
                logger.exception("job_failed", job_id=job.id, attempts=job.attempts)
                error = redact_sql(str(exc)) or type(exc).__name__
            # Same retry contract as any failed attempt: requeue with
            # backoff, dead-letter past the budget. If the database is
            # still unreachable this raises a connection-loss error to the
            # caller (the run loop owns the outage warning and backoff); the
            # job stays RUNNING and is reclaimed after its visibility timeout.
            await self._handle_failure(job, error)

    async def _claim_next(self) -> Any:
        """Claim the oldest claimable job whose kind this worker can handle.

        A job is claimable when it is ``QUEUED`` with no future ``visible_at``
        (respecting exponential-backoff delay on requeued jobs), **or** it is
        ``RUNNING`` but its ``visible_at`` deadline has passed (orphaned by a
        dead worker — recover it). The claim atomically flips it to
        ``RUNNING``, stamps a fresh visibility deadline, and increments
        ``attempts`` so a poison job's retry budget is enforced.
        ``SKIP LOCKED`` keeps concurrent workers from contending on the same row;
        on SQLite (no row locks) the claim runs in a ``BEGIN IMMEDIATE`` write
        transaction, which serialises claimers. Either way a job is handed to
        exactly one claimer, whether it is another slot of this loop or a loop
        in another process.
        """
        supported_kinds = list(self._handlers.kinds)
        if not supported_kinds:
            return None

        now = datetime.now(UTC)
        visible_at = now + timedelta(seconds=self._config.visibility_timeout_s)

        async def _claim(session: Any) -> Any:
            claimable = or_(
                (Job.status == JobStatus.QUEUED)
                & (or_(Job.visible_at.is_(None), Job.visible_at <= now)),
                (Job.status == JobStatus.RUNNING) & (Job.visible_at < now),
            )
            subq = (
                select(Job.id)
                .where(Job.kind.in_(supported_kinds), claimable)
                .order_by(Job.created_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
                .scalar_subquery()
            )
            stmt = (
                update(Job)
                .where(Job.id == subq)
                .values(
                    status=JobStatus.RUNNING,
                    visible_at=visible_at,
                    attempts=Job.attempts + 1,
                )
                .returning(Job)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

        # Use run_in_transaction so a transient "database is locked" (SQLite
        # write contention with request handlers or the import job) is retried
        # with backoff instead of crashing the poll tick. The claim is
        # idempotent across attempts: it either flips one claimable row or
        # matches none.
        return await self._db.run_in_transaction(_claim)

    async def _execute_handler(self, handler: Any, job: Any) -> JobResultPayload:
        """Run the handler within a transaction."""
        async with self._db.transaction() as session:
            result: JobResultPayload = await handler.execute(
                job.id,
                session,
                payload=job.payload,
                created_by=job.created_by,
                actor_type=job.actor_type,
            )
            return result

    async def _is_approved_execution(self, session: Any, job_id: str) -> bool:
        """True when the job is a held execution a reviewer approved."""
        return await get_approved_by_job_id(session, job_id) is not None

    async def _refuse_approved_reclaim(self, job: Any) -> bool:
        """Fail an approved execution re-claimed after its worker died mid-run.

        ``attempts`` counts claims including this one, so above 1 means an
        earlier claim already started the run — the upstream call may have
        happened, and an approved call runs at most once. Returns True when
        the job was failed here instead of dispatched.
        """
        if job.kind != JobKind.EXECUTION or int(job.attempts or 0) <= 1:
            return False
        async with self._db.transaction() as session:
            approval = await get_approved_by_job_id(session, job.id)
            if approval is None:
                return False
            db_job = await session.get(Job, job.id)
            if db_job is None or db_job.status != JobStatus.RUNNING:
                return True
            db_job.status = JobStatus.FAILED
            db_job.error = "approved execution interrupted; not re-run"
            db_job.visible_at = None
            session.add(
                JobResult(
                    job_id=job.id,
                    kind=job.kind,
                    content_type=PROBLEM_CONTENT_TYPE,
                    body=approval_resume_failed_problem(approval.id),
                    available_until=datetime.now(UTC)
                    + timedelta(seconds=self._approved_result_retention_seconds),
                    created_by=db_job.created_by,
                )
            )
        logger.warning("approved_job_reclaim_refused", job_id=job.id, attempts=job.attempts)
        return True

    async def _complete_job(self, job_id: str, kind: str, result: JobResultPayload) -> None:
        """Mark job completed and write result.

        Only an import job emits ``import.completed``: the event (and the spec
        import telemetry derived from it) means a spec was imported, which no
        other job kind does.
        """
        async with self._db.transaction() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            if job.status == JobStatus.CANCELLED:
                return
            job.status = JobStatus.COMPLETED
            job.visible_at = None
            available_until = None
            if kind == JobKind.EXECUTION and await self._is_approved_execution(session, job_id):
                # An approved execution runs once: a failed run (re-auth deny,
                # upstream error) is terminal ``failed`` with its outcome as the
                # result, and the result is kept for the retention window.
                if result.body.get("status") == "failed":
                    job.status = JobStatus.FAILED
                available_until = datetime.now(UTC) + timedelta(
                    seconds=self._approved_result_retention_seconds
                )
                execution_id = result.body.get("execution_id")
                if isinstance(execution_id, str) and execution_id:
                    await set_execution_id_for_job(session, job_id, execution_id)
            job_result = JobResult(
                job_id=job_id,
                kind=kind,
                body=result.body,
                content_type=result.content_type,
                available_until=available_until,
            )
            session.add(job_result)
            if kind != JobKind.IMPORT:
                return
            try:
                await emit_event(
                    session,
                    type=EventType.IMPORT_COMPLETED,
                    severity=EventSeverity.INFO,
                    summary=f"Import completed (job {job_id})",
                    job_id=job_id,
                    created_by=job.created_by,
                    actor_id=job.created_by,
                    actor_type=job.actor_type,
                )
            except Exception:
                logger.warning("emit_event_failed", job_id=job_id)

    async def _handle_failure(self, job: Any, error: str) -> None:
        """Requeue a failed job with backoff, or dead-letter it past the budget.

        ``attempts`` was incremented at claim time, so it is the count of claims
        *including* this one. While it is below ``max_attempts`` the job goes back
        to ``QUEUED`` with a future ``visible_at`` (the backoff delay — the claim
        query won't pick a ``QUEUED`` row before then because requeued jobs carry
        a forward-dated visibility deadline). At/over the budget it is moved to
        ``DEAD_LETTER`` (poison-message handling) instead of looping forever.

        Approved executions never retry: an approval releases one run, so a
        handler failure moves the job straight to ``FAILED``.
        """
        if job.kind == JobKind.EXECUTION:
            async with self._db.session() as check_session:
                is_approved = await self._is_approved_execution(check_session, job.id)
            if is_approved:
                await self._terminal_job(
                    job.id, JobStatus.FAILED, error, event_summary_prefix="Execution failed"
                )
                logger.info("approved_job_failed_no_retry", job_id=job.id)
                return

        attempts = int(job.attempts or 0)
        if attempts >= self._config.max_attempts:
            await self._terminal_job(
                job.id,
                JobStatus.DEAD_LETTER,
                error,
                event_summary_prefix="Job dead-lettered",
            )
            logger.error("job_dead_lettered", job_id=job.id, attempts=attempts)
            return

        delay_s = _backoff_s(attempts, self._config)
        requeue_at = datetime.now(UTC) + timedelta(seconds=delay_s)
        async with self._db.transaction() as session:
            db_job = await session.get(Job, job.id)
            if db_job is None or db_job.status == JobStatus.CANCELLED:
                return
            db_job.status = JobStatus.QUEUED
            db_job.error = error[:_ERROR_MAX_LEN]
            db_job.visible_at = requeue_at
        logger.info("job_requeued", job_id=job.id, attempts=attempts, delay_s=round(delay_s, 2))

    async def _fail_job(self, job_id: str, error: str) -> None:
        """Mark a job terminally ``FAILED`` (non-retryable, e.g. no handler)."""
        await self._terminal_job(
            job_id, JobStatus.FAILED, error, event_summary_prefix="Import failed"
        )

    async def _terminal_job(
        self, job_id: str, status: JobStatus, error: str, *, event_summary_prefix: str
    ) -> None:
        """Move a job to a terminal status, clear its claim, and emit an event."""
        async with self._db.transaction() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            job.status = status
            job.error = error[:_ERROR_MAX_LEN] if error else None
            job.visible_at = None
            try:
                sanitized = error[:_ERROR_MAX_LEN] if error else "unknown"
                if status == JobStatus.DEAD_LETTER:
                    event_type = EventType.JOB_FAILED_PERMANENTLY
                else:
                    event_type = EventType.IMPORT_FAILED
                await emit_event(
                    session,
                    type=event_type,
                    severity=EventSeverity.ERROR,
                    summary=f"{event_summary_prefix}: {sanitized}",
                    requires_action=True,
                    job_id=job_id,
                    created_by=job.created_by,
                    actor_id=job.created_by,
                    actor_type=job.actor_type,
                )
            except Exception:
                logger.warning("emit_event_failed", job_id=job_id)

    async def _sweep_expired(self) -> None:
        """Periodically expire lapsed approvals, remove expired job results, and
        purge long-expired token rows."""
        try:
            # Pending approvals past their TTL expire and fail their held jobs
            # with a permission-denied result, each pair in one transaction.
            async with self._db.transaction() as session:
                expired = await expire_lapsed_approvals(session)
            if expired:
                logger.info("approval_expiry_sweep_expired", count=expired)

            async with self._db.transaction() as session:
                stmt = delete(JobResult).where(
                    JobResult.available_until.is_not(None),
                    JobResult.available_until < func.now(),
                )
                result = await session.execute(stmt)
                count = int(result.rowcount)  # type: ignore[attr-defined]
                if count > 0:
                    logger.info("retention_sweep_removed", count=count)

            # Expired access/refresh tokens are dead weight: they can never
            # verify again, so keep only a short post-expiry grace window (for
            # introspection/debugging and refresh-reuse forensics) and then
            # delete the rows — otherwise they accumulate forever (#610 audit).
            cutoff = datetime.now(UTC) - timedelta(days=_TOKEN_RETENTION_DAYS)
            async with self._db.transaction() as session:
                access_removed = await AccessTokenRepository.delete_expired(session, before=cutoff)
                refresh_removed = await RefreshTokenRepository.delete_expired(
                    session, before=cutoff
                )
                if access_removed or refresh_removed:
                    logger.info(
                        "token_retention_sweep_removed",
                        access_tokens=access_removed,
                        refresh_tokens=refresh_removed,
                    )
        except Exception as exc:
            # A lost connection is the run loop's to report (once per outage);
            # logging it here too would add a traceback per sweep.
            if is_connection_loss(exc):
                raise
            logger.exception("retention_sweep_failed")

    def stop(self) -> None:
        """Signal the worker to stop claiming; ``run`` returns once in-flight jobs finish."""
        self._running = False
        self._wake.set()

    async def drain(self, timeout_s: float | None = None) -> bool:
        """Stop claiming new jobs and wait for every in-flight job to finish.

        Flips into draining mode so the loop stops claiming immediately, then
        waits (bounded by ``timeout_s``, default ``WorkerConfig.drain_timeout_s``)
        for all jobs already being processed to reach a terminal/requeued state.
        A claim already in flight when drain starts counts as busy: the job it
        returns is started and waited for like any other in-flight job.
        Returns ``True`` if the worker is idle by the deadline. If the deadline
        passes with jobs still running, the caller stops the loop anyway — those
        jobs stay ``RUNNING`` and are reclaimed via their visibility timeout
        after restart (an approved execution is failed on reclaim instead of
        re-run), and their ids are logged with ``worker_drain_timeout``.
        """
        self._draining = True
        self._wake.set()
        budget = self._config.drain_timeout_s if timeout_s is None else timeout_s
        try:
            async with asyncio.timeout(budget):
                await self._idle.wait()
            drained = True
        except TimeoutError:
            logger.warning(
                "worker_drain_timeout",
                timeout_s=budget,
                in_flight=len(self._in_flight),
                job_tasks=sorted(task.get_name() for task in self._in_flight),
                claim_in_flight=self._claiming,
            )
            drained = False
        self._running = False
        self._wake.set()
        return drained
