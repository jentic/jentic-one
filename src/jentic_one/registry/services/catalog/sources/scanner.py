"""Background scanner that runs catalog source syncs and re-resolves.

Each tick it turns due sync requests (``POST /catalog/sources/{slug}:sync`` and
``:notify``, recorded as ``sync_due_at``) on sources no run holds into wanted
syncs, flags enabled, auto-syncing sources whose last sync is older than their
``max_age_seconds`` (``sync_wanted_at``), then drains every source with
``sync_wanted_at`` or ``reresolve_wanted_at`` set. Read paths and request
handlers only ever set flags; the scanner is the one place a background sync
runs. The per-source lease keeps replicas from syncing the same source at once.

A request handler in this process wakes the scanner (``wake_source_sync_scanner``)
so a requested sync starts without waiting out the tick; other replicas notice
the request on their next tick, or when it falls due.

A source with ``max_age_seconds: 0`` is never flagged here, so it makes no
background egress; a requested ``:sync`` still syncs it.

Needs the registry surface and the registry DB only.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import TYPE_CHECKING

import structlog

from jentic_one.registry.repos.catalog_source_repo import CatalogSourceRepository
from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.registry.services.catalog.sources.reconcile import (
    ReconcileOutcome,
    reconcile_legacy_source,
)
from jentic_one.registry.services.catalog.sources.sync import (
    CatalogSourceSync,
    SyncOutcome,
)

if TYPE_CHECKING:
    from jentic_one.shared.context import Context

logger = structlog.get_logger(__name__)

#: How often the loop wakes. Syncs are rare (max-age gated); this bounds how
#: promptly a wanted flag is noticed.
_TICK_SECONDS = 30.0

#: Finished ``catalog_sync_runs`` rows are kept this long.
_SYNC_RUN_RETENTION = timedelta(days=30)

#: Shortest wait between ticks, so a burst of wake-ups cannot spin the loop.
_MIN_WAIT_SECONDS = 1.0


class _Wakeup:
    """Wakes this process's scanner; one event per event loop (an event binds to its loop)."""

    def __init__(self) -> None:
        self._events: dict[asyncio.AbstractEventLoop, asyncio.Event] = {}

    def _event(self, loop: asyncio.AbstractEventLoop) -> asyncio.Event:
        event = self._events.get(loop)
        if event is None:
            event = self._events[loop] = asyncio.Event()
        return event

    def set(self) -> None:
        for loop, event in list(self._events.items()):
            if loop.is_closed():
                self._events.pop(loop, None)
            else:
                loop.call_soon_threadsafe(event.set)

    async def wait(self, timeout: float) -> None:
        event = self._event(asyncio.get_running_loop())
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), timeout=timeout)
        event.clear()


def _wakeup(ctx: Context) -> _Wakeup:
    return ctx.resource("catalog.source_sync_wakeup", _Wakeup)


def wake_source_sync_scanner(ctx: Context) -> None:
    """Wake this process's scanner to look for due sync requests now (no-op without one)."""
    _wakeup(ctx).set()


class CatalogSourceSyncScanner:
    """Periodically drains wanted catalog source syncs and re-resolves."""

    def __init__(self, ctx: Context, *, tick_seconds: float = _TICK_SECONDS) -> None:
        self._ctx = ctx
        self._tick_seconds = tick_seconds
        self._running = False

    async def run(self) -> None:
        """Boot once, then one tick per interval until cancelled; a failing tick never ends it.

        The first tick waits one interval, so startup itself makes no egress.
        """
        self._running = True
        logger.info("catalog_source_sync_scanner_started")
        try:
            try:
                await self.boot()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("catalog_source_sync_scanner_boot_error")
            wakeup = _wakeup(self._ctx)
            while self._running:
                await wakeup.wait(await self._next_wait())
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("catalog_source_sync_scanner_tick_error")
        except asyncio.CancelledError:
            logger.info("catalog_source_sync_scanner_cancelled")
        finally:
            self._running = False
            logger.info("catalog_source_sync_scanner_stopped")

    async def boot(self) -> ReconcileOutcome:
        """Register the configured sources, then run the one-shot legacy reconcile."""
        sync = CatalogSourceSync(self._ctx)
        await sync.register_sources()
        return await reconcile_legacy_source(sync)

    async def _next_wait(self) -> float:
        """Seconds until the next tick: the tick interval, or sooner when a request falls due."""
        try:
            async with self._ctx.registry_db.session() as session:
                due = await CatalogSourceRepository.next_sync_due_at(session)
        except Exception:
            logger.warning("catalog_source_sync_scanner_due_check_failed", exc_info=True)
            return self._tick_seconds
        if due is None:
            return self._tick_seconds
        until = (due - utcnow()).total_seconds()
        return max(_MIN_WAIT_SECONDS, min(self._tick_seconds, until))

    async def tick(self) -> list[SyncOutcome]:
        """Start due requests, flag stale sources, then run each wanted sync/re-resolve once."""
        sync = CatalogSourceSync(self._ctx)
        async with self._ctx.registry_db.transaction() as session:
            requested = set(await CatalogSourceRepository.promote_due_syncs(session))
        await sync.mark_stale_wanted()
        async with self._ctx.registry_db.session() as session:
            rows = await CatalogSourceRepository.list_all(session)
        outcomes: list[SyncOutcome] = []
        for row in rows:
            if row.slug not in sync.sources:
                continue
            if row.sync_wanted_at is not None:
                outcomes.append(await sync.sync_source(row.slug))
        for row in rows:
            if row.slug in sync.sources and row.reresolve_wanted_at is not None:
                await sync.reresolve_source(row.slug)
        now = utcnow()
        await sync.sweep_abandoned_staging(
            older_than=now - _ABANDONED_STAGING_AGE,
            runs_finished_before=now - _SYNC_RUN_RETENTION,
        )
        # A requested sync is an explicit "check upstream now", so it also starts
        # the update-notify sweep (fire-and-forget), as the removed
        # ``POST /catalog:refresh`` did. Whatever the run's outcome: a spec can
        # change upstream while its listing does not.
        if any(o.slug in requested for o in outcomes):
            CatalogService(self._ctx).trigger_update_notify_sweep()
        return outcomes

    def stop(self) -> None:
        """Signal the scanner to stop after the current tick."""
        self._running = False
