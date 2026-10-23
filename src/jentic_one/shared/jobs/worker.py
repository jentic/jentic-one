"""Asyncio worker loop that claims and processes queued jobs."""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.exc import StatementError
from sqlalchemy.sql import func

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
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
from jentic_one.shared.db.errors import DatabaseCommitOutcomeUnknownError
from jentic_one.shared.events import emit_event, valid_trace_id_or_none
from jentic_one.shared.jobs.handlers import (
    JobHandlerRegistry,
    JobResultPayload,
    NonRetryableJobError,
)
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
# Bounds the exception-chain walk in _carries_sql.
_MAX_CHAIN_DEPTH = 8
# Bounded retries for _complete_job after a handler has already returned.
_COMPLETION_MAX_RETRIES = 3
_COMPLETION_RETRY_BASE_S = 0.05
# Payload marker stamped on a still-running execution job when its handler has
# returned and _complete_job could not persist the result.
_DISPATCHED_PAYLOAD_KEY = "_dispatched"
# Bounds the in-memory fallback set of dispatched job IDs awaiting completion.
_MAX_TRACKED_DISPATCHED_JOBS = 1024


def _carries_sql(exc: BaseException) -> bool:
    """True when a traceback of ``exc`` would render SQL or bound parameters.

    Follows ``__cause__`` and ``__context__``, the links a rendered traceback
    prints. SQLAlchemy's ``StatementError`` (every ``DBAPIError``) appends the
    statement and parameters to its message, and domain database errors built
    from that message carry them too.
    """
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending and len(seen) < _MAX_CHAIN_DEPTH:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, StatementError) or "[SQL:" in str(current):
            return True
        pending.extend(link for link in (current.__cause__, current.__context__) if link)
    return False


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
        self._dispatched_jobs: dict[str, None] = {}
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
        when a poll succeeds again. Any other error keeps the full traceback,
        unless rendering it would print SQL (see :func:`_carries_sql`): then only
        type names and the SQL-free detail are logged.

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
                            if _carries_sql(exc):
                                logger.error(
                                    "worker_loop_tick_error",
                                    error_type=type(exc).__name__,
                                    cause=type(exc.__cause__).__name__ if exc.__cause__ else None,
                                    error=redact_sql(str(exc))[:_ERROR_MAX_LEN],
                                )
                            else:
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
        except DatabaseCommitOutcomeUnknownError as exc:
            self._log_commit_outcome_unknown(job, exc)
            return
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
                error = redact_sql(str(exc)) or type(exc).__name__
                if isinstance(exc, NonRetryableJobError):
                    # The handler judged the failure deterministic: retrying
                    # cannot change it, so the job fails on this attempt. The
                    # handler logs its own detail; this is not a system fault.
                    logger.warning(
                        "job_failed_non_retryable",
                        job_id=job.id,
                        kind=str(kind),
                        attempts=job.attempts,
                        error_type=type(exc).__name__,
                        error=error[:_ERROR_MAX_LEN],
                    )
                    await self._fail_claimed_job(job, kind, error)
                    return
                if _carries_sql(exc):
                    # A traceback would render the chained statement and
                    # its bound parameters, so only type names and the
                    # SQL-free detail are logged.
                    logger.error(
                        "job_failed",
                        job_id=job.id,
                        attempts=job.attempts,
                        error_type=type(exc).__name__,
                        cause=type(exc.__cause__).__name__ if exc.__cause__ else None,
                        error=error[:_ERROR_MAX_LEN],
                    )
                else:
                    logger.exception("job_failed", job_id=job.id, attempts=job.attempts)
            # Same retry contract as any failed attempt: requeue with
            # backoff, dead-letter past the budget. If the database is
            # still unreachable this raises a connection-loss error to the
            # caller (the run loop owns the outage warning and backoff); the
            # job stays RUNNING and is reclaimed after its visibility timeout.
            await self._handle_failure(job, error)
            return

        if kind == JobKind.EXECUTION:
            self._track_dispatched(job.id)
        await self._complete_with_retry(job, result)

    def _track_dispatched(self, job_id: str) -> None:
        self._dispatched_jobs.pop(job_id, None)
        if len(self._dispatched_jobs) >= _MAX_TRACKED_DISPATCHED_JOBS:
            self._dispatched_jobs.pop(next(iter(self._dispatched_jobs)))
        self._dispatched_jobs[job_id] = None

    @staticmethod
    def _log_commit_outcome_unknown(job: Any, exc: DatabaseCommitOutcomeUnknownError) -> None:
        """Log a commit whose connection dropped before the server's reply arrived.

        The write may have landed (for :meth:`_complete_job`: the job is already
        ``COMPLETED``). Requeueing would run the job again, so the row is left
        as is: if the commit did not land it stays ``RUNNING`` and the
        visibility-timeout reclaim picks it up.
        """
        leaf = connection_loss_cause(exc)
        logged_cause = leaf or exc.__cause__ or exc
        logger.warning(
            "job_commit_outcome_unknown",
            job_id=job.id,
            attempts=job.attempts,
            error_type=type(logged_cause).__name__,
            error=(
                describe_connection_loss(leaf)
                if leaf is not None
                else redact_sql(exc.detail)[:_ERROR_MAX_LEN]
            ),
        )

    async def _complete_with_retry(self, job: Any, result: JobResultPayload) -> None:
        """Persist a finished handler's result, retrying transient completion failures.

        Once the handler returns, the job has already run (for an execution job,
        credentials were injected and the upstream call was sent), so
        :meth:`_handle_failure` is never called here: requeuing would run the
        handler a second time. Transient failures in :meth:`_complete_job` are
        retried with bounded exponential backoff. An unknown commit outcome is
        logged once without retry, and a completion that still cannot be written
        after retries leaves the job ``RUNNING`` for visibility-timeout reclaim
        (which refuses to re-run a dispatched execution).
        """
        attempt = 0
        while True:
            try:
                await self._complete_job(job.id, job.kind, result)
                self._dispatched_jobs.pop(job.id, None)
                return
            except DatabaseCommitOutcomeUnknownError as exc:
                await self._mark_dispatched_best_effort(job)
                self._log_commit_outcome_unknown(job, exc)
                return
            except Exception as exc:
                await self._mark_dispatched_best_effort(job)
                if attempt >= _COMPLETION_MAX_RETRIES:
                    self._log_completion_write_failed(job, exc)
                    return
                attempt += 1
                delay_s = _COMPLETION_RETRY_BASE_S * float(2 ** (attempt - 1))
                cause = connection_loss_cause(exc)
                logged_error = (
                    describe_connection_loss(cause)
                    if cause is not None
                    else (redact_sql(str(exc)) or type(exc).__name__)[:_ERROR_MAX_LEN]
                )
                logger.warning(
                    "job_completion_write_retry",
                    job_id=job.id,
                    attempts=job.attempts,
                    retry=attempt,
                    max_retries=_COMPLETION_MAX_RETRIES,
                    retry_in_s=round(delay_s, 3),
                    error_type=type(cause or exc).__name__,
                    error=logged_error,
                )
                await self._sleep(delay_s)

    @staticmethod
    def _log_completion_write_failed(job: Any, exc: BaseException) -> None:
        """Log a completion write that exhausted its retries without leaking SQL."""
        cause = connection_loss_cause(exc)
        if cause is not None:
            logger.warning(
                "job_completion_write_failed",
                job_id=job.id,
                attempts=job.attempts,
                error_type=type(cause).__name__,
                error=describe_connection_loss(cause),
            )
            return
        error = (redact_sql(str(exc)) or type(exc).__name__)[:_ERROR_MAX_LEN]
        if _carries_sql(exc):
            logger.error(
                "job_completion_write_failed",
                job_id=job.id,
                attempts=job.attempts,
                error_type=type(exc).__name__,
                cause=type(exc.__cause__).__name__ if exc.__cause__ else None,
                error=error,
            )
            return
        logger.exception(
            "job_completion_write_failed",
            job_id=job.id,
            attempts=job.attempts,
            error_type=type(exc).__name__,
            error=error,
        )

    async def _mark_dispatched_best_effort(self, job: Any) -> None:
        """Record on a still-running execution job that its handler already ran.

        Best-effort: when the completion write fails for reasons other than a
        dead database (or the database recovers between the completion failure
        and this write), stamping the payload lets a visibility-timeout reclaim
        on another worker process refuse to re-run the job even when the
        handler wrote no execution or event row.
        """
        if getattr(job, "kind", None) != JobKind.EXECUTION:
            return
        raw_payload = getattr(job, "payload", None)
        payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
        if payload.get(_DISPATCHED_PAYLOAD_KEY) is True:
            return
        payload[_DISPATCHED_PAYLOAD_KEY] = True
        with contextlib.suppress(Exception):
            async with self._db.transaction() as session:
                res = await session.execute(
                    update(Job)
                    .where(
                        Job.id == job.id,
                        Job.status == JobStatus.RUNNING,
                        Job.attempts == job.attempts,
                    )
                    .values(payload=payload)
                )
                if res.rowcount:  # type: ignore[attr-defined]
                    job.payload = payload

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
        """Run the handler; it opens its own transactions on the worker's database."""
        result: JobResultPayload = await handler.execute(
            job.id,
            self._db,
            payload=job.payload,
            created_by=job.created_by,
            actor_type=job.actor_type,
        )
        return result

    async def _is_approved_execution(self, session: Any, job_id: str) -> bool:
        """True when the job is a held execution a reviewer approved."""
        return await get_approved_by_job_id(session, job_id) is not None

    async def _refuse_approved_reclaim(self, job: Any) -> bool:
        """Fail an execution job re-claimed after an earlier attempt already started it.

        ``attempts`` counts claims including this one, so above 1 means an
        earlier claim already ran. An approved execution runs at most once even
        if the earlier claim died before dispatch, and its result carries the
        ``approval_resume_failed`` problem body. Any other execution job is
        refused once an earlier attempt already dispatched (or its handler
        returned), so a failed completion write or a mid-call worker crash never
        sends the upstream request or ``credential.accessed`` a second time.
        Returns True when the job was failed here instead of dispatched.
        """
        if job.kind != JobKind.EXECUTION or int(job.attempts or 0) <= 1:
            return False
        async with self._db.transaction() as session:
            db_job = await session.get(Job, job.id, with_for_update=True)
            if (
                db_job is None
                or db_job.status != JobStatus.RUNNING
                or db_job.attempts != job.attempts
            ):
                self._dispatched_jobs.pop(job.id, None)
                return True
            approval = await get_approved_by_job_id(session, job.id)
            if approval is not None:
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
                self._dispatched_jobs.pop(job.id, None)
                logger.warning("approved_job_reclaim_refused", job_id=job.id, attempts=job.attempts)
                return True

            if not await self._has_execution_dispatched(session, db_job):
                return False
            db_job.status = JobStatus.FAILED
            db_job.error = "execution interrupted after dispatch; not re-run"
            db_job.visible_at = None
            self._dispatched_jobs.pop(job.id, None)
        logger.warning("execution_job_reclaim_refused", job_id=job.id, attempts=job.attempts)
        return True

    async def _has_execution_dispatched(self, session: Any, job: Any) -> bool:
        """True when an earlier claim of ``job`` already injected credentials or dispatched."""
        raw_payload = getattr(job, "payload", None)
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        if payload.get(_DISPATCHED_PAYLOAD_KEY) is True or job.id in self._dispatched_jobs:
            return True

        execution_id = (
            getattr(job, "execution_id", None) or payload.get("execution_id") or f"exec_{job.id}"
        )
        trace_id = valid_trace_id_or_none(str(payload.get("trace_id") or ""))

        event_clauses = [Event.job_id == job.id]
        if isinstance(execution_id, str) and execution_id:
            event_clauses.append(Event.execution_id == execution_id)
        if trace_id is not None:
            cred_clause = (Event.trace_id == trace_id) & (
                Event.type == EventType.CREDENTIAL_ACCESSED
            )
            created_by = getattr(job, "created_by", None)
            if isinstance(created_by, str) and created_by:
                cred_clause = cred_clause & (Event.actor_id == created_by)
            created_at = getattr(job, "created_at", None)
            if created_at is not None:
                cred_clause = cred_clause & (Event.created_at >= created_at)
            event_clauses.append(cred_clause)

        predicates = [exists().where(or_(*event_clauses))]
        if isinstance(execution_id, str) and execution_id:
            predicates.insert(0, exists().where(ExecutionRecord.id == execution_id))
        return bool((await session.execute(select(or_(*predicates)))).scalar_one())

    async def _complete_job(self, job_id: str, kind: str, result: JobResultPayload) -> None:
        """Mark job completed and write result.

        Only an import job emits ``import.completed``: the event (and the spec
        import telemetry derived from it) means a spec was imported, which no
        other job kind does.
        """
        async with self._db.transaction() as session:
            job = await session.get(Job, job_id, with_for_update=True)
            if job is None or job.status != JobStatus.RUNNING:
                return
            job.status = JobStatus.COMPLETED
            job.visible_at = None
            # ``error`` holds the last failed attempt's message (written on
            # requeue); a job that finished reports none. An approved execution
            # that ends ``failed`` below carries its outcome in the result.
            job.error = None
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

        Every transition applies only while this worker still holds the claim:
        the row is ``RUNNING`` with the ``attempts`` value stamped by this
        claim. A job that already reached a terminal status (e.g. a completion
        whose commit landed before the connection dropped), was cancelled, or
        was reclaimed by another worker is left untouched.
        """
        if job.kind == JobKind.EXECUTION:
            async with self._db.session() as check_session:
                is_approved = await self._is_approved_execution(check_session, job.id)
            if is_approved:
                if await self._terminal_job(
                    job.id,
                    JobStatus.FAILED,
                    error,
                    event_summary_prefix="Execution failed",
                    claim_attempts=job.attempts,
                ):
                    logger.info("approved_job_failed_no_retry", job_id=job.id)
                return

        attempts = int(job.attempts or 0)
        if attempts >= self._config.max_attempts:
            if await self._terminal_job(
                job.id,
                JobStatus.DEAD_LETTER,
                error,
                event_summary_prefix="Job dead-lettered",
                claim_attempts=job.attempts,
            ):
                logger.error("job_dead_lettered", job_id=job.id, attempts=attempts)
            return

        delay_s = _backoff_s(attempts, self._config)
        requeue_at = datetime.now(UTC) + timedelta(seconds=delay_s)
        async with self._db.transaction() as session:
            result = await session.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.status == JobStatus.RUNNING,
                    Job.attempts == job.attempts,
                )
                .values(
                    status=JobStatus.QUEUED,
                    error=error[:_ERROR_MAX_LEN],
                    visible_at=requeue_at,
                )
            )
        if not result.rowcount:  # type: ignore[attr-defined]
            logger.info("job_requeue_skipped_claim_lost", job_id=job.id, attempts=attempts)
            return
        logger.info("job_requeued", job_id=job.id, attempts=attempts, delay_s=round(delay_s, 2))

    async def _fail_claimed_job(self, job: Any, kind: JobKind, error: str) -> None:
        """Mark a job this claim holds terminally ``FAILED`` without a retry.

        Used for a :class:`NonRetryableJobError`. Like :meth:`_handle_failure`
        the transition applies only while the claim still holds. A failed
        import emits ``import.failed``; any other kind emits
        ``job.failed_permanently``, the event a dead-lettered job carries.
        """
        is_import = kind == JobKind.IMPORT
        if await self._terminal_job(
            job.id,
            JobStatus.FAILED,
            error,
            event_summary_prefix="Import failed" if is_import else "Job failed",
            claim_attempts=job.attempts,
            event_type=EventType.IMPORT_FAILED if is_import else EventType.JOB_FAILED_PERMANENTLY,
        ):
            logger.info("job_failed_no_retry", job_id=job.id, attempts=job.attempts)

    async def _fail_job(self, job_id: str, error: str) -> None:
        """Mark a job terminally ``FAILED`` (non-retryable, e.g. no handler)."""
        await self._terminal_job(
            job_id, JobStatus.FAILED, error, event_summary_prefix="Import failed"
        )

    async def _terminal_job(
        self,
        job_id: str,
        status: JobStatus,
        error: str,
        *,
        event_summary_prefix: str,
        claim_attempts: int | None = None,
        event_type: str | None = None,
    ) -> bool:
        """Move a job to a terminal status, clear its claim, and emit an event.

        With ``claim_attempts`` the transition applies only while the row is
        still ``RUNNING`` under that claim (see :meth:`_handle_failure`); the
        row is locked for the check so a concurrent reclaim cannot interleave.

        ``event_type`` overrides the emitted event; by default ``DEAD_LETTER``
        emits ``job.failed_permanently`` and any other status ``import.failed``.

        Returns True when the transition was applied, False when the job is
        gone or the claim check skipped it.
        """
        async with self._db.transaction() as session:
            if claim_attempts is None:
                job = await session.get(Job, job_id)
            else:
                job = await session.get(Job, job_id, with_for_update=True)
                if job is None or (
                    job.status != JobStatus.RUNNING or job.attempts != claim_attempts
                ):
                    logger.info(
                        "job_terminal_skipped_claim_lost", job_id=job_id, status=str(status)
                    )
                    return False
            if job is None:
                return False
            job.status = status
            job.error = error[:_ERROR_MAX_LEN] if error else None
            job.visible_at = None
            try:
                sanitized = error[:_ERROR_MAX_LEN] if error else "unknown"
                if event_type is None:
                    event_type = (
                        EventType.JOB_FAILED_PERMANENTLY
                        if status == JobStatus.DEAD_LETTER
                        else EventType.IMPORT_FAILED
                    )
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
