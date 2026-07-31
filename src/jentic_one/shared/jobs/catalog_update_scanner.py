"""Background scanner that drives the Flow-3 catalog update-notify sweep.

Phase 2 shipped the sweep as a fire-and-forget piggyback on the lazy manifest
refresh (``CatalogService.trigger_update_notify_sweep``) — cheap, but its cadence is
whatever read traffic happens to be, so a quiet install could go arbitrarily long
without a check. This scanner gives the sweep an owned cadence: a long-lived task
(modelled on ``CredentialExpiryScanner``) that runs one sweep per
``update_check_interval_seconds`` regardless of read traffic.

``CatalogService`` is per-request/short-lived (it parks fire-and-forget tasks in a
module set precisely because ``self`` can be GC'd), so the scanner constructs a fresh
``CatalogService(ctx)`` each tick and *awaits* the sweep to completion rather than
detaching it. The per-API ``last_checked_at < interval`` gate inside ``_probe_one``
means the scanner and the manual ``POST /catalog:refresh`` trigger coexist without
double-probing within an interval.

Gated on both the ``registry`` DB (candidates + check rows) and the ``admin`` DB
(event emit); a standalone-registry deployment gets no scanner.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from jentic_one.registry.services.catalog.service import CatalogService

if TYPE_CHECKING:
    from jentic_one.shared.context import Context

logger = structlog.get_logger(__name__)

#: How often the loop wakes to check whether a sweep is due. The real cadence is the
#: config's ``update_check_interval_seconds`` (default daily); this just bounds how
#: promptly a config/kill-switch change or shutdown is noticed. Kept small enough to
#: react within a minute, large enough not to busy-spin.
_TICK_SECONDS = 30.0


class CatalogUpdateScanner:
    """Periodically runs the catalog update-notify sweep on an owned cadence."""

    def __init__(
        self,
        ctx: Context,
        *,
        tick_seconds: float = _TICK_SECONDS,
    ) -> None:
        self._ctx = ctx
        self._cfg = ctx.config.catalog
        self._tick_seconds = tick_seconds
        self._running = False
        self._last_swept_at: float | None = None

    async def run(self) -> None:
        """Main loop — run a sweep whenever one is due, until cancelled.

        A single tick must never kill the loop: a transient DB/upstream error during a
        sweep is caught and logged so the scanner keeps running (mirrors
        ``CredentialExpiryScanner.run``). The sweep itself is best-effort per API.
        """
        self._running = True
        logger.info("catalog_update_scanner_started")
        try:
            while self._running:
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("catalog_update_scanner_tick_error")
                await asyncio.sleep(self._tick_seconds)
        except asyncio.CancelledError:
            logger.info("catalog_update_scanner_cancelled")
        finally:
            self._running = False
            logger.info("catalog_update_scanner_stopped")

    async def _tick(self) -> None:
        interval = self._cfg.update_check_interval_seconds
        if interval <= 0:
            # Kill switch (air-gapped installs): never sweep.
            return
        loop_now = asyncio.get_running_loop().time()
        if self._last_swept_at is not None and (loop_now - self._last_swept_at) < interval:
            return
        # Stamp before running so a long sweep doesn't immediately re-trigger; the
        # per-API DB gate is the authoritative dedupe across restarts.
        self._last_swept_at = loop_now
        await self.sweep()

    async def sweep(self) -> None:
        """Run one update-notify sweep to completion (awaitable)."""
        await CatalogService(self._ctx).run_update_sweep()

    def stop(self) -> None:
        """Signal the scanner to stop after the current tick."""
        self._running = False
