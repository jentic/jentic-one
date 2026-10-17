"""Background scanner that runs catalog source syncs and re-resolves.

Each tick it flags enabled, auto-syncing sources whose last sync is older than
their ``max_age_seconds`` (``sync_wanted_at``), then drains every source with
``sync_wanted_at`` or ``reresolve_wanted_at`` set. Read paths only ever set the
flag; the scanner is the one place a background sync runs. The per-source lease
keeps replicas from syncing the same source at once.

A source with ``max_age_seconds: 0`` is never flagged here, so it makes no
background egress; a manual ``POST /catalog:refresh`` still syncs it.

Needs the registry surface and the registry DB only.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

import structlog

from jentic_one.registry.repos.catalog_source_repo import CatalogSourceRepository
from jentic_one.registry.services.catalog.sources.reconcile import (
    ReconcileOutcome,
    reconcile_legacy_source,
)
from jentic_one.registry.services.catalog.sources.sync import (
    CatalogSourceSync,
    SyncOutcome,
)
from jentic_one.shared.db.utils import utcnow

if TYPE_CHECKING:
    from jentic_one.shared.context import Context

logger = structlog.get_logger(__name__)

#: How often the loop wakes. Syncs are rare (max-age gated); this bounds how
#: promptly a wanted flag is noticed.
_TICK_SECONDS = 30.0

#: Staged rows older than this belong to a run that crashed before its apply.
_ABANDONED_STAGING_AGE = timedelta(hours=1)


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
            while self._running:
                await asyncio.sleep(self._tick_seconds)
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

    async def tick(self) -> list[SyncOutcome]:
        """Flag stale sources, then run every wanted sync and re-resolve once."""
        sync = CatalogSourceSync(self._ctx)
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
        await sync.sweep_abandoned_staging(older_than=utcnow() - _ABANDONED_STAGING_AGE)
        return outcomes

    def stop(self) -> None:
        """Signal the scanner to stop after the current tick."""
        self._running = False
