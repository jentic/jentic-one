"""Supervised flush loop that drains the telemetry queue and POSTs batches.

Application lifespan (``_start_telemetry`` / ``_stop_telemetry`` in
``shared/web/app_factory.py``) owns one long-lived task: it starts the loop on
startup, drains queued events during shutdown, cancels any remaining work, and
closes the client. Periodic sends and the final drain are best-effort, and send
failures are logged and swallowed so telemetry does not terminate request flow.
"""

from __future__ import annotations

import asyncio

import structlog

from jentic_one.shared.telemetry.client import TelemetryClient
from jentic_one.shared.telemetry.events import TelemetryEvent
from jentic_one.shared.telemetry.sink import TelemetrySink

logger = structlog.get_logger(__name__)


class TelemetryFlushLoop:
    """Periodically batch + POST queued telemetry events."""

    def __init__(
        self,
        sink: TelemetrySink,
        client: TelemetryClient,
        *,
        flush_interval_s: float,
        max_batch: int,
    ) -> None:
        self._sink = sink
        self._client = client
        self._flush_interval_s = flush_interval_s
        self._max_batch = max(1, max_batch)
        self._running = False

    def _drain_batch(self) -> list[TelemetryEvent]:
        """Pop up to ``max_batch`` events without blocking."""
        batch: list[TelemetryEvent] = []
        while len(batch) < self._max_batch:
            try:
                batch.append(self._sink.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    async def _flush_once(self) -> None:
        """Drain the queue (in batches) and POST until it is empty."""
        while True:
            batch = self._drain_batch()
            if not batch:
                return
            await self._client.send(batch)

    async def run(self) -> None:
        """Main loop — flush on an interval until cancelled."""
        self._running = True
        logger.info("telemetry_flush_loop_started")
        try:
            while self._running:
                await asyncio.sleep(self._flush_interval_s)
                try:
                    await self._flush_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("telemetry_flush_error")
        except asyncio.CancelledError:
            logger.info("telemetry_flush_loop_cancelled")
        finally:
            self._running = False
            logger.info("telemetry_flush_loop_stopped")

    def stop(self) -> None:
        self._running = False

    async def drain(self) -> None:
        """Flush whatever is queued one last time (called on shutdown)."""
        self._running = False
        try:
            await self._flush_once()
        except Exception:
            logger.warning("telemetry_drain_flush_failed")
