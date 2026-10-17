"""Asyncio worker loop that claims and processes queued jobs."""

from __future__ import annotations

import asyncio
import random
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlalchemy.sql.elements import ColumnElement

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
from jentic_one.shared.db.utils import db_now
from jentic_one.shared.events import emit_event
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.jobs.hold import (
    PROBLEM_CONTENT_TYPE,
    approval_resume_failed_problem,
    expire_lapsed_approvals,
    get_approved_by_job_id,
    set_execution_id_for_job,
)
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.jobs import JobKind, JobStatus

if TYPE_CHECKING:
    from jentic_one.shared.db.session import DatabaseSession

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECONDS = 2.0
_RETENTION_SWEEP_INTERVAL = 60
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
# A running job's lease is renewed this many times per visibility timeout, so
# several consecutive heartbeats can fail before another worker may reclaim it.
_HEARTBEATS_PER_LEASE = 4


def _heartbeat_interval_s(cfg: WorkerConfig) -> float:
    """Seconds between lease renewals for a running job."""
    return cfg.visibility_timeout_s / _HEARTBEATS_PER_LEASE


def _claim_fence(job_id: str, claim: int) -> tuple[ColumnElement[bool], ...]:
    """Match a job only while it is still held by the claim numbered ``claim``.

    Every claim increments ``attempts`` under the row lock, so the value read
    back from a claim is a fencing token: a later claim of the same job (after
    its lease lapsed) carries a higher number. A write guarded by this predicate
    matches no row once the job has been reclaimed, cancelled, or finalised,
    so a worker that lost its lease can never overwrite another claim's state.
    """
    return (Job.id == job_id, Job.status == JobStatus.RUNNING, Job.attempts == claim)


