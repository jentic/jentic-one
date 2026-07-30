"""Asyncio worker loop that claims and processes queued jobs."""

from __future__ import annotations

import asyncio
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
from jentic_one.shared.events import emit_event
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.jobs import JobKind, JobStatus

if TYPE_CHECKING:
    from jentic_one.shared.db.session import DatabaseSession

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECONDS = 2.0
_RETENTION_SWEEP_INTERVAL = 60
# Post-expiry grace before an access/refresh token row is deleted by the sweep.
# Expired tokens never verify, but keeping them briefly aids introspection and
# refresh-token reuse forensics.
_TOKEN_RETENTION_DAYS = 7
_ERROR_MAX_LEN = 128


def _backoff_s(attempts: int, cfg: WorkerConfig) -> float:
    """Exponential backoff for the ``attempts``-th claim, capped at the max."""
    raw = cfg.retry_backoff_base_s * (2 ** max(0, attempts - 1))
    return float(min(raw, cfg.retry_backoff_max_s))


class WorkerLoop:
    """Background worker that claims queued jobs and dispatches to handlers."""

    def __init__(
        self,
        db: DatabaseSession,
        handler_registry: JobHandlerRegistry,
        *,
        poll_interval: float = _POLL_INTERVAL_SECONDS,
        worker_config: WorkerConfig | None = None,
    ) -> None:
        self._db = db
        self._handlers = handler_registry
        self._poll_interval = poll_interval
        self._config = worker_config or WorkerConfig()
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
        """
        self._running = True
        logger.info("worker_loop_started")
        try:
            while self._running:
                try:
                    processed = await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("worker_loop_tick_error")
                    processed = False
                if not processed:
                    await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            logger.info("worker_loop_cancelled")
        finally:
            self._running = False
            logger.info("worker_loop_stopped")

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
            kind = JobKind(job.kind)
            handler = self._handlers.get(kind)
            if handler is None:
                logger.error("job_no_handler", kind=kind, job_id=job.id)
                await self._fail_job(job.id, f"no handler for kind={kind}")
                return True

            try:
                result = await self._execute_handler(handler, job)
                await self._complete_job(job.id, job.kind, result)
            except Exception as exc:
                logger.exception("job_failed", job_id=job.id, attempts=job.attempts)
                await self._handle_failure(job, str(exc))
            return True
        finally:
            self._idle.set()

    async def _claim_next(self) -> Any:
        """Claim the oldest claimable job whose kind this worker can handle.

        A job is claimable when it is ``QUEUED`` with no future ``visible_at``
        (respecting exponential-backoff delay on requeued jobs), **or** it is
        ``RUNNING`` but its ``visible_at`` deadline has passed (orphaned by a
        dead worker — recover it, §09 E4.2). The claim atomically flips it to
        ``RUNNING``, stamps a fresh visibility deadline, and increments
        ``attempts`` so a poison job's retry budget is enforced.
        ``SKIP LOCKED`` keeps concurrent workers from contending on the same row.
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

    async def _complete_job(self, job_id: str, kind: str, result: JobResultPayload) -> None:
        """Mark job completed and write result."""
        async with self._db.transaction() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            if job.status == JobStatus.CANCELLED:
                return
            job.status = JobStatus.COMPLETED
            job.visible_at = None
            job_result = JobResult(
                job_id=job_id,
                kind=kind,
                body=result.body,
                content_type=result.content_type,
            )
            session.add(job_result)
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
        """
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
        """Periodically remove expired job results and long-expired token rows."""
        try:
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
        except Exception:
            logger.exception("retention_sweep_failed")

    def stop(self) -> None:
        """Signal the worker to stop."""
        self._running = False

    async def drain(self, timeout_s: float | None = None) -> bool:
        """Stop claiming new jobs and wait for the in-flight job to finish (§09 E4.3).

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
