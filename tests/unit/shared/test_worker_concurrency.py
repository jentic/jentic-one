"""Unit tests for the WorkerLoop's concurrency, wake-up, and shutdown mechanics.

The claim and per-job processing steps are replaced by an in-memory queue so
these tests pin the loop's scheduling contract — the concurrency bound, error
isolation between jobs, prompt wake-up, and graceful stop/drain/cancel — with
no database. Atomic claiming against real databases is covered by
``tests/integration/shared/test_worker_concurrency.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.jobs.wakeup import JOB_NOTIFY_CHANNEL, JobWakeup, notify_job_enqueued
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models.jobs import JobKind

# Generous upper bound for "this should happen promptly"; never a sleep.
_PROMPT_S = 2.0


@dataclass
class _Job:
    id: str
    kind: str = JobKind.EXECUTION
    attempts: int = 1


class _NoopHandler:
    async def execute(self, job_id: str, db: Any, **_: Any) -> JobResultPayload:
        return JobResultPayload(body={})


class _FakeDb:
    def __init__(self, dialect_name: str = "postgresql") -> None:
        self.backend = SimpleNamespace(dialect_name=dialect_name)


class _ScriptedWorker(WorkerLoop):
    """WorkerLoop whose claim pops an in-memory queue and whose job body is injected."""

    def __init__(
        self,
        body: Callable[[_Job], Awaitable[None]],
        *,
        jobs: list[_Job] | None = None,
        config: WorkerConfig | None = None,
        wakeup: JobWakeup | None = None,
        db: _FakeDb | None = None,
    ) -> None:
        registry = JobHandlerRegistry()
        registry.register(JobKind.EXECUTION, _NoopHandler())
        super().__init__(
            db=db or _FakeDb(),  # type: ignore[arg-type]
            handler_registry=registry,
            worker_config=config or WorkerConfig(),
            wakeup=wakeup or JobWakeup(),
        )
        self.queue: list[_Job] = list(jobs or [])
        self.claims = 0
        self.empty_polls = 0
        self.polled_empty = asyncio.Event()
        self._body = body

    async def _claim_next(self) -> Any:
        self.claims += 1
        if self.queue:
            return self.queue.pop(0)
        self.empty_polls += 1
        self.polled_empty.set()
        return None

    async def _process_job(self, job: Any) -> None:
        await self._body(job)


class _Tracker:
    """Records concurrency and completion of scripted job bodies."""

    def __init__(self, *, full_at: int | None = None) -> None:
        self.current = 0
        self.peak = 0
        self.done: list[str] = []
        self.cancelled: list[str] = []
        self.gate = asyncio.Event()
        self.full = asyncio.Event()
        self.all_done = asyncio.Event()
        self._full_at = full_at
        self.expected = 0

    async def body(self, job: _Job) -> None:
        self.current += 1
        self.peak = max(self.peak, self.current)
        if self._full_at is not None and self.current >= self._full_at:
            self.full.set()
        try:
            await self.gate.wait()
            self.done.append(job.id)
            if len(self.done) >= self.expected:
                self.all_done.set()
        except asyncio.CancelledError:
            self.cancelled.append(job.id)
            raise
        finally:
            self.current -= 1


async def _yield_loop(times: int = 20) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


def test_worker_takes_poll_interval_and_concurrency_from_config() -> None:
    worker = _ScriptedWorker(
        _Tracker().body, config=WorkerConfig(poll_interval_s=0.5, concurrency=4)
    )
    assert worker._poll_interval == 0.5
    assert worker._concurrency == 4


async def test_concurrency_bound_runs_n_jobs_at_once_and_never_more() -> None:
    tracker = _Tracker(full_at=3)
    tracker.expected = 7
    jobs = [_Job(id=f"j{i}") for i in range(7)]
    worker = _ScriptedWorker(
        tracker.body, jobs=jobs, config=WorkerConfig(concurrency=3, poll_interval_s=60.0)
    )
    task = asyncio.create_task(worker.run())

    await asyncio.wait_for(tracker.full.wait(), timeout=_PROMPT_S)
    await _yield_loop()
    # Three slots are busy: the loop must not claim a fourth job.
    assert tracker.current == 3
    assert len(worker.queue) == 4

    tracker.gate.set()
    # Freed slots trigger an immediate re-poll — no 60 s poll wait.
    await asyncio.wait_for(tracker.all_done.wait(), timeout=_PROMPT_S)
    assert sorted(tracker.done) == sorted(j.id for j in jobs)
    assert tracker.peak == 3

    worker.stop()
    await asyncio.wait_for(task, timeout=_PROMPT_S)


async def test_serial_default_runs_one_job_at_a_time() -> None:
    tracker = _Tracker()
    tracker.expected = 3
    tracker.gate.set()
    worker = _ScriptedWorker(
        tracker.body,
        jobs=[_Job(id=f"j{i}") for i in range(3)],
        config=WorkerConfig(poll_interval_s=60.0),
    )
    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(tracker.all_done.wait(), timeout=_PROMPT_S)
    assert tracker.peak == 1
    worker.stop()
    await asyncio.wait_for(task, timeout=_PROMPT_S)


async def test_one_job_error_does_not_cancel_siblings_or_kill_the_loop() -> None:
    finished: list[str] = []
    both_done = asyncio.Event()
    release = asyncio.Event()

    async def body(job: _Job) -> None:
        if job.id == "bad":
            raise RuntimeError("db error while recording outcome")
        await release.wait()
        finished.append(job.id)
        if len(finished) == 2:
            both_done.set()

    worker = _ScriptedWorker(
        body,
        jobs=[_Job(id="bad"), _Job(id="ok1"), _Job(id="ok2")],
        config=WorkerConfig(concurrency=3, poll_interval_s=60.0),
    )
    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(worker.polled_empty.wait(), timeout=_PROMPT_S)
    release.set()
    await asyncio.wait_for(both_done.wait(), timeout=_PROMPT_S)

    assert sorted(finished) == ["ok1", "ok2"]
    assert not task.done()
    worker.stop()
    await asyncio.wait_for(task, timeout=_PROMPT_S)


async def test_wakeup_polls_immediately_instead_of_waiting_out_the_interval() -> None:
    wakeup = JobWakeup()
    ran = asyncio.Event()

    async def body(job: _Job) -> None:
        ran.set()

    worker = _ScriptedWorker(body, config=WorkerConfig(poll_interval_s=60.0), wakeup=wakeup)
    task = asyncio.create_task(worker.run())
    # The first poll found nothing; the loop is now parked on a 60 s wait.
    await asyncio.wait_for(worker.polled_empty.wait(), timeout=_PROMPT_S)

    worker.queue.append(_Job(id="late"))
    wakeup.notify()
    await asyncio.wait_for(ran.wait(), timeout=_PROMPT_S)

    worker.stop()
    await asyncio.wait_for(task, timeout=_PROMPT_S)


async def test_stop_waits_for_in_flight_jobs_to_finish() -> None:
    tracker = _Tracker(full_at=2)
    tracker.expected = 2
    worker = _ScriptedWorker(
        tracker.body,
        jobs=[_Job(id="a"), _Job(id="b"), _Job(id="never")],
        config=WorkerConfig(concurrency=2, poll_interval_s=60.0),
    )
    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(tracker.full.wait(), timeout=_PROMPT_S)

    worker.stop()
    await _yield_loop()
    assert not task.done(), "run() must wait for in-flight jobs after stop()"

    tracker.gate.set()
    await asyncio.wait_for(task, timeout=_PROMPT_S)
    assert sorted(tracker.done) == ["a", "b"]
    assert tracker.cancelled == []
    assert [j.id for j in worker.queue] == ["never"]


async def test_drain_waits_for_every_in_flight_job_and_stops_claiming() -> None:
    tracker = _Tracker(full_at=2)
    tracker.expected = 2
    worker = _ScriptedWorker(
        tracker.body,
        jobs=[_Job(id="a"), _Job(id="b"), _Job(id="queued")],
        config=WorkerConfig(concurrency=2, poll_interval_s=60.0),
    )
    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(tracker.full.wait(), timeout=_PROMPT_S)

    drain = asyncio.create_task(worker.drain(timeout_s=_PROMPT_S))
    await _yield_loop()
    assert not drain.done(), "drain() must wait while jobs are in flight"

    tracker.gate.set()
    assert await asyncio.wait_for(drain, timeout=_PROMPT_S) is True
    await asyncio.wait_for(task, timeout=_PROMPT_S)
    assert sorted(tracker.done) == ["a", "b"]
    # The freed slots during drain must not claim the remaining job.
    assert [j.id for j in worker.queue] == ["queued"]


async def test_drain_timeout_then_cancel_cancels_in_flight_jobs() -> None:
    tracker = _Tracker(full_at=2)
    worker = _ScriptedWorker(
        tracker.body,
        jobs=[_Job(id="a"), _Job(id="b")],
        config=WorkerConfig(concurrency=2, poll_interval_s=60.0),
    )
    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(tracker.full.wait(), timeout=_PROMPT_S)

    with structlog.testing.capture_logs() as logs:
        assert await worker.drain(timeout_s=0.01) is False
    [timeout] = [e for e in logs if e["event"] == "worker_drain_timeout"]
    assert timeout["job_tasks"] == ["job-a", "job-b"]
    task.cancel()
    # run() re-raises cancellation after logging it.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=_PROMPT_S)

    assert sorted(tracker.cancelled) == ["a", "b"]
    assert tracker.done == []
    assert worker._running is False


def test_wakeup_notifies_subscribers_and_ignores_unsubscribed() -> None:
    async def scenario() -> None:
        wakeup = JobWakeup()
        kept, dropped = asyncio.Event(), asyncio.Event()
        wakeup.subscribe(kept)
        wakeup.subscribe(dropped)
        wakeup.unsubscribe(dropped)
        wakeup.notify()
        await asyncio.wait_for(kept.wait(), timeout=_PROMPT_S)
        assert not dropped.is_set()

    asyncio.run(scenario())


def test_wakeup_notify_without_subscribers_is_a_noop() -> None:
    JobWakeup().notify()


@pytest.mark.parametrize("concurrency", [1, 4])
async def test_idle_is_set_after_jobs_finish(concurrency: int) -> None:
    tracker = _Tracker()
    tracker.expected = concurrency
    tracker.gate.set()
    worker = _ScriptedWorker(
        tracker.body,
        jobs=[_Job(id=f"j{i}") for i in range(concurrency)],
        config=WorkerConfig(concurrency=concurrency, poll_interval_s=60.0),
    )
    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(tracker.all_done.wait(), timeout=_PROMPT_S)
    await asyncio.wait_for(worker._idle.wait(), timeout=_PROMPT_S)
    assert worker._in_flight == set()
    worker.stop()
    await asyncio.wait_for(task, timeout=_PROMPT_S)


class _SweepCountingWorker(_ScriptedWorker):
    def __init__(self, config: WorkerConfig) -> None:
        super().__init__(_Tracker().body, config=config)
        self.sweeps = 0

    async def _sweep_expired(self) -> None:
        self.sweeps += 1


async def test_retention_sweep_follows_configured_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sweep runs once ``worker.sweep_interval_s`` of wall time has elapsed."""
    clock = [1000.0]
    monkeypatch.setattr("jentic_one.shared.jobs.worker.time.monotonic", lambda: clock[0])
    worker = _SweepCountingWorker(WorkerConfig(sweep_interval_s=30.0))

    clock[0] += 29.9
    await worker._maybe_sweep()
    assert worker.sweeps == 0

    clock[0] += 0.1
    await worker._maybe_sweep()
    assert worker.sweeps == 1

    # The next sweep is a full interval after the last one, not the boot time.
    clock[0] += 29.0
    await worker._maybe_sweep()
    assert worker.sweeps == 1
    clock[0] += 1.0
    await worker._maybe_sweep()
    assert worker.sweeps == 2


