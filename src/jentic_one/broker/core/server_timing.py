"""Per-request stage timing for the broker's opt-in ``Server-Timing`` header (#1717).

A request-scoped :class:`ServerTimingRecorder` lives in a context variable that
the Server-Timing middleware activates. It is the broker's single per-request
record of where the time went: the ``Server-Timing`` header is formatted from
it, and any other per-request latency consumer reads the same durations from
:func:`current_recorder` rather than keeping its own clock.

Instrumented code brackets a stage with :func:`stage`, which records in a
``finally`` so a stage that raises is still reported:

.. code-block:: python

    with stage(ServerTimingStage.DISCOVER):
        resolved = await discover(...)

    with stage(ServerTimingStage.UPSTREAM) as upstream:
        outcome = await broker.execute(...)
    upstream.duration_ms  # the measured window (``None`` when no recorder)

:func:`stage_start` / :func:`stage_end` are the explicit pair for a stage that
must be recorded on success only (the ``auth`` gate).

When no recorder is active (the default), :func:`stage` returns a shared no-op
handle and :func:`stage_start` returns ``None`` — one context-variable read per
call, no allocation. Repeated recordings of the same stage accumulate, so a
stage spread over several spans (e.g. the egress pre-check plus the
post-injection re-check) is reported as one total.

Stage names are a fixed, low-cardinality enum in the reserved ``jentic-``
namespace, and the formatted value carries only those names and millisecond
durations — never URLs, ids, or credential material.
"""

from __future__ import annotations

import time
from contextvars import ContextVar, Token
from enum import StrEnum
from types import TracebackType

__all__ = [
    "SERVER_TIMING_HEADER",
    "SERVER_TIMING_NAMESPACE",
    "ServerTimingRecorder",
    "ServerTimingStage",
    "StageTimer",
    "activate",
    "current_recorder",
    "deactivate",
    "stage",
    "stage_end",
    "stage_start",
]

SERVER_TIMING_HEADER = "Server-Timing"

# Every broker metric name starts with this prefix, so a client can tell the
# broker's entries from an upstream's ``auth``/``total``/``db`` metrics.
SERVER_TIMING_NAMESPACE = "jentic-"


class ServerTimingStage(StrEnum):
    """Broker request stages, emitted in declaration order.

    - ``jentic-auth`` — credential validation, the execute-permission check,
      and the per-caller rate limit (the execute route's auth dependency).
    - ``jentic-egress`` — the egress allowlist check of the upstream URL,
      including DNS resolution: once on entry and again after credential
      injection when server variables rewrite the host.
    - ``jentic-discover`` — operation discovery against the registry,
      including revision-pin resolution and the unregistered-URL handler.
    - ``jentic-rules`` — credential-binding derivation and permission-rule
      evaluation.
    - ``jentic-body`` — reading (and size-capping) the request body.
    - ``jentic-idempotency`` — the ``Idempotency-Key`` claim and completion
      round-trips to shared state.
    - ``jentic-cred`` — credential resolution and injection into the outbound
      request.
    - ``jentic-upstream`` — the upstream call. Buffered: the whole exchange
      including the body (and any retries). Streaming: until the upstream
      response headers arrive.
    - ``jentic-persist`` — the request's admin-DB write: the execution record
      and lifecycle events (including the ``FAILED`` record of a
      transport-level upstream failure), the enqueued job of an async call, or
      the hold of an approval-gated call. Absent on the streaming path, which
      persists after the body has been sent.
    - ``jentic-total`` — from the Server-Timing middleware receiving the
      request (inside admission control) to the response headers. What the
      stages above do not cover is routing, response assembly, and the
      middleware itself.
    """

    AUTH = "jentic-auth"
    EGRESS = "jentic-egress"
    DISCOVER = "jentic-discover"
    RULES = "jentic-rules"
    BODY = "jentic-body"
    IDEMPOTENCY = "jentic-idempotency"
    CRED = "jentic-cred"
    UPSTREAM = "jentic-upstream"
    PERSIST = "jentic-persist"
    TOTAL = "jentic-total"


_STAGE_ORDER: tuple[ServerTimingStage, ...] = tuple(ServerTimingStage)


