"""Unit tests for the shared worker infrastructure."""

from __future__ import annotations

import asyncio
from collections.abc import MutableMapping
from typing import Any

import asyncpg
import pytest
import structlog
from sqlalchemy.exc import DBAPIError, OperationalError

from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.jobs.worker import WorkerLoop, _backoff_s, _reconnect_backoff_s
from jentic_one.shared.models.jobs import JobKind


class _FakeHandler:
    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        return JobResultPayload(body={"done": True})


def test_register_and_get() -> None:
    registry = JobHandlerRegistry()
    handler = _FakeHandler()
    registry.register(JobKind.IMPORT, handler)
    assert registry.get(JobKind.IMPORT) is handler


def test_get_unregistered_returns_none() -> None:
    registry = JobHandlerRegistry()
    assert registry.get(JobKind.EXECUTION) is None


@pytest.mark.asyncio
async def test_claim_next_returns_none_when_no_handlers() -> None:
    """A worker with no registered handlers should never claim a job."""
    registry = JobHandlerRegistry()

    class _FakeDb:
        pass

    worker = WorkerLoop(db=_FakeDb(), handler_registry=registry, poll_interval=0.0)  # type: ignore[arg-type]
    result = await worker._claim_next()
    assert result is None


@pytest.mark.asyncio
async def test_run_survives_tick_exception() -> None:
    """A transient tick failure (e.g. cold-start DB error) must not kill the loop.

    The loop should log and keep polling rather than terminating permanently,
    which would otherwise leave every queued job stuck forever.
    """
    calls = 0

    class _FlakyWorker(WorkerLoop):
        def __init__(self) -> None:
            self._poll_interval = 0.0
            self._running = False

        async def _tick(self) -> bool:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient cold-start DB error")
            if calls >= 3:
                self._running = False
            return False

    worker = _FlakyWorker()
    await asyncio.wait_for(worker.run(), timeout=2.0)

    assert calls >= 3, "loop should keep ticking after a tick raised"


def test_backoff_grows_exponentially_and_caps() -> None:
    cfg = WorkerConfig(retry_backoff_base_s=2.0, retry_backoff_max_s=10.0)
    assert _backoff_s(1, cfg) == 2.0
    assert _backoff_s(2, cfg) == 4.0
    assert _backoff_s(3, cfg) == 8.0
    # 2 * 2**3 = 16 → capped at 10.
    assert _backoff_s(4, cfg) == 10.0
    assert _backoff_s(99, cfg) == 10.0


@pytest.mark.asyncio
async def test_drain_returns_immediately_when_idle() -> None:
    """drain() returns True at once when no job is in flight, and stops the loop."""

    class _FakeDb:
        pass

    worker = WorkerLoop(
        db=_FakeDb(),  # type: ignore[arg-type]
        handler_registry=JobHandlerRegistry(),
        worker_config=WorkerConfig(drain_timeout_s=1.0),
    )
    drained = await asyncio.wait_for(worker.drain(), timeout=1.0)
    assert drained is True
    assert worker._running is False
    assert worker._draining is True


@pytest.mark.asyncio
async def test_drain_stops_claiming_via_tick() -> None:
    """Once draining, _tick claims nothing even if handlers are registered."""

    class _FakeDb:
        pass

    worker = WorkerLoop(
        db=_FakeDb(),  # type: ignore[arg-type]
        handler_registry=_registry_with(_FakeHandler()),
        worker_config=WorkerConfig(),
    )
    worker._draining = True
    # _tick short-circuits before touching the DB while draining.
    assert await worker._tick() is False


def _registry_with(handler: Any) -> JobHandlerRegistry:
    reg = JobHandlerRegistry()
    reg.register(JobKind.EXECUTION, handler)
    return reg


def _connection_dropped() -> DBAPIError:
    """The error from #1082: SQLAlchemy's DBAPIError over ConnectionDoesNotExistError."""
    driver = asyncpg.exceptions.ConnectionDoesNotExistError(
        "connection was closed in the middle of operation"
    )
    err = DBAPIError("UPDATE jobs SET status=$1::VARCHAR", ("running",), driver)
    err.__cause__ = driver
    return err


class _ScriptedWorker(WorkerLoop):
    """WorkerLoop whose ticks follow a script; sleeps are recorded, not awaited.

    Each script entry is an exception to raise or a bool to return. The loop
    stops once the script is exhausted.
    """

    def __init__(
        self,
        script: list[BaseException | bool],
        *,
        jitter: float = 1.0,
        worker_config: WorkerConfig | None = None,
    ) -> None:
        super().__init__(
            db=object(),  # type: ignore[arg-type]
            handler_registry=JobHandlerRegistry(),
            poll_interval=0.5,
            worker_config=worker_config,
        )
        self._script = list(script)
        self._fixed_jitter = jitter
        self.sleeps: list[float] = []

    async def _tick(self) -> bool:
        step = self._script.pop(0)
        if not self._script:
            self._running = False
        if isinstance(step, BaseException):
            raise step
        return step

    async def _sleep(self, delay_s: float) -> None:
        self.sleeps.append(delay_s)

    def _jitter(self) -> float:
        return self._fixed_jitter