async def test_retention_sweep_default_interval_is_120s(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("jentic_one.shared.jobs.worker.time.monotonic", lambda: clock[0])
    worker = _SweepCountingWorker(WorkerConfig())
    clock[0] = 119.0
    await worker._maybe_sweep()
    assert worker.sweeps == 0
    clock[0] = 120.0
    await worker._maybe_sweep()
    assert worker.sweeps == 1


def test_concurrency_above_one_is_clamped_to_serial_on_sqlite() -> None:
    """A SQLite admin DB runs one job at a time whatever ``concurrency`` asks for.

    The handler's write transaction spans the upstream call, so a second slot
    would wait on the SQLite write lock past ``busy_timeout``.
    """
    with structlog.testing.capture_logs() as logs:
        worker = _ScriptedWorker(
            _Tracker().body, config=WorkerConfig(concurrency=4), db=_FakeDb("sqlite")
        )
    assert worker._concurrency == 1
    [clamped] = [e for e in logs if e["event"] == "worker_concurrency_clamped_sqlite"]
    assert clamped["log_level"] == "warning"
    assert clamped["requested"] == 4
    assert clamped["effective"] == 1


def test_serial_sqlite_worker_logs_no_clamp() -> None:
    with structlog.testing.capture_logs() as logs:
        worker = _ScriptedWorker(_Tracker().body, db=_FakeDb("sqlite"))
    assert worker._concurrency == 1
    assert not [e for e in logs if e["event"] == "worker_concurrency_clamped_sqlite"]


class _BlockingClaimWorker(_ScriptedWorker):
    """The first claim parks until released, standing in for a slow claim query."""

    def __init__(self, body: Callable[[_Job], Awaitable[None]], **kwargs: Any) -> None:
        super().__init__(body, **kwargs)
        self.claim_started = asyncio.Event()
        self.release_claim = asyncio.Event()
        self.claim_error: BaseException | None = None

    async def _claim_next(self) -> Any:
        if not self.claim_started.is_set():
            self.claim_started.set()
            await self.release_claim.wait()
            if self.claim_error is not None:
                raise self.claim_error
        return await super()._claim_next()


async def test_drain_waits_for_a_job_whose_claim_was_in_flight() -> None:
    """drain() mid-claim must not report idle; the claimed job runs to completion.

    Reporting idle would let shutdown cancel the job right after it is claimed,
    leaving it ``RUNNING`` with ``attempts`` bumped — and an approved execution
    is failed on reclaim, so its approved call would never run.
    """
    tracker = _Tracker()
    tracker.expected = 1
    worker = _BlockingClaimWorker(
        tracker.body,
        jobs=[_Job(id="claimed"), _Job(id="queued")],
        config=WorkerConfig(poll_interval_s=60.0),
    )
    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(worker.claim_started.wait(), timeout=_PROMPT_S)

    drain = asyncio.create_task(worker.drain(timeout_s=_PROMPT_S))
    await _yield_loop()
    assert not drain.done(), "drain() must wait while a claim is in flight"

    worker.release_claim.set()
    await _yield_loop()
    assert tracker.current == 1, "a job claimed during drain must still be started"
    assert not drain.done(), "drain() must wait for the job claimed during drain"

    tracker.gate.set()
    assert await asyncio.wait_for(drain, timeout=_PROMPT_S) is True
    await asyncio.wait_for(task, timeout=_PROMPT_S)
    assert tracker.done == ["claimed"]
    assert tracker.cancelled == []
    # Draining stops further claims.
    assert [j.id for j in worker.queue] == ["queued"]


@pytest.mark.parametrize("outcome", ["empty", "error"])
async def test_drain_returns_once_an_in_flight_claim_yields_no_job(outcome: str) -> None:
    """A claim that finds nothing (or fails) during drain releases the drain."""
    worker = _BlockingClaimWorker(_Tracker().body, config=WorkerConfig(poll_interval_s=60.0))
    if outcome == "error":
        worker.claim_error = RuntimeError("claim failed")
    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(worker.claim_started.wait(), timeout=_PROMPT_S)

    drain = asyncio.create_task(worker.drain(timeout_s=_PROMPT_S))
    await _yield_loop()
    assert not drain.done()

    worker.release_claim.set()
    assert await asyncio.wait_for(drain, timeout=_PROMPT_S) is True
    await asyncio.wait_for(task, timeout=_PROMPT_S)


async def test_tick_keeps_worker_busy_for_the_whole_claim() -> None:
    """The single-step ``_tick`` path shares the same claim bookkeeping."""
    tracker = _Tracker()
    tracker.expected = 1
    tracker.gate.set()
    worker = _BlockingClaimWorker(tracker.body, jobs=[_Job(id="a")])
    tick = asyncio.create_task(worker._tick())
    await asyncio.wait_for(worker.claim_started.wait(), timeout=_PROMPT_S)
    assert not worker._idle.is_set()
    worker.release_claim.set()
    assert await asyncio.wait_for(tick, timeout=_PROMPT_S) is True
    assert worker._idle.is_set()
    assert tracker.done == ["a"]


class _FlakySweepWorker(_ScriptedWorker):
    def __init__(self, config: WorkerConfig, failures: int) -> None:
        super().__init__(_Tracker().body, config=config)
        self.attempts = 0
        self._failures = failures

    async def _sweep_expired(self) -> None:
        self.attempts += 1
        if self.attempts <= self._failures:
            raise ConnectionResetError("database went away")


async def test_failed_sweep_is_retried_on_the_next_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sweep that raises does not restart the interval, so recovery retries it at once."""
    clock = [1000.0]
    monkeypatch.setattr("jentic_one.shared.jobs.worker.time.monotonic", lambda: clock[0])
    worker = _FlakySweepWorker(WorkerConfig(sweep_interval_s=30.0), failures=1)

    clock[0] += 30.0
    with pytest.raises(ConnectionResetError):
        await worker._maybe_sweep()
    clock[0] += 1.0
    await worker._maybe_sweep()
    assert worker.attempts == 2
    # The successful sweep restarts the interval.
    clock[0] += 29.0
    await worker._maybe_sweep()
    assert worker.attempts == 2


class _ClosingLoop:
    """A loop that closes between ``is_closed()`` and ``call_soon_threadsafe``."""

    def is_closed(self) -> bool:
        return False

    def call_soon_threadsafe(self, *_: Any) -> None:
        raise RuntimeError("Event loop is closed")


def test_wakeup_notify_never_raises_for_a_loop_closing_mid_notify() -> None:
    """notify() runs inside after_commit; raising would fail an already-committed request."""
    wakeup = JobWakeup()
    racing = asyncio.Event()
    wakeup._subscribers[racing] = _ClosingLoop()  # type: ignore[assignment]
    wakeup.notify()
    assert racing not in wakeup._subscribers


def test_wakeup_notify_drops_subscribers_from_closed_loops() -> None:
    wakeup = JobWakeup()
    wake = asyncio.Event()

    async def subscribe() -> None:
        wakeup.subscribe(wake)

    asyncio.run(subscribe())  # the loop is closed once run() returns
    wakeup.notify()
    assert wake not in wakeup._subscribers


class _FakeNotifySession:
    """Minimal session double recording statements executed by ``notify_job_enqueued``."""

    def __init__(self, dialect_name: str) -> None:
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect_name))
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> None:
        self.executed.append((str(stmt), dict(params or {})))


async def test_notify_job_enqueued_emits_pg_notify_on_postgres() -> None:
    """On Postgres, ``notify_job_enqueued`` issues ``SELECT pg_notify('jentic_jobs', :payload)``."""
    session = _FakeNotifySession("postgresql")
    await notify_job_enqueued(session, JobKind.EXECUTION)  # type: ignore[arg-type]
    await notify_job_enqueued(session)  # type: ignore[arg-type]

    assert session.executed == [
        ("SELECT pg_notify('jentic_jobs', :payload)", {"payload": "execution"}),
        ("SELECT pg_notify('jentic_jobs', :payload)", {"payload": ""}),
    ]


async def test_notify_job_enqueued_is_noop_for_pg_notify_on_sqlite() -> None:
    """On SQLite, ``notify_job_enqueued`` executes no ``pg_notify`` statement."""
    session = _FakeNotifySession("sqlite")
    await notify_job_enqueued(session, JobKind.IMPORT)  # type: ignore[arg-type]

    assert session.executed == []


class _FakeListenConn:
    """In-memory stand-in for a dedicated ``asyncpg`` listener connection."""

    def __init__(self, *, add_listener_error: BaseException | None = None) -> None:
        self.listeners: dict[str, list[Callable[..., None]]] = {}
        self.termination_listeners: list[Callable[[Any], None]] = []
        self.removed_channels: list[str] = []
        self.closed = False
        self.terminated = False
        self._add_listener_error = add_listener_error

    def is_closed(self) -> bool:
        return self.closed

    def add_termination_listener(self, cb: Callable[[Any], None]) -> None:
        self.termination_listeners.append(cb)

    def remove_termination_listener(self, cb: Callable[[Any], None]) -> None:
        if cb in self.termination_listeners:
            self.termination_listeners.remove(cb)

    async def add_listener(self, channel: str, cb: Callable[..., None]) -> None:
        if self._add_listener_error is not None:
            raise self._add_listener_error
        self.listeners.setdefault(channel, []).append(cb)

    async def remove_listener(self, channel: str, cb: Callable[..., None]) -> None:
        self.removed_channels.append(channel)
        if channel in self.listeners and cb in self.listeners[channel]:
            self.listeners[channel].remove(cb)

    async def close(self) -> None:
        self.closed = True

    def terminate(self) -> None:
        self.terminated = True
        self.closed = True

    def deliver(self, payload: str, *, channel: str = JOB_NOTIFY_CHANNEL) -> None:
        for cb in list(self.listeners.get(channel, ())):
            cb(self, 1234, channel, payload)

    def simulate_drop(self) -> None:
        self.closed = True
        for cb in list(self.termination_listeners):
            cb(self)


class _ListeningScriptedWorker(_ScriptedWorker):
    """``_ScriptedWorker`` with a scripted sequence of listener connection outcomes."""

    def __init__(
        self,
        body: Callable[[_Job], Awaitable[None]],
        outcomes: list[_FakeListenConn | BaseException],
        **kwargs: Any,
    ) -> None:
        super().__init__(body, **kwargs)
        self._outcomes = list(outcomes)
        self.listen_sleeps: list[float] = []
        self.connect_attempts = 0
        self.exhausted = asyncio.Event()

    async def _open_listen_connection(self) -> Any:
        self.connect_attempts += 1
        outcome = self._outcomes.pop(0)
        if not self._outcomes:
            self.exhausted.set()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def _listen_sleep(self, delay_s: float) -> None:
        self.listen_sleeps.append(delay_s)
        await _yield_loop(2)

    def _jitter(self) -> float:
        return 1.0


async def test_pg_notify_wakes_worker_on_matching_kind_and_ignores_unrelated_kind() -> None:
    """A ``NOTIFY jentic_jobs`` wakes the loop when its payload matches a registered handler.

    Payloads for unregistered kinds are ignored so a surface-scoped worker does
    not poll on notifications meant for another surface. An empty payload wakes
    unconditionally.
    """
    conn = _FakeListenConn()
    ran_first = asyncio.Event()
    ran_second = asyncio.Event()
    seen: list[str] = []

    async def body(job: _Job) -> None:
        seen.append(job.id)
        if len(seen) == 1:
            ran_first.set()
        elif len(seen) == 2:
            ran_second.set()

    worker = _ListeningScriptedWorker(
        body,
        outcomes=[conn],
        config=WorkerConfig(poll_interval_s=60.0),
    )
    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(worker.polled_empty.wait(), timeout=_PROMPT_S)
    await asyncio.wait_for(worker._listening.wait(), timeout=_PROMPT_S)
    initial_claims = worker.claims

    # Unrelated kind ("import" when only EXECUTION is registered) is ignored.
    conn.deliver(JobKind.IMPORT)
    await _yield_loop()
    assert not worker._wake.is_set()
    assert worker.claims == initial_claims

    # Matching kind ("execution") wakes the parked worker immediately.
    worker.polled_empty.clear()
    worker.queue.append(_Job(id="exec-1", kind=JobKind.EXECUTION))
    conn.deliver(JobKind.EXECUTION)
    await asyncio.wait_for(ran_first.wait(), timeout=_PROMPT_S)
    await asyncio.wait_for(worker.polled_empty.wait(), timeout=_PROMPT_S)

    # Empty payload ("") also wakes the parked worker immediately.
    worker.queue.append(_Job(id="exec-2", kind=JobKind.EXECUTION))
    conn.deliver("")
    await asyncio.wait_for(ran_second.wait(), timeout=_PROMPT_S)

    worker.stop()
    await asyncio.wait_for(task, timeout=_PROMPT_S)
    assert seen == ["exec-1", "exec-2"]
    assert conn.removed_channels == [JOB_NOTIFY_CHANNEL]
    assert conn.closed is True


async def test_listen_failure_falls_back_to_polling_and_recovers_with_backoff() -> None:
    """When LISTEN cannot be established (e.g. cold start or pgbouncer), polling continues."""
    rejected = _FakeListenConn(
        add_listener_error=RuntimeError("LISTEN is not supported in transaction pooling mode")
    )
    recovered_conn = _FakeListenConn()
    ran = asyncio.Event()

    async def body(job: _Job) -> None:
        ran.set()

    worker = _ListeningScriptedWorker(
        body,
        outcomes=[OSError("connection refused"), rejected, recovered_conn],
        jobs=[_Job(id="polled-while-listen-down")],
        config=WorkerConfig(
            poll_interval_s=60.0,
            reconnect_backoff_base_s=1.0,
            reconnect_backoff_max_s=4.0,
        ),
    )
    with structlog.testing.capture_logs() as logs:
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(ran.wait(), timeout=_PROMPT_S)
        await asyncio.wait_for(worker._listening.wait(), timeout=_PROMPT_S)
        worker.stop()
        await asyncio.wait_for(task, timeout=_PROMPT_S)

    # The connection that failed inside add_listener was still closed cleanly.
    assert rejected.closed is True
    assert recovered_conn.closed is True
    assert worker.listen_sleeps == [1.0, 2.0]
    warnings = [e for e in logs if e["event"] == "worker_listen_unavailable"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    recovered = [e for e in logs if e["event"] == "worker_listen_recovered"]
    assert len(recovered) == 1
    assert recovered[0]["failed_attempts"] == 2


async def test_listen_disconnect_reconnects_and_stop_cleans_up_connection() -> None:
    """A dropped LISTEN connection reconnects with backoff and wakes on the new connection."""
    first_conn = _FakeListenConn()
    second_conn = _FakeListenConn()
    ran = asyncio.Event()

    async def body(job: _Job) -> None:
        ran.set()

    worker = _ListeningScriptedWorker(
        body,
        outcomes=[first_conn, second_conn],
        config=WorkerConfig(
            poll_interval_s=60.0,
            reconnect_backoff_base_s=1.5,
            reconnect_backoff_max_s=10.0,
        ),
    )
    with structlog.testing.capture_logs() as logs:
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(worker._listening.wait(), timeout=_PROMPT_S)
        await asyncio.wait_for(worker.polled_empty.wait(), timeout=_PROMPT_S)

        # Simulate the first LISTEN connection dropping mid-flight.
        first_conn.simulate_drop()
        await asyncio.wait_for(worker.exhausted.wait(), timeout=_PROMPT_S)
        await asyncio.wait_for(worker._listening.wait(), timeout=_PROMPT_S)

        # Notifications on the reconnected listener wake the worker.
        worker.queue.append(_Job(id="after-reconnect"))
        second_conn.deliver(JobKind.EXECUTION)
        await asyncio.wait_for(ran.wait(), timeout=_PROMPT_S)

        worker.stop()
        await asyncio.wait_for(task, timeout=_PROMPT_S)

    assert worker.listen_sleeps == [1.5]
    assert second_conn.removed_channels == [JOB_NOTIFY_CHANNEL]
    assert second_conn.closed is True
    assert worker._listen_task is None
    assert not worker._listening.is_set()
    assert len([e for e in logs if e["event"] == "worker_listen_unavailable"]) == 1
    assert len([e for e in logs if e["event"] == "worker_listen_recovered"]) == 1


async def test_close_listen_connection_terminates_on_error_or_cancellation() -> None:
    """A failing or cancelled listener close falls back to ``conn.terminate()``."""

    class _BrokenCloseConn(_FakeListenConn):
        async def remove_listener(self, channel: str, cb: Callable[..., None]) -> None:
            raise OSError("broken pipe")

    class _CancelledCloseConn(_FakeListenConn):
        async def remove_listener(self, channel: str, cb: Callable[..., None]) -> None:
            raise asyncio.CancelledError

    worker = WorkerLoop(
        db=_FakeDb(),  # type: ignore[arg-type]
        handler_registry=JobHandlerRegistry(),
    )
    broken = _BrokenCloseConn()
    await worker._close_listen_connection(broken)
    assert broken.terminated is True

    cancelled = _CancelledCloseConn()
    with pytest.raises(asyncio.CancelledError):
        await worker._close_listen_connection(cancelled)
    assert cancelled.terminated is True


async def test_stop_listener_waits_for_in_flight_close_when_cancelled() -> None:
    """Cancelling ``_listen_task`` after ``stop()`` entered ``finally`` still awaits close."""
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    class _SlowCloseConn(_FakeListenConn):
        async def close(self) -> None:
            close_started.set()
            await release_close.wait()
            await super().close()

    conn = _SlowCloseConn()

    async def body(job: _Job) -> None:
        return None

    worker = _ListeningScriptedWorker(
        body,
        outcomes=[conn],
        config=WorkerConfig(poll_interval_s=60.0),
    )
    worker._running = True
    listen_task = asyncio.create_task(worker._listen_loop())
    worker._listen_task = listen_task
    await asyncio.wait_for(worker._listening.wait(), timeout=_PROMPT_S)

    worker._signal_listen_stop()
    await asyncio.wait_for(close_started.wait(), timeout=_PROMPT_S)

    stop_task = asyncio.create_task(worker._stop_listener())
    await _yield_loop(5)
    assert not stop_task.done(), "_stop_listener must wait until _close_listen_connection finishes"

    release_close.set()
    await asyncio.wait_for(stop_task, timeout=_PROMPT_S)
    assert conn.closed is True
    assert conn.terminated is False


async def test_listen_error_redacts_embedded_url_credentials_and_omits_traceback() -> None:
    """Listener errors carrying DSN userinfo redact the credentials and omit ``exc_info``."""
    recovered_conn = _FakeListenConn()

    async def body(job: _Job) -> None:
        return None

    dsn_error = (
        "connect failed for "
        "postgresql://admin:s3cr3tPass@db.internal:5432/app"  # pragma: allowlist secret
    )
    worker = _ListeningScriptedWorker(
        body,
        outcomes=[
            RuntimeError(dsn_error),
            recovered_conn,
        ],
        config=WorkerConfig(poll_interval_s=60.0),
    )
    with structlog.testing.capture_logs() as logs:
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(worker._listening.wait(), timeout=_PROMPT_S)
        worker.stop()
        await asyncio.wait_for(task, timeout=_PROMPT_S)

    [warning] = [e for e in logs if e["event"] == "worker_listen_unavailable"]
    assert "s3cr3tPass" not in str(warning)
    assert "postgresql://***REDACTED***@db.internal:5432/app" in warning["error"]
    [backoff] = [e for e in logs if e["event"] == "worker_listen_reconnect_backoff"]
    assert backoff["exc_info"] is False


def test_on_pg_notify_ignores_named_kind_when_no_handlers_are_registered() -> None:
    """A worker with an empty handler registry ignores kind-scoped notifications."""
    worker = WorkerLoop(
        db=_FakeDb(),  # type: ignore[arg-type]
        handler_registry=JobHandlerRegistry(),
    )
    worker._on_pg_notify(None, 1, JOB_NOTIFY_CHANNEL, JobKind.EXECUTION)
    assert not worker._wake.is_set()
    worker._on_pg_notify(None, 1, JOB_NOTIFY_CHANNEL, "")
    assert worker._wake.is_set()
