"""App-owned registry for request work that must not hold the response.

Some work belongs to a request but must not delay what the client sees: a
streaming response that fails mid-body has to close its connection at once,
while the execution record for that request is written afterwards. A request
hands such work to the app's :class:`BackgroundTaskRegistry` with
:meth:`~BackgroundTaskRegistry.spawn` and returns without awaiting it.

The registry is the supervisor those tasks would otherwise lack:

* every task runs under its own ``asyncio.timeout``, so a stalled task (a
  starved DB pool, a hung transaction) ends at its budget and the number of
  pending tasks stays bounded by spawn rate x timeout;
* every outcome is logged where it is not a success and counted on the
  ``app.background_tasks`` counter by ``kind`` and ``outcome`` (``completed``,
  ``failed``, ``timed_out``, ``cancelled``, ``rejected``); no exception
  escapes a task;
* the app lifespan owns the registry: it is created with the app and drained
  on shutdown *before* the databases close
  (:meth:`~BackgroundTaskRegistry.drain`). The drain waits a bounded time,
  then cancels and counts whatever is still running. A spawn after the drain
  has finished is rejected (counted and logged, the coroutine closed unrun).

A spawned coroutine is a unit of best-effort work: it must catch and report
its own domain failures where it has better context, and it must tolerate
being cancelled at its timeout or at shutdown.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import structlog
from starlette.applications import Starlette

from jentic_one.shared.metrics import get_meter

__all__ = [
    "BackgroundTaskOutcome",
    "BackgroundTaskRegistry",
    "DrainResult",
    "get_background_tasks",
    "install_background_tasks",
]

logger = structlog.get_logger(__name__)

_meter = get_meter("app")
_task_outcomes = _meter.create_counter(
    "app.background_tasks",
    description=(
        "Tasks handed to the app's background-task registry, by kind and outcome "
        "(completed, failed, timed_out, cancelled, rejected)"
    ),
)

# How long a drain waits for tasks it has cancelled to unwind. Cancellation is
# cooperative; a task that keeps running past this is left to the event loop's
# own teardown.
_CANCEL_GRACE_S = 1.0

_STATE_ATTR = "background_tasks"


class BackgroundTaskOutcome(StrEnum):
    """How a task handed to the registry ended (the counter's ``outcome``)."""

    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class DrainResult:
    """What a :meth:`BackgroundTaskRegistry.drain` found.

    ``finished`` tasks ended on their own during the drain (any outcome);
    ``cancelled`` tasks were still running at the deadline and were cancelled.
    """

    finished: int
    cancelled: int


def _count(kind: str, outcome: BackgroundTaskOutcome) -> None:
    _task_outcomes.add(1, {"kind": kind, "outcome": outcome.value})


async def _supervise(
    coro: Coroutine[Any, Any, None],
    *,
    kind: str,
    timeout_s: float,
    fields: Mapping[str, object],
) -> None:
    """Run ``coro`` under its timeout; log and count how it ended. Never raises.

    ``CancelledError`` (a drain cancelling the task) is counted, logged and
    re-raised, never swallowed.
    """
    try:
        async with asyncio.timeout(timeout_s) as budget:
            await coro
    except TimeoutError:
        if budget.expired():
            _count(kind, BackgroundTaskOutcome.TIMED_OUT)
            logger.error("background_task_timed_out", kind=kind, timeout_s=timeout_s, **fields)
        else:
            _count(kind, BackgroundTaskOutcome.FAILED)
            logger.error("background_task_failed", kind=kind, exc_info=True, **fields)
    except asyncio.CancelledError:
        _count(kind, BackgroundTaskOutcome.CANCELLED)
        logger.error("background_task_cancelled", kind=kind, **fields)
        raise
    except Exception:
        _count(kind, BackgroundTaskOutcome.FAILED)
        logger.error("background_task_failed", kind=kind, exc_info=True, **fields)
    else:
        _count(kind, BackgroundTaskOutcome.COMPLETED)


class BackgroundTaskRegistry:
    """Tracks fire-and-forget request work until it ends or the app drains it.

    One instance per app, created with the app (:func:`install_background_tasks`)
    and drained by its lifespan. Not thread-safe; use from the app's event loop.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    @property
    def pending(self) -> int:
        """Tasks spawned and not yet finished."""
        return len(self._tasks)

    @property
    def closed(self) -> bool:
        """Whether a drain has finished; further spawns are rejected."""
        return self._closed

    def spawn(
        self,
        coro: Coroutine[Any, Any, None],
        *,
        kind: str,
        timeout_s: float,
        log_fields: Mapping[str, object] | None = None,
    ) -> bool:
        """Start ``coro`` as a supervised task and return without awaiting it.

        ``kind`` labels the counter and every log line (keep it a small, fixed
        vocabulary, e.g. ``streaming_execution_persist``). ``timeout_s`` bounds
        the task's whole run, including any wait for a resource inside it.
        ``log_fields`` are added to the task's failure/timeout/cancel log lines
        (correlation ids only — never secrets or payloads).

        Returns ``False`` (and closes ``coro`` unrun) when the registry has
        already been drained.
        """
        fields = dict(log_fields or {})
        if self._closed:
            coro.close()
            _count(kind, BackgroundTaskOutcome.REJECTED)
            logger.error("background_task_rejected", kind=kind, reason="draining", **fields)
            return False
        task = asyncio.create_task(
            _supervise(coro, kind=kind, timeout_s=timeout_s, fields=fields),
            name=f"background:{kind}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def drain(self, timeout_s: float) -> DrainResult:
        """Wait up to ``timeout_s`` for every pending task, then cancel the rest.

        Tasks spawned while the drain waits are waited on too. Once it returns
        the registry is closed. Logs one summary line; each cancelled task is
        also counted (``outcome=cancelled``) and logged by its supervisor.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout_s, 0.0)
        finished = 0
        while self._tasks:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            done, _ = await asyncio.wait(set(self._tasks), timeout=remaining)
            finished += len(done)
        self._closed = True
        leftover = set(self._tasks)
        for task in leftover:
            task.cancel()
        if leftover:
            await asyncio.wait(leftover, timeout=_CANCEL_GRACE_S)
            logger.warning(
                "background_tasks_drain_timed_out",
                finished=finished,
                cancelled=len(leftover),
                timeout_s=timeout_s,
            )
        elif finished:
            logger.info("background_tasks_drained", finished=finished)
        return DrainResult(finished=finished, cancelled=len(leftover))


def install_background_tasks(app: Starlette) -> BackgroundTaskRegistry:
    """Create the app's registry on ``app.state`` (the app lifespan must drain it)."""
    registry = BackgroundTaskRegistry()
    setattr(app.state, _STATE_ATTR, registry)
    return registry


def get_background_tasks(app: Starlette) -> BackgroundTaskRegistry | None:
    """The app's registry, or ``None`` for an app built without one."""
    registry = getattr(app.state, _STATE_ATTR, None)
    return registry if isinstance(registry, BackgroundTaskRegistry) else None
