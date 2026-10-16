"""Per-request stage timing for the broker's opt-in ``Server-Timing`` header (#1717).

A request-scoped :class:`ServerTimingRecorder` lives in a context variable that
the Server-Timing middleware sets only when ``broker.server_timing`` is enabled.
Instrumented code brackets a stage with :func:`stage_start` / :func:`stage_end`:

.. code-block:: python

    t = stage_start()
    resolved = await discover(...)
    stage_end(ServerTimingStage.DISCOVER, t)

When no recorder is active (the default), :func:`stage_start` returns ``None``
and :func:`stage_end` returns immediately — one context-variable read per call,
no allocation. Repeated ``stage_end`` calls for the same stage accumulate, so a
stage spread over several spans (e.g. discovery plus a pinned re-resolve) is
reported as one total.

Stage names are a fixed, low-cardinality enum and the formatted value carries
only names and millisecond durations — never URLs, ids, or credential material.
"""

from __future__ import annotations

import time
from contextvars import ContextVar, Token
from enum import StrEnum

__all__ = [
    "SERVER_TIMING_HEADER",
    "ServerTimingRecorder",
    "ServerTimingStage",
    "activate",
    "current_recorder",
    "deactivate",
    "stage_end",
    "stage_start",
]

SERVER_TIMING_HEADER = "Server-Timing"


class ServerTimingStage(StrEnum):
    """Broker request stages, emitted in declaration order.

    - ``auth`` — credential validation, the execute-permission check, and the
      per-caller rate limit (the execute route's auth dependency).
    - ``discover`` — operation discovery against the registry, including
      revision-pin resolution.
    - ``rules`` — credential-binding derivation and permission-rule evaluation.
    - ``cred`` — credential resolution and injection into the outbound request.
    - ``upstream`` — the upstream call. Buffered: the whole exchange including
      the body (and any retries). Streaming: until the upstream response
      headers arrive.
    - ``persist`` — writing the execution record and lifecycle events, including
      the ``FAILED`` record of a transport-level upstream failure (buffered path
      only; the streaming path persists after the body has been sent).
    - ``total`` — from the request entering the broker to the response headers.
    """

    AUTH = "auth"
    DISCOVER = "discover"
    RULES = "rules"
    CRED = "cred"
    UPSTREAM = "upstream"
    PERSIST = "persist"
    TOTAL = "total"


_STAGE_ORDER: tuple[ServerTimingStage, ...] = tuple(ServerTimingStage)


class ServerTimingRecorder:
    """Accumulates per-stage durations (milliseconds) for one request."""

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

    def header_value(self) -> str:
        """Format the recorded stages per the W3C Server-Timing syntax.

        ``auth;dur=1.2, discover;dur=0.8, …`` — stages in declaration order,
        durations in milliseconds at 0.1 ms resolution. Coarser than the
        recorder's clock on purpose: sub-0.1 ms precision adds nothing for an
        operator and only sharpens a timing side channel.
        """
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
    """The active recorder, or ``None`` when Server-Timing is off."""
    return _current.get()


def stage_start() -> float | None:
    """Start timing a stage: a ``perf_counter`` reading, or ``None`` when off."""
    if _current.get() is None:
        return None
    return time.perf_counter()


def stage_end(stage: ServerTimingStage, started: float | None) -> None:
    """Record the time since ``started`` against ``stage`` (no-op when off)."""
    if started is None:
        return
    recorder = _current.get()
    if recorder is not None:
        recorder.add(stage, (time.perf_counter() - started) * 1000.0)
