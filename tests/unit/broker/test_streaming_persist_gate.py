"""Unit tests for the gate bounding concurrent streaming execution persists.

``_persist_streaming_outcome`` waits on a per-Context semaphore before it opens
the admin transaction. These tests use a real ``asyncio.Semaphore`` and a stub
transaction that blocks until released, and pin that every way out of the wait
or the write (timeout, cancellation, write error) is counted by ``reason``,
returns the pending gauge to zero, and never leaks a permit.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs

from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.web.routers.execute import (
    _persist_streaming_outcome,
    _streaming_persist_failures,
    _streaming_persist_gate,
    _streaming_persist_pending,
)
from jentic_one.broker.web.streaming import StreamingOutcome
from jentic_one.shared.schemas import OperationInfo

_LIMIT = 2


class _BlockingAdmin:
    """Stub ``admin_db`` whose transactions block until ``release`` is set."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.entered = 0
        self.completed = 0

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[MagicMock]:
        self.entered += 1
        await self.release.wait()
        yield MagicMock()
        self.completed += 1

    def transaction(self) -> Any:
        return self._tx()


def _ctx(
    *, limit: int = _LIMIT, pool_max: int = 10, acquire_timeout_s: float = 5.0
) -> tuple[MagicMock, _BlockingAdmin]:
    ctx = MagicMock()
    ctx.config.broker.streaming_persist_max_concurrency = limit
    ctx.config.broker.streaming_persist_acquire_timeout_s = acquire_timeout_s
    ctx.config.databases.admin.pool_max = pool_max
    admin = _BlockingAdmin()
    ctx.admin_db = admin
    return ctx, admin


def _ctx_req() -> ExecuteRequestContext:
    return ExecuteRequestContext(
        upstream_url="https://api.example.com/x",
        method="GET",
        trace_id="trace-gate",
        toolkit_id="tk-gate",
        operation=OperationInfo(id="op-gate"),
        api_vendor="vendor",
        api_name="name",
        api_version="v1",
    )


async def _persist(ctx: MagicMock, execution_id: str) -> None:
    outcome = StreamingOutcome(execution_id=execution_id, http_status=200)
    outcome.duration_ms = 1
    await _persist_streaming_outcome(
        outcome, ctx, _ctx_req(), datetime.now(UTC), "agent-gate", "agent"
    )


@pytest.fixture()
def pending() -> Iterator[dict[str, int]]:
    """Running value of the ``persist_pending`` up-down counter, by state."""
    state = {"waiting": 0, "in_flight": 0}
    real_add: Callable[..., None] = _streaming_persist_pending.add

    def _add(amount: int, attributes: dict[str, str] | None = None, **kw: Any) -> None:
        real_add(amount, attributes, **kw)
        assert attributes is not None
        state[attributes["state"]] += amount

    with patch.object(_streaming_persist_pending, "add", side_effect=_add):
        yield state


@pytest.fixture()
def failures() -> Iterator[MagicMock]:
    with patch.object(_streaming_persist_failures, "add") as add:
        yield add


