"""Broker-overhead histogram: wall-clock time a sync execution spends outside the upstream.

``broker.execution.overhead_ms`` is derived from the request's
:class:`~jentic_one.broker.core.server_timing.ServerTimingRecorder`, the one
per-request timing record (it also feeds the opt-in ``Server-Timing`` header),
so the two never disagree. The recorder starts when the broker's
Server-Timing middleware receives the request, just inside admission control.

* **buffered** — ``recorder.elapsed_ms() - recorder.duration_ms(UPSTREAM)``,
  taken once the execution record has committed, the response is assembled and
  any ``Idempotency-Key`` is completed. ``jentic-upstream`` there is the whole
  ``broker.execute`` call, so bulkhead waits, every retry attempt, backoff
  sleeps and the circuit check are upstream-window time, never overhead.
* **streaming** — the time before the upstream response headers
  (``elapsed_ms()`` minus ``jentic-upstream`` at that point) plus the
  persistence that runs after the body has been sent (``jentic-persist``,
  recorded into the same recorder). The streamed body itself, including any
  client backpressure, is upstream-window time.

Both are wall-clock, not CPU: awaits on the admin and control databases, the
idempotency store, and the client's request-body upload all count. Only an
execution that got the upstream response headers and was persisted records a
sample. A failure before those headers (connect refused, header timeout, open
circuit, a stream that never opened), a failed persist, and paths that never
call the upstream (async ``202``, approval hold, idempotent replay) record
nothing. A streaming call that fails after the headers (mid-body reset,
transfer deadline) persists a FAILED record and so still records its
pre-header time plus that persistence.
"""

from __future__ import annotations

from typing import Literal

import structlog

from jentic_one.broker.core.server_timing import ServerTimingRecorder, ServerTimingStage
from jentic_one.shared.metrics import get_meter

__all__ = ["record_overhead", "upstream_exclusive_elapsed_ms"]

logger = structlog.get_logger(__name__)

_meter = get_meter("broker")
_execution_overhead = _meter.create_histogram(
    "broker.execution.overhead_ms",
    unit="ms",
    description=(
        "Wall-clock broker time per sync execution outside the upstream window, in "
        "milliseconds: request received to the upstream call, plus the upstream "
        "call's end to the execution record being persisted. Filter by mode: "
        "streaming persistence runs after the client has the body"
    ),
)


def upstream_exclusive_elapsed_ms(recorder: ServerTimingRecorder) -> float:
    """Milliseconds since the request was received, minus the recorded upstream window."""
    return recorder.elapsed_ms() - recorder.duration_ms(ServerTimingStage.UPSTREAM)


def record_overhead(
    overhead_ms: float, operation_id: str | None, mode: Literal["buffered", "streaming"]
) -> None:
    """Record one overhead sample (labels: ``operation``, ``mode``).

    The upstream window is a sub-span of the request's elapsed time, so a
    negative value means the stages are mis-wired: it is logged and dropped
    rather than clamped, so the histogram never hides the fault.
    """
    if overhead_ms < 0:
        logger.warning(
            "broker_overhead_negative",
            overhead_ms=overhead_ms,
            operation_id=operation_id,
            mode=mode,
        )
        return
    _execution_overhead.record(overhead_ms, {"operation": operation_id or "", "mode": mode})
