"""In-memory telemetry sink — a bounded, non-blocking event buffer.

The sink is the boundary between synchronous emit points and the async flush
loop. ``record`` is **synchronous, non-blocking, and never raises**: when
telemetry is disabled it is a no-op, and when the queue is full it drops the
event (telemetry is best-effort and must never affect request flow). Only
``emit_event`` calls ``record`` — services never touch the sink directly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime

import structlog

from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.models.events import EventTag
from jentic_one.shared.telemetry.events import TelemetryEvent, TelemetryEventName

logger = structlog.get_logger(__name__)


def _coerce_actor_type(value: str | None) -> ActorType | None:
    """Map a caller-supplied actor type onto the closed ``ActorType`` enum.

    Returns ``None`` for a missing value or one that is not an enum member (a
    non-enum value is dropped, logged once, rather than forwarded) — this is the
    structural guard that keeps a free-form label/email out of the wire payload.
    """
    if value is None:
        return None
    try:
        return ActorType(value)
    except ValueError:
        logger.warning("telemetry_actor_type_dropped", actor_type=str(value))
        return None


class TelemetrySink:
    """Bounded queue of telemetry events drained by the flush loop."""

    def __init__(self, *, enabled: bool, queue_max: int) -> None:
        self._enabled = enabled
        self._dropped = 0
        # Bounded so a stalled/unreachable endpoint can never grow memory without
        # limit. Under saturation ``put_nowait`` raises and we drop the *newest*
        # event (the one being recorded) rather than block the emitter — telemetry
        # is best-effort and must never stall the request path.
        self._queue: asyncio.Queue[TelemetryEvent] = asyncio.Queue(maxsize=max(1, queue_max))

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def queue(self) -> asyncio.Queue[TelemetryEvent]:
        return self._queue

    def record(
        self,
        name: TelemetryEventName | str,
        tags: Iterable[EventTag] = (),
        actor_type: str | None = None,
    ) -> None:
        """Enqueue an event. No-op when disabled; drops (never blocks) when full.

        ``actor_type`` is coerced to the closed ``ActorType`` enum here: a value
        that is not a member is dropped to ``None`` (logged once) rather than put
        on the wire. This enforces — not just documents — that ``actor_type`` can
        only ever be one of the closed-enum kinds, so a raw label/email from a
        future caller cannot leak to Jentic.
        """
        if not self._enabled:
            return
        event = TelemetryEvent(
            name=name,
            tags=tuple(tags),
            ts=datetime.now(UTC),
            actor_type=_coerce_actor_type(actor_type),
        )
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1
            # Throttle: only log on the first drop of a saturation episode.
            if self._dropped == 1:
                logger.warning("telemetry_queue_full_dropping", telemetry_event=str(name))

    @property
    def dropped(self) -> int:
        """Count of events dropped due to a full queue (diagnostics/tests)."""
        return self._dropped


# --- Process-global active sink ------------------------------------------
#
# ``emit_event`` is the single entry point to telemetry but receives only a raw
# ``AsyncSession`` (no Context) — the worker loop and broker execution path have
# no Context in scope. So the lifespan registers the active sink here and
# ``emit_event`` reads it. There is one app per process, so a module global is
# the right scope; it is None whenever telemetry is disabled or not yet wired.

_active_sink: TelemetrySink | None = None


def set_active_sink(sink: TelemetrySink | None) -> None:
    """Register (or clear) the process-wide telemetry sink. Called by the lifespan."""
    global _active_sink
    _active_sink = sink


def get_active_sink() -> TelemetrySink | None:
    """Return the active telemetry sink, or None when telemetry is off/unwired."""
    return _active_sink