async def _wait_for(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(2.0):
        while not predicate():
            await asyncio.sleep(0)


def _write_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("jentic_one.broker.web.routers.execute.persist_streaming_execution", _noop)


@pytest.mark.asyncio
async def test_cancelled_waiter_is_counted_and_leaks_no_permit(
    monkeypatch: pytest.MonkeyPatch, pending: dict[str, int], failures: MagicMock
) -> None:
    """Cancelling a queued persist re-raises, counts ``cancelled`` once, frees nothing."""
    _write_stub(monkeypatch)
    ctx, admin = _ctx()
    holders = [asyncio.create_task(_persist(ctx, f"hold-{i}")) for i in range(_LIMIT)]
    await _wait_for(lambda: admin.entered == _LIMIT)

    waiter = asyncio.create_task(_persist(ctx, "waiter"))
    await _wait_for(lambda: pending["waiting"] == 1)
    with capture_logs() as logs:
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

    failures.assert_called_once_with(1, {"reason": "cancelled"})
    abandoned = [e for e in logs if e["event"] == "streaming_execution_persist_abandoned"]
    assert len(abandoned) == 1
    assert abandoned[0]["reason"] == "cancelled"
    assert abandoned[0]["stage"] == "waiting"
    assert abandoned[0]["trace_id"] == "trace-gate"
    assert "exc_info" not in abandoned[0]
    assert pending == {"waiting": 0, "in_flight": _LIMIT}

    admin.release.set()
    await asyncio.gather(*holders)
    assert pending == {"waiting": 0, "in_flight": 0}

    # Every permit is back: a fresh persist acquires and writes immediately.
    await _persist(ctx, "after")
    assert admin.completed == _LIMIT + 1
    assert _streaming_persist_gate(ctx).semaphore._value == _LIMIT


@pytest.mark.asyncio
async def test_cancel_while_writing_releases_slot(
    monkeypatch: pytest.MonkeyPatch, pending: dict[str, int], failures: MagicMock
) -> None:
    """A persist cancelled inside its transaction frees its permit and in_flight."""
    _write_stub(monkeypatch)
    ctx, admin = _ctx()
    task = asyncio.create_task(_persist(ctx, "writing"))
    await _wait_for(lambda: admin.entered == 1)
    assert pending["in_flight"] == 1

    with capture_logs() as logs:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    failures.assert_called_once_with(1, {"reason": "cancelled"})
    (abandoned,) = [e for e in logs if e["event"] == "streaming_execution_persist_abandoned"]
    assert abandoned["stage"] == "writing"
    assert pending == {"waiting": 0, "in_flight": 0}
    assert _streaming_persist_gate(ctx).semaphore._value == _LIMIT


@pytest.mark.asyncio
async def test_slot_wait_is_bounded_by_acquire_timeout(
    monkeypatch: pytest.MonkeyPatch, pending: dict[str, int], failures: MagicMock
) -> None:
    """With every slot held, a persist gives up at the timeout and never raises."""
    _write_stub(monkeypatch)
    ctx, admin = _ctx(acquire_timeout_s=0.05)
    holders = [asyncio.create_task(_persist(ctx, f"hold-{i}")) for i in range(_LIMIT)]
    await _wait_for(lambda: admin.entered == _LIMIT)

    with capture_logs() as logs:
        async with asyncio.timeout(2.0):
            await _persist(ctx, "starved")

    failures.assert_called_once_with(1, {"reason": "backlog_timeout"})
    (abandoned,) = [e for e in logs if e["event"] == "streaming_execution_persist_abandoned"]
    assert abandoned["reason"] == "backlog_timeout"
    assert abandoned["stage"] == "waiting"
    assert abandoned["acquire_timeout_s"] == 0.05
    assert admin.entered == _LIMIT
    assert pending["waiting"] == 0

    admin.release.set()
    await asyncio.gather(*holders)
    assert pending == {"waiting": 0, "in_flight": 0}
    assert _streaming_persist_gate(ctx).semaphore._value == _LIMIT


@pytest.mark.asyncio
async def test_cancelling_queued_persists_counts_each_one(
    monkeypatch: pytest.MonkeyPatch, pending: dict[str, int], failures: MagicMock
) -> None:
    """Cancelling everything (as a shutdown drain does) counts every queued persist."""
    _write_stub(monkeypatch)
    ctx, admin = _ctx()
    tasks = [asyncio.create_task(_persist(ctx, f"burst-{i}")) for i in range(_LIMIT + 3)]
    await _wait_for(lambda: admin.entered == _LIMIT and pending["waiting"] == 3)

    for task in tasks:
        task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert all(isinstance(r, asyncio.CancelledError) for r in results)
    reasons = [call.args[1]["reason"] for call in failures.call_args_list]
    assert reasons == ["cancelled"] * (_LIMIT + 3)
    assert pending == {"waiting": 0, "in_flight": 0}
    assert _streaming_persist_gate(ctx).semaphore._value == _LIMIT


@pytest.mark.asyncio
async def test_gate_setup_failure_is_best_effort(failures: MagicMock) -> None:
    """A persist whose gate can't be built is counted, logged, and never raises."""
    ctx = MagicMock()  # config knobs are MagicMocks, so the limit can't be computed

    with capture_logs() as logs:
        await _persist(ctx, "bad-config")

    failures.assert_called_once_with(1, {"reason": "write_error"})
    assert [e["event"] for e in logs] == ["streaming_execution_persist_failed"]


@pytest.mark.parametrize(
    ("configured", "pool_max", "expected"),
    [(5, 10, 5), (20, 10, 9), (4, 1, 1), (1, 10, 1)],
)
@pytest.mark.asyncio
async def test_limit_is_clamped_below_admin_pool_and_logged_once(
    configured: int, pool_max: int, expected: int
) -> None:
    """The limit never exceeds ``admin.pool_max - 1`` (floor 1); a clamp is logged once."""
    ctx, _ = _ctx(limit=configured, pool_max=pool_max)

    with capture_logs() as logs:
        gate = _streaming_persist_gate(ctx)
        assert _streaming_persist_gate(ctx) is gate

    assert gate.limit == expected
    assert gate.semaphore._value == expected
    clamped = [e for e in logs if e["event"] == "streaming_persist_limit_clamped"]
    if expected < configured:
        assert clamped == [
            {
                "event": "streaming_persist_limit_clamped",
                "log_level": "warning",
                "configured": configured,
                "effective": expected,
                "admin_pool_max": pool_max,
            }
        ]
    else:
        assert clamped == []


@pytest.mark.asyncio
async def test_each_context_gets_its_own_gate() -> None:
    """Two Contexts in one process never share a semaphore."""
    small, _ = _ctx(limit=1)
    large, _ = _ctx(limit=4)

    small_gate = _streaming_persist_gate(small)
    large_gate = _streaming_persist_gate(large)

    assert small_gate.semaphore is not large_gate.semaphore
    assert (small_gate.limit, large_gate.limit) == (1, 4)
    assert _streaming_persist_gate(small) is small_gate