def _events(logs: list[MutableMapping[str, Any]], name: str) -> list[MutableMapping[str, Any]]:
    return [entry for entry in logs if entry["event"] == name]


def test_reconnect_backoff_grows_exponentially_caps_and_jitters() -> None:
    cfg = WorkerConfig()
    # Full jitter (1.0) yields the capped exponential value itself.
    full = [_reconnect_backoff_s(n, 1.0, cfg) for n in range(1, 8)]
    assert full == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]
    # Zero jitter is the lower bound: half the capped value.
    assert _reconnect_backoff_s(1, 0.0, cfg) == 1.0
    assert _reconnect_backoff_s(99, 0.0, cfg) == 15.0
    # Every jittered delay stays within [capped / 2, cap].
    for n in range(1, 10):
        for j in (0.0, 0.25, 0.5, 0.999):
            assert 1.0 <= _reconnect_backoff_s(n, j, cfg) <= 30.0


def test_reconnect_backoff_uses_configured_base_and_cap() -> None:
    cfg = WorkerConfig(reconnect_backoff_base_s=0.5, reconnect_backoff_max_s=3.0)
    assert [_reconnect_backoff_s(n, 1.0, cfg) for n in range(1, 6)] == [0.5, 1.0, 2.0, 3.0, 3.0]
    assert _reconnect_backoff_s(99, 0.0, cfg) == 1.5


@pytest.mark.asyncio
async def test_run_connection_loss_backoff_follows_worker_config() -> None:
    """The run loop's reconnect delays come from ``worker.reconnect_backoff_*``."""
    cfg = WorkerConfig(reconnect_backoff_base_s=1.0, reconnect_backoff_max_s=4.0)
    worker = _ScriptedWorker(
        [*(_connection_dropped() for _ in range(4)), True, False], worker_config=cfg
    )
    await asyncio.wait_for(worker.run(), timeout=2.0)
    assert worker.sleeps == [1.0, 2.0, 4.0, 4.0, 0.5]


@pytest.mark.asyncio
async def test_run_connection_loss_warns_once_backs_off_and_logs_recovery() -> None:
    """N connection drops then success: one warning, growing capped backoff, one recovery."""
    drops = 6
    worker = _ScriptedWorker([*(_connection_dropped() for _ in range(drops)), True, False])

    with structlog.testing.capture_logs() as logs:
        await asyncio.wait_for(worker.run(), timeout=2.0)

    lost = _events(logs, "worker_db_connection_lost")
    assert len(lost) == 1
    assert lost[0]["log_level"] == "warning"
    assert lost[0]["error_type"] == "ConnectionDoesNotExistError"
    assert lost[0]["error"] == "connection was closed in the middle of operation"
    assert "exc_info" not in lost[0]
    assert "UPDATE jobs" not in str(lost[0])
    # Detail (with traceback) is debug-only, one per attempt.
    debug = _events(logs, "worker_db_reconnect_backoff")
    assert [d["log_level"] for d in debug] == ["debug"] * drops
    assert all(d["exc_info"] is True for d in debug)
    # No full-traceback error dumps for an expected outage.
    assert not _events(logs, "worker_loop_tick_error")

    # Backoff grows exponentially then caps; the trailing idle tick polls normally.
    assert worker.sleeps == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 0.5]

    recovered = _events(logs, "worker_db_connection_recovered")
    assert len(recovered) == 1
    assert recovered[0]["log_level"] == "info"
    assert recovered[0]["failed_attempts"] == drops


@pytest.mark.asyncio
async def test_run_second_outage_warns_again_and_resets_backoff() -> None:
    """After recovery the attempt counter resets: a new outage warns and restarts backoff."""
    worker = _ScriptedWorker(
        [_connection_dropped(), _connection_dropped(), True, _connection_dropped(), True]
    )
    with structlog.testing.capture_logs() as logs:
        await asyncio.wait_for(worker.run(), timeout=2.0)

    assert len(_events(logs, "worker_db_connection_lost")) == 2
    assert len(_events(logs, "worker_db_connection_recovered")) == 2
    assert worker.sleeps == [2.0, 4.0, 2.0]


@pytest.mark.asyncio
async def test_run_unrelated_error_keeps_traceback_and_poll_interval() -> None:
    """A non-connection error is still logged with a traceback and does not back off."""
    worker = _ScriptedWorker(
        [
            RuntimeError("real bug"),
            _connection_dropped(),
            ValueError("x"),
            _connection_dropped(),
            False,
        ]
    )
    with structlog.testing.capture_logs() as logs:
        await asyncio.wait_for(worker.run(), timeout=2.0)

    tick_errors = _events(logs, "worker_loop_tick_error")
    assert len(tick_errors) == 2
    assert all(e["log_level"] == "error" and e["exc_info"] is True for e in tick_errors)
    # Unrelated errors use the normal poll interval; only drops back off, and an
    # unrelated error ends the outage so the next drop warns and restarts backoff.
    assert worker.sleeps == [0.5, 2.0, 0.5, 2.0, 0.5]
    assert len(_events(logs, "worker_db_connection_lost")) == 2
    # Only a successful tick counts as recovery.
    assert len(_events(logs, "worker_db_connection_recovered")) == 1