def _claim_of(job: Any) -> int:
    """The fencing token of a claimed job (its ``attempts`` at claim time)."""
    return int(job.attempts or 0)


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
    """Background worker that claims queued jobs and dispatches to handlers."""

    def __init__(
        self,
        db: DatabaseSession,
        handler_registry: JobHandlerRegistry,
        *,
        poll_interval: float = _POLL_INTERVAL_SECONDS,
        worker_config: WorkerConfig | None = None,
        approved_result_retention_seconds: int = _DEFAULT_APPROVED_RESULT_RETENTION_SECONDS,
    ) -> None:
        self._db = db
        self._handlers = handler_registry
        self._poll_interval = poll_interval
        self._config = worker_config or WorkerConfig()
        self._approved_result_retention_seconds = approved_result_retention_seconds
        self._running = False
        self._draining = False
        self._tick_count = 0
        # Set while a claimed job is being processed, cleared when it reaches a
        # terminal/requeued state — so drain() can wait for the current job.
        self._idle = asyncio.Event()
        self._idle.set()

    async def run(self) -> None:
        """Main loop — poll for jobs until cancelled.

        A single tick must never kill the loop. ``_tick`` already guards
        per-job handler failures, but the surrounding bookkeeping (claiming a
        job, the retention sweep) issues its own DB transactions that can raise
        transient errors — most notably during cold start before the database
        is fully reachable. Such an exception escaping ``run`` would terminate
        the worker permanently, leaving every queued job stuck forever. So we
        catch and log non-cancellation errors here and keep polling.

        A lost or unreachable database (see
        :func:`~jentic_one.shared.db.connectivity.is_connection_loss`) is an
        expected condition — e.g. after a laptop's Docker VM sleeps — so it is
        logged as a single warning per outage (traceback at debug only), retried
        with capped, jittered exponential backoff, and reported once at info
        when a tick succeeds again. Any other error keeps the full traceback.
        """
        self._running = True
        logger.info("worker_loop_started")
        outage_attempts = 0
        outage_started = 0.0
        try:
            while self._running:
                try:
                    processed = await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    cause = connection_loss_cause(exc)
                    if cause is None:
                        logger.exception("worker_loop_tick_error")
                        processed = False
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
                if not processed:
                    await self._sleep(self._poll_interval)
        except asyncio.CancelledError:
            logger.info("worker_loop_cancelled")
        finally:
            self._running = False
            logger.info("worker_loop_stopped")

    async def _sleep(self, delay_s: float) -> None:
        """Wait between ticks (a seam so tests can observe delays without waiting)."""
        await asyncio.sleep(delay_s)

    def _jitter(self) -> float:
        """Uniform sample in ``[0, 1)`` used to spread reconnect attempts."""
        return random.random()

    async def _tick(self) -> bool:
        """Claim one job and process it. Returns True if a job was processed."""
        self._tick_count += 1

        if self._tick_count % _RETENTION_SWEEP_INTERVAL == 0:
            await self._sweep_expired()

        # While draining we stop *claiming* new work, but a tick already in
        # flight (below) is allowed to finish — drain() waits on _idle.
        if self._draining:
            return False

        job = await self._claim_next()
        if job is None:
            return False

        self._idle.clear()
        try:
            if await self._refuse_approved_reclaim(job):
                return True
            kind = JobKind(job.kind)
            handler = self._handlers.get(kind)
            if handler is None:
                logger.error("job_no_handler", kind=kind, job_id=job.id)
                await self._fail_job(job, f"no handler for kind={kind}")
                return True

            try:
                result = await self._execute_handler(handler, job)
                # A lost lease (reclaimed or cancelled meanwhile) drops the
                # result and requeues nothing: the newer claim owns the job.
                await self._complete_job(job, result)
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
                # backoff, dead-letter past the budget — fenced on the claim,
                # so nothing is written once the lease is lost. If the database
                # is still unreachable this raises a connection-loss error to
                # run(), which owns the outage warning and backoff; the job
                # stays RUNNING and is reclaimed after its visibility timeout.
                await self._handle_failure(job, error)
            return True
        finally:
            self._idle.set()

    async def _claim_next(self) -> Any:
        """Claim the oldest claimable job whose kind this worker can handle.

        A job is claimable when it is ``QUEUED`` with no future ``visible_at``
        (respecting exponential-backoff delay on requeued jobs), **or** it is
        ``RUNNING`` but its ``visible_at`` deadline has passed (orphaned by a
        dead worker — recover it). The claim atomically flips it to
        ``RUNNING``, stamps a fresh visibility deadline, and increments
        ``attempts`` so a poison job's retry budget is enforced. The returned
        ``attempts`` is the claim's fencing token (see :func:`_claim_fence`).
        ``SKIP LOCKED`` keeps concurrent workers from contending on the same row.

        Deadlines are read and stamped on the database clock, so workers on
        hosts with skewed clocks agree on when a lease has lapsed.
        """
        supported_kinds = list(self._handlers.kinds)
        if not supported_kinds:
            return None

        timeout = timedelta(seconds=self._config.visibility_timeout_s)

        async def _claim(session: Any) -> Any:
            now = await db_now(session)
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
                    visible_at=now + timeout,
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
        """Run the handler within a transaction, renewing the job's lease meanwhile.

        The lease is renewed only where :meth:`_renews_leases` holds (not on SQLite).
        """
        heartbeat = (
            asyncio.create_task(self._heartbeat(job), name=f"job-lease-heartbeat:{job.id}")
            if self._renews_leases()
            else None
        )
        try:
            async with self._db.transaction() as session:
                result: JobResultPayload = await handler.execute(
                    job.id,
                    session,
                    payload=job.payload,
                    created_by=job.created_by,
                    actor_type=job.actor_type,
                )
                return result
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                # Joins the cancelled heartbeat; a cancellation of this task
                # while waiting still propagates out of gather.
                await asyncio.gather(heartbeat, return_exceptions=True)

    def _renews_leases(self) -> bool:
        """Whether a running job's lease is renewed by a heartbeat.

        Not on SQLite: the handler's write transaction holds SQLite's single
        write lock for the whole run, so a renewal could only wait out the busy
        timeout and fail — and no other worker can write a reclaim meanwhile.
        """
        return bool(self._db.backend.dialect_name != "sqlite")

    async def _heartbeat(self, job: Any) -> None:
        """Renew ``job``'s lease every heartbeat interval until cancelled or lost.

        Runs alongside the handler and is cancelled when it returns. A failed
        renewal (e.g. a transient database error) is logged and retried on the
        next beat; the interval leaves several beats of headroom before the
        lease lapses. Each renewal is bounded by the interval, so one stalled
        renewal (a pool checkout or network stall) costs a single beat rather
        than the remaining lease. A lost lease (the job was reclaimed or
        cancelled) ends the heartbeat; the handler runs on, and its fenced
        completion drops the result.
        """
        interval = _heartbeat_interval_s(self._config)
        while True:
            await asyncio.sleep(interval)
            try:
                async with asyncio.timeout(interval):
                    renewed = await self._renew_lease(job)
            except Exception as exc:
                cause = connection_loss_cause(exc)
                logger.warning(
                    "job_lease_renew_failed",
                    job_id=job.id,
                    claim=_claim_of(job),
                    error_type=type(cause or exc).__name__,
                    error=describe_connection_loss(cause) if cause else redact_sql(str(exc)),
                )
                continue
            if not renewed:
                return

    async def _renew_lease(self, job: Any) -> bool:
        """Push the claim's visibility deadline out by one timeout on the DB clock.

        Returns False (after logging) when the claim no longer holds the job.
        """
        timeout = timedelta(seconds=self._config.visibility_timeout_s)
        async with self._db.transaction() as session:
            now = await db_now(session)
            renewed = await session.execute(
                update(Job)
                .where(*_claim_fence(job.id, _claim_of(job)))
                .values(visible_at=now + timeout)
                .returning(Job.id)
            )
            if renewed.scalar_one_or_none() is not None:
                return True
            await self._log_lease_lost(session, job, stage="heartbeat")
            return False

    async def _log_lease_lost(self, session: AsyncSession, job: Any, *, stage: str) -> None:
        """Log that a fenced write at ``stage`` matched no row, and why.

        A cancelled job is an expected outcome; anything else (reclaimed by a
        newer claim, finalised, or deleted) means this worker's lease lapsed.
        """
        current = (
            await session.execute(select(Job.status, Job.attempts).where(Job.id == job.id))
        ).one_or_none()
        if current is not None and current.status == JobStatus.CANCELLED:
            logger.info("job_cancelled_while_running", job_id=job.id, stage=stage)
            return
        logger.warning(
            "job_lease_lost",
            job_id=job.id,
            stage=stage,
            claim=_claim_of(job),
            current_status=None if current is None else current.status,
            current_claim=None if current is None else current.attempts,
        )

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
            refused = (
                await session.execute(
                    update(Job)
                    .where(*_claim_fence(job.id, _claim_of(job)))
                    .values(
                        status=JobStatus.FAILED,
                        error="approved execution interrupted; not re-run",
                        visible_at=None,
                    )
                    .returning(Job.created_by)
                )
            ).one_or_none()
            if refused is None:
                await self._log_lease_lost(session, job, stage="refuse_reclaim")
                return True
            session.add(
                JobResult(
                    job_id=job.id,
                    kind=job.kind,
                    content_type=PROBLEM_CONTENT_TYPE,
                    body=approval_resume_failed_problem(approval.id),
                    available_until=datetime.now(UTC)
                    + timedelta(seconds=self._approved_result_retention_seconds),
                    created_by=refused.created_by,
                )
            )
        logger.warning("approved_job_reclaim_refused", job_id=job.id, attempts=job.attempts)
        return True

    async def _complete_job(self, job: Any, result: JobResultPayload) -> bool:
        """Mark the claimed job completed and write its result.

        The status write is fenced on the claim: when it matches no row the
        lease was lost (the job was reclaimed, cancelled or finalised), so no
        result is written and False is returned.

        Only an import job emits ``import.completed``: the event (and the spec
        import telemetry derived from it) means a spec was imported, which no
        other job kind does.
        """
        job_id, kind = job.id, job.kind
        async with self._db.transaction() as session:
            status = JobStatus.COMPLETED
            available_until = None
            approved = kind == JobKind.EXECUTION and await self._is_approved_execution(
                session, job_id
            )
            if approved:
                # An approved execution runs once: a failed run (re-auth deny,
                # upstream error) is terminal ``failed`` with its outcome as the
                # result, and the result is kept for the retention window.
                if result.body.get("status") == "failed":
                    status = JobStatus.FAILED
                available_until = datetime.now(UTC) + timedelta(
                    seconds=self._approved_result_retention_seconds
                )
            completed = (
                await session.execute(
                    update(Job)
                    .where(*_claim_fence(job_id, _claim_of(job)))
                    .values(status=status, visible_at=None)
                    .returning(Job.created_by, Job.actor_type)
                )
            ).one_or_none()
            if completed is None:
                await self._log_lease_lost(session, job, stage="complete")
                return False
            if approved:
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
                return True
            try:
                await emit_event(
                    session,
                    type=EventType.IMPORT_COMPLETED,
                    severity=EventSeverity.INFO,
                    summary=f"Import completed (job {job_id})",
                    job_id=job_id,
                    created_by=completed.created_by,
                    actor_id=completed.created_by,
                    actor_type=completed.actor_type,
                )
            except Exception:
                logger.warning("emit_event_failed", job_id=job_id)
        return True

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

        Every write is fenced on the claim, so a worker whose lease was lost
        neither requeues nor finalises a job another claim now holds.
        """
        if job.kind == JobKind.EXECUTION:
            async with self._db.session() as check_session:
                is_approved = await self._is_approved_execution(check_session, job.id)
            if is_approved:
                if await self._terminal_job(
                    job, JobStatus.FAILED, error, event_summary_prefix="Execution failed"
                ):
                    logger.info("approved_job_failed_no_retry", job_id=job.id)
                return

        attempts = int(job.attempts or 0)
        if attempts >= self._config.max_attempts:
            if await self._terminal_job(
                job,
                JobStatus.DEAD_LETTER,
                error,
                event_summary_prefix="Job dead-lettered",
            ):
                logger.error("job_dead_lettered", job_id=job.id, attempts=attempts)
            return

        delay_s = _backoff_s(attempts, self._config)
        async with self._db.transaction() as session:
            requeue_at = await db_now(session) + timedelta(seconds=delay_s)
            requeued = await session.execute(
                update(Job)
                .where(*_claim_fence(job.id, _claim_of(job)))
                .values(
                    status=JobStatus.QUEUED,
                    error=error[:_ERROR_MAX_LEN],
                    visible_at=requeue_at,
                )
                .returning(Job.id)
            )
            if requeued.scalar_one_or_none() is None:
                await self._log_lease_lost(session, job, stage="requeue")
                return
        logger.info("job_requeued", job_id=job.id, attempts=attempts, delay_s=round(delay_s, 2))

    async def _fail_job(self, job: Any, error: str) -> None:
        """Mark a job terminally ``FAILED`` (non-retryable, e.g. no handler)."""
        await self._terminal_job(job, JobStatus.FAILED, error, event_summary_prefix="Import failed")

    async def _terminal_job(
        self, job: Any, status: JobStatus, error: str, *, event_summary_prefix: str
    ) -> bool:
        """Move a claimed job to a terminal status, clear its claim, and emit an event.

        Fenced on the claim; returns False (writing nothing) when the lease was lost.
        """
        job_id = job.id
        async with self._db.transaction() as session:
            finalised = (
                await session.execute(
                    update(Job)
                    .where(*_claim_fence(job_id, _claim_of(job)))
                    .values(
                        status=status,
                        error=error[:_ERROR_MAX_LEN] if error else None,
                        visible_at=None,
                    )
                    .returning(Job.created_by, Job.actor_type)
                )
            ).one_or_none()
            if finalised is None:
                await self._log_lease_lost(session, job, stage=str(status))
                return False
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
                    created_by=finalised.created_by,
                    actor_id=finalised.created_by,
                    actor_type=finalised.actor_type,
                )
            except Exception:
                logger.warning("emit_event_failed", job_id=job_id)
        return True

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
        """Signal the worker to stop."""
        self._running = False

    async def drain(self, timeout_s: float | None = None) -> bool:
        """Stop claiming new jobs and wait for the in-flight job to finish.

        Flips into draining mode so ``_tick`` stops claiming immediately, then
        waits (bounded by ``timeout_s``, default ``WorkerConfig.drain_timeout_s``)
        for any job already being processed to reach a terminal/requeued state.
        Returns ``True`` if the worker is idle by the deadline. If the deadline
        passes with a job still running, the caller stops the loop anyway — the
        job stays ``RUNNING`` and is reclaimed via its visibility timeout after
        restart, so no work is dropped (just delayed).
        """
        self._draining = True
        budget = self._config.drain_timeout_s if timeout_s is None else timeout_s
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=budget)
            drained = True
        except TimeoutError:
            logger.warning("worker_drain_timeout", timeout_s=budget)
            drained = False
        self._running = False
        return drained
