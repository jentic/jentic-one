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
from typing import Any

import pytest

from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.jobs.wakeup import JobWakeup
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
    async def execute(self, job_id: str, session: Any, **_: Any) -> JobResultPayload:
        return JobResultPayload(body={})


class _FakeDb:
    pass


class _ScriptedWorker(WorkerLoop):
    """WorkerLoop whose claim pops an in-memory queue and whose job body is injected."""

    def __init__(
        self,
        body: Callable[[_Job], Awaitable[None]],
        *,
        jobs: list[_Job] | None = None,
        config: WorkerConfig | None = None,
        wakeup: JobWakeup | None = None,
    ) -> None:
        registry = JobHandlerRegistry()
        registry.register(JobKind.EXECUTION, _NoopHandler())
        super().__init__(
            db=_FakeDb(),  # type: ignore[arg-type]
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

    assert await worker.drain(timeout_s=0.01) is False
    task.cancel()
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