@pytest.mark.asyncio
async def test_run_propagates_cancellation_during_outage() -> None:
    """Cancellation is never swallowed by the reconnect path."""

    class _CancelOnSleep(_ScriptedWorker):
        async def _sleep(self, delay_s: float) -> None:
            raise asyncio.CancelledError

    worker = _CancelOnSleep([_connection_dropped(), True])
    with structlog.testing.capture_logs() as logs:
        await asyncio.wait_for(worker.run(), timeout=2.0)
    assert _events(logs, "worker_loop_cancelled")
    assert worker._running is False


@pytest.mark.asyncio
async def test_sweep_reraises_connection_loss_but_logs_other_errors() -> None:
    """The retention sweep defers a lost connection to the loop; other errors stay local."""

    class _FailingDb:
        def __init__(self, exc: BaseException) -> None:
            self._exc = exc

        def transaction(self) -> Any:
            raise self._exc

    lost = WorkerLoop(db=_FailingDb(_connection_dropped()), handler_registry=JobHandlerRegistry())  # type: ignore[arg-type]
    with pytest.raises(DBAPIError):
        await lost._sweep_expired()

    buggy = WorkerLoop(db=_FailingDb(RuntimeError("bug")), handler_registry=JobHandlerRegistry())  # type: ignore[arg-type]
    with structlog.testing.capture_logs() as logs:
        await buggy._sweep_expired()
    assert _events(logs, "retention_sweep_failed")


class _Job:
    id = "job-1"
    kind = JobKind.EXECUTION
    attempts = 1
    payload = None
    created_by = None
    actor_type = None


class _MidJobWorker(WorkerLoop):
    """Claims one job whose handler (or completion) raises ``exc``.

    ``_handle_failure`` records the persisted error string instead of writing
    it, or raises ``requeue_exc`` to model a database still down at requeue.
    """

    def __init__(self, exc: BaseException, *, requeue_exc: BaseException | None = None) -> None:
        super().__init__(
            db=object(),  # type: ignore[arg-type]
            handler_registry=_registry_with(_FakeHandler()),
        )
        self._exc = exc
        self._requeue_exc = requeue_exc
        self.persisted: list[str] = []

    async def _claim_next(self) -> Any:
        return _Job()

    async def _execute_handler(self, handler: Any, job: Any) -> JobResultPayload:
        raise self._exc

    async def _handle_failure(
        self, job: Any, error: str, *, min_delay_s: float | None = None
    ) -> None:
        if self._requeue_exc is not None:
            raise self._requeue_exc
        self.persisted.append(error)


@pytest.mark.asyncio
async def test_tick_mid_job_connection_loss_persists_generic_error_without_traceback() -> None:
    """A drop mid-job: one warning (no traceback), generic error persisted, job requeued."""
    worker = _MidJobWorker(_connection_dropped())
    with structlog.testing.capture_logs() as logs:
        assert await worker._tick() is True

    assert not _events(logs, "job_failed")
    interrupted = _events(logs, "job_interrupted_db_connection_lost")
    assert len(interrupted) == 1
    assert interrupted[0]["log_level"] == "warning"
    assert "exc_info" not in interrupted[0]
    assert interrupted[0]["error_type"] == "ConnectionDoesNotExistError"
    assert "UPDATE jobs" not in str(interrupted[0])
    assert worker.persisted == ["interrupted: database connection lost"]
    assert worker._idle.is_set()


@pytest.mark.asyncio
async def test_tick_mid_job_connection_loss_with_db_still_down_defers_to_run_loop() -> None:
    """If the requeue also hits the dead connection, the error escapes to run()."""
    worker = _MidJobWorker(_connection_dropped(), requeue_exc=_connection_dropped())
    with pytest.raises(DBAPIError):
        await worker._tick()
    assert worker._idle.is_set()


@pytest.mark.asyncio
async def test_tick_job_db_error_keeps_traceback_but_persists_no_sql() -> None:
    """A non-connection DB error is a real failure: traceback kept, SQL never persisted."""
    exc = OperationalError(
        "SELECT * FROM jobs WHERE secret = ?", ("s3cr3t",), Exception("no such table: jobs")
    )
    worker = _MidJobWorker(exc)
    with structlog.testing.capture_logs() as logs:
        assert await worker._tick() is True

    failed = _events(logs, "job_failed")
    assert len(failed) == 1 and failed[0]["exc_info"] is True
    assert not _events(logs, "job_interrupted_db_connection_lost")
    assert len(worker.persisted) == 1
    persisted = worker.persisted[0]
    assert "no such table: jobs" in persisted
    assert "SELECT" not in persisted and "s3cr3t" not in persisted