class ServerTimingRecorder:
    """Accumulates per-stage durations (milliseconds) for one request.

    ``started_at`` is the ``perf_counter`` reading when the request entered the
    middleware; :meth:`elapsed_ms` measures from it. A consumer that needs the
    broker's own time for a request takes ``elapsed_ms()`` at its measurement
    point and subtracts ``duration_ms(ServerTimingStage.UPSTREAM)``.
    """

    __slots__ = ("_durations_ms", "started_at")

    def __init__(self) -> None:
        self.started_at = time.perf_counter()
        self._durations_ms: dict[ServerTimingStage, float] = {}

    def add(self, stage: ServerTimingStage, duration_ms: float) -> None:
        """Add ``duration_ms`` to ``stage`` (accumulating across calls)."""
        self._durations_ms[stage] = self._durations_ms.get(stage, 0.0) + duration_ms

    def has(self, stage: ServerTimingStage) -> bool:
        """Whether ``stage`` has been recorded."""
        return stage in self._durations_ms

    def duration_ms(self, stage: ServerTimingStage) -> float:
        """The recorded total for ``stage`` (``0.0`` when unrecorded)."""
        return self._durations_ms.get(stage, 0.0)

    def elapsed_ms(self) -> float:
        """Milliseconds since the request entered the middleware."""
        return (time.perf_counter() - self.started_at) * 1000.0

    def header_value(self, *, with_durations: bool = True) -> str:
        """Format the recorded stages per the W3C Server-Timing syntax.

        ``jentic-auth;dur=1.2, jentic-discover;dur=0.8, …`` — stages in
        declaration order, durations in milliseconds at 0.1 ms resolution.
        With ``with_durations=False`` only the names of the stages that ran are
        listed (``jentic-auth, jentic-discover``), which says where a request
        stopped without saying how long anything took.
        """
        if not with_durations:
            return ", ".join(stage.value for stage in _STAGE_ORDER if stage in self._durations_ms)
        return ", ".join(
            f"{stage.value};dur={self._durations_ms[stage]:.1f}"
            for stage in _STAGE_ORDER
            if stage in self._durations_ms
        )


_current: ContextVar[ServerTimingRecorder | None] = ContextVar("broker_server_timing", default=None)


def activate(recorder: ServerTimingRecorder) -> Token[ServerTimingRecorder | None]:
    """Make ``recorder`` the active recorder for the current context."""
    return _current.set(recorder)


def deactivate(token: Token[ServerTimingRecorder | None]) -> None:
    """Restore the recorder that was active before :func:`activate`."""
    _current.reset(token)


def current_recorder() -> ServerTimingRecorder | None:
    """The active recorder, or ``None`` when no request recorder is active."""
    return _current.get()


def stage_start() -> float | None:
    """Start timing a stage: a ``perf_counter`` reading, or ``None`` when off."""
    if _current.get() is None:
        return None
    return time.perf_counter()


def stage_end(stage: ServerTimingStage, started: float | None) -> float | None:
    """Record the time since ``started`` against ``stage``.

    Returns the recorded duration in milliseconds, or ``None`` when no
    recorder is active.
    """
    if started is None:
        return None
    recorder = _current.get()
    if recorder is None:
        return None
    duration_ms = (time.perf_counter() - started) * 1000.0
    recorder.add(stage, duration_ms)
    return duration_ms


class StageTimer:
    """Times one stage; records on exit, exception or not.

    Usable as a sync or an async context manager, so it can share one
    ``async with`` statement with the resource it times (``async with
    stage(PERSIST), db.transaction() as session:`` covers the commit too).
    ``duration_ms`` holds the measured window once the block exits.
    """

    __slots__ = ("_recorder", "_stage", "_started", "duration_ms")

    def __init__(self, recorder: ServerTimingRecorder, stage: ServerTimingStage) -> None:
        self._recorder = recorder
        self._stage = stage
        self._started = 0.0
        self.duration_ms: float | None = None

    def __enter__(self) -> StageTimer:
        self._started = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.duration_ms = (time.perf_counter() - self._started) * 1000.0
        self._recorder.add(self._stage, self.duration_ms)

    async def __aenter__(self) -> StageTimer:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc, tb)


class _NoopStageTimer:
    """The shared handle :func:`stage` returns when no recorder is active."""

    __slots__ = ()

    duration_ms: float | None = None

    def __enter__(self) -> _NoopStageTimer:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def __aenter__(self) -> _NoopStageTimer:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


_NOOP_STAGE = _NoopStageTimer()


def stage(name: ServerTimingStage) -> StageTimer | _NoopStageTimer:
    """Time the enclosed block as ``name`` (a shared no-op when no recorder is active)."""
    recorder = _current.get()
    if recorder is None:
        return _NOOP_STAGE
    return StageTimer(recorder, name)
