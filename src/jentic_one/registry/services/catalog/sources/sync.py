"""Per-source catalog sync: lease, staging, one short apply, and re-resolve.

A sync run for one source:

1. claims the source's lease (one run per source across processes; a crashed
   run's lease lapses after ``sync_lease_seconds``) and renews it from a
   heartbeat while it works;
2. iterates the source's listing and stages every change in
   ``catalog_staged_entries``, in batches, holding no long transaction;
3. applies in one transaction under a per-source lock (a Postgres advisory lock,
   or an in-process lock on SQLite) whose first statement re-checks the lease: a
   run that lost it applies nothing. A run older than the last applied one is
   dropped as superseded; the mass-removal guard may hold the
   whole run (``suspect_empty``); otherwise connector text is sanitised, the
   ``catalog_api_id`` resolved, rows upserted with ``seen_at`` = the run's start,
   and, only for a full listing confirmed by its summary, rows the run did not
   see are tombstoned. Digests are recomputed and the lease released.

The run's start time comes from the database clock on Postgres, so replicas
with skewed clocks still order runs and tombstones the same way.

A listing that raises applies nothing: its staged rows are dropped and the
source keeps its last good rows. A run whose heartbeat cannot renew the lease
stops listing and ends ``lease_lost``. A caller may cap the time spent waiting
on the listing itself (``listing_timeout_s``; staging writes and the apply are
not counted): a listing over it ends ``listing_timeout``. A run cancelled while
listing ends ``cancelled``; one cancelled while applying finishes the apply.
Both failures count toward the source's backoff and release the lease.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
import weakref
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog

from jentic_one.registry.core.schema.catalog_entries import CatalogEntryRow
from jentic_one.registry.core.schema.catalog_sources import (
    SOURCE_STATUS_FAILED,
    SOURCE_STATUS_SUSPECT_EMPTY,
    CatalogSourceRow,
)
from jentic_one.registry.core.schema.catalog_staged_entries import (
    STAGED_OP_REMOVE,
    STAGED_OP_UPSERT,
    CatalogStagedEntry,
)
from jentic_one.registry.core.schema.catalog_sync_runs import (
    RUN_STATUS_APPLIED,
    RUN_STATUS_FAILED,
    RUN_STATUS_SUPERSEDED,
    RUN_STATUS_SUSPECT_EMPTY,
)
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.catalog_entry_repo import (
    CatalogEntryRepository,
    CatalogSyncRunRepository,
)
from jentic_one.registry.repos.catalog_source_repo import CatalogSourceRepository
from jentic_one.registry.services.catalog.entries import (
    CatalogApiIdMapping,
    CatalogApiIdResolver,
    make_entry_id,
    resolver_inputs_digest,
)
from jentic_one.registry.services.catalog.sources import digests, metrics, sanitize
from jentic_one.registry.services.catalog.sources.deps import (
    SourceDeps,
    build_source_deps,
    ensure_source_buckets,
)
from jentic_one.registry.services.catalog.sources.protocol import (
    CatalogSource,
    ListingTooLargeError,
    ListStream,
    Removed,
    SourceUnavailableError,
    SpecGoneError,
    Upserted,
)
from jentic_one.registry.services.catalog.sources.registry import resolve_catalog_source_kind
from jentic_one.shared.config import CatalogSourceConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.utils import utcnow

logger = structlog.get_logger(__name__)

#: Staged changes are written in batches of this many rows.
STAGE_BATCH_SIZE = 1000

#: Longest ``external_id`` a row can store (``entry_id`` is capped at 320).
_EXTERNAL_ID_MAX_LENGTH = 287

DepsFactory = Callable[[CatalogSourceConfig], SourceDeps]

#: Outcome statuses besides the ``catalog_sync_runs`` ones.
OUTCOME_BUSY = "busy"
OUTCOME_DISABLED = "disabled"

ERROR_LEASE_LOST = "lease_lost"
ERROR_LISTING_TIMEOUT = "listing_timeout"
ERROR_CANCELLED = "cancelled"

_END = object()

#: Staged rows and ``running`` runs older than this, whose run no longer holds
#: its source's lease, belong to a process that died mid-run; a lease that
#: expired longer ago than this is cleared.
ABANDONED_STAGING_AGE = timedelta(hours=1)


#: SQLite's per-source apply locks, one set per event loop (an ``asyncio.Lock``
#: belongs to the loop it first waits on).
_SQLITE_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def _sqlite_lock(slug: str) -> asyncio.Lock:
    locks = _SQLITE_LOCKS.setdefault(asyncio.get_running_loop(), {})
    return locks.setdefault(slug, asyncio.Lock())


#: ``SourceWarning.code`` values.
WARNING_MAPPING_UNMATCHED = "mapping_unmatched"
WARNING_OWNERSHIP_CONFLICT = "ownership_conflict"


@dataclass(frozen=True, slots=True)
class OwnershipConflict:
    """An imported API holding a ``catalog_api_id`` a curated source now owns."""

    entry_id: str
    catalog_api_id: str
    vendor: str
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class SourceWarning:
    """An operator-facing problem with one source's configuration or rows."""

    code: str
    external_id: str
    catalog_api_id: str
    #: ``vendor/name/version`` of the local API, for an ownership conflict.
    local_api: str | None = None


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """What one sync attempt did."""

    slug: str
    status: str
    run_id: str | None = None
    staged: int = 0
    upserted: int = 0
    removed: int = 0
    error_code: str | None = None


class _ListingError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _BoundedStream:
    """``stream`` with the time spent awaiting it (the listing fetch) capped in total.

    Only the stream's own awaits count, so writing staged batches between
    entries does not eat the budget.
    """

    def __init__(self, stream: ListStream, budget_s: float) -> None:
        self._stream = stream
        self._left = budget_s
        self.is_full = stream.is_full

    async def _bounded(self, awaitable: Any) -> Any:
        start = time.monotonic()
        try:
            async with asyncio.timeout(max(self._left, 0.0)):
                return await awaitable
        except TimeoutError:
            raise _ListingError(ERROR_LISTING_TIMEOUT) from None
        finally:
            self._left -= time.monotonic() - start

    async def _iter(self) -> AsyncIterator[Any]:
        it = aiter(self._stream)
        while (change := await self._bounded(anext(it, _END))) is not _END:
            yield change

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iter()

    async def summary(self) -> Any:
        return await self._bounded(self._stream.summary())


def _valid_external_id(value: str) -> bool:
    if not value or len(value) > _EXTERNAL_ID_MAX_LENGTH:
        return False
    return not any(ord(ch) < 0x20 or ord(ch) == 0x7F or 0xD800 <= ord(ch) <= 0xDFFF for ch in value)


def _record_detached_apply(
    task: asyncio.Future[SyncOutcome], slug: str, run_id: str, clock_start: float
) -> None:
    """Log and meter an apply that finished after its caller was cancelled."""
    if task.cancelled():
        logger.warning("catalog_source_detached_apply_cancelled", source=slug, run_id=run_id)
        return
    if (exc := task.exception()) is not None:
        # The run stays ``running`` until the sweep closes it as abandoned.
        logger.warning(
            "catalog_source_detached_apply_failed", source=slug, run_id=run_id, exc_info=exc
        )
        return
    outcome = task.result()
    logger.info(
        "catalog_source_detached_apply_done", source=slug, run_id=run_id, status=outcome.status
    )
    metrics.record_sync(slug, outcome.status, time.monotonic() - clock_start, outcome.error_code)


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, _ListingError):
        return exc.code
    if isinstance(exc, SourceUnavailableError):
        return "source_unavailable"
    if isinstance(exc, SpecGoneError):
        return "source_gone"
    if isinstance(exc, ListingTooLargeError):
        return "listing_too_large"
    return "listing_error"


def guard_trips(
    *, live: int, removals: int, max_removal_ratio: float, min_removal_count: int
) -> bool:
    """Whether a run removing ``removals`` of ``live`` rows must be held.

    Removing every row of a non-empty source always trips; otherwise the run
    trips only above both the ratio and the count floor. A source with no live
    rows has nothing to remove and never trips.
    """
    if live <= 0 or removals <= 0:
        return False
    if removals >= live:
        return True
    return removals / live > max_removal_ratio and removals > min_removal_count


class CatalogSourceSync:
    """Runs syncs and re-resolves for the configured catalog sources."""

    def __init__(self, ctx: Context, *, deps_factory: DepsFactory | None = None) -> None:
        self._ctx = ctx
        self._catalog = ctx.config.catalog
        self._sources: dict[str, CatalogSourceConfig] = {s.slug: s for s in self._catalog.sources}
        self._mappings = [
            CatalogApiIdMapping.from_config(m) for m in self._catalog.catalog_api_id_mappings
        ]
        self._deps_factory = deps_factory or self._default_deps

    @property
    def ctx(self) -> Context:
        return self._ctx

    @property
    def sources(self) -> Mapping[str, CatalogSourceConfig]:
        return self._sources

    def _default_deps(self, cfg: CatalogSourceConfig) -> SourceDeps:
        return build_source_deps(self._ctx, cfg)

    def build_source(self, cfg: CatalogSourceConfig) -> CatalogSource:
        factory = resolve_catalog_source_kind(cfg.kind)
        return factory.build(cfg, deps=self._deps_factory(cfg))

    @contextlib.asynccontextmanager
    async def _source_transaction(self, slug: str) -> AsyncIterator[Any]:
        """A transaction holding the source's apply lock (applies and re-resolves).

        Postgres takes a transaction-scoped advisory lock; SQLite, a single
        process, holds an in-process per-source lock around the transaction.
        """
        sqlite = self._ctx.registry_db.backend.dialect_name == "sqlite"
        local: contextlib.AbstractAsyncContextManager[Any] = (
            _sqlite_lock(slug) if sqlite else contextlib.nullcontext()
        )
        async with local, self._ctx.registry_db.transaction() as session:
            await CatalogSourceRepository.lock_for_apply(session, slug)
            yield session

    def current_resolver_inputs_digest(self) -> str:
        return resolver_inputs_digest(list(self._sources.values()), self._mappings)

    def _curated_slugs(self) -> list[str]:
        return [s.slug for s in self._sources.values() if s.trust == "curated" and s.enabled]

    def _non_curated_slugs(self) -> list[str]:
        return [s.slug for s in self._sources.values() if s.trust != "curated"]

    async def _resolver(self, session: Any) -> CatalogApiIdResolver:
        curated_owned = await CatalogEntryRepository.live_catalog_api_ids(
            session, self._curated_slugs()
        )
        return CatalogApiIdResolver(
            sources=self._sources, mappings=self._mappings, curated_owned=curated_owned
        )

    # ── source state rows ──────────────────────────────────────────────────────

    async def register_sources(self) -> None:
        """Create or refresh the state row of every configured source.

        Also flags every non-curated source whose rows were resolved under
        different resolver inputs (mapping rows, a source's trust or enabled
        flag) for a re-resolve, so a config change takes effect without a sync.
        """
        current = self.current_resolver_inputs_digest()
        async with self._ctx.registry_db.transaction() as session:
            for cfg in self._sources.values():
                row = await CatalogSourceRepository.upsert_config(
                    session,
                    slug=cfg.slug,
                    kind=cfg.kind,
                    config_digest=digests.config_digest(cfg),
                    enabled=cfg.enabled,
                    visibility=cfg.visibility,
                    trust=cfg.trust,
                    precedence=cfg.precedence,
                )
                if row.resolver_inputs_digest == current:
                    continue
                if row.live_entries == 0:
                    row.resolver_inputs_digest = current
                else:
                    # Curated sources too: one that just became curated (or was
                    # re-enabled) still stores ids resolved as non-curated.
                    row.reresolve_wanted_at = utcnow()
                    logger.info("catalog_source_reresolve_flagged", source=cfg.slug)
            (await self._resolver(session)).report_unmatched()
        await ensure_source_buckets(self._ctx)

    async def mark_stale_wanted(self) -> int:
        """Flag enabled, auto-syncing sources whose last sync is older than their max age.

        The read path calls this instead of syncing inline; the scanner drains it.
        """
        now = utcnow()
        async with self._ctx.registry_db.transaction() as session:
            rows = {r.slug: r for r in await CatalogSourceRepository.list_all(session)}
            stale = [
                cfg.slug
                for cfg in self._sources.values()
                if (row := rows.get(cfg.slug)) is not None
                and is_due(cfg, row, now=now)
                and row.sync_wanted_at is None
            ]
            return await CatalogSourceRepository.mark_sync_wanted(session, stale)

    # ── sync ───────────────────────────────────────────────────────────────────

    async def sync_source(
        self,
        slug: str,
        *,
        forced: bool = False,
        stream: ListStream | None = None,
        listing_timeout_s: float | None = None,
    ) -> SyncOutcome:
        """Claim the lease and sync one source; ``busy`` when another run holds it.

        ``stream`` replaces the source's own listing (a caller that already holds
        the listing document, such as the snapshot refresh, passes it here).
        ``listing_timeout_s`` caps the time spent awaiting the listing.
        """
        cfg = self._sources[slug]
        if not cfg.enabled:
            return SyncOutcome(slug=slug, status=OUTCOME_DISABLED)
        run_id = generate_ksuid("csyn")
        async with self._ctx.registry_db.transaction() as session:
            claimed = await CatalogSourceRepository.claim_lease(
                session, slug=slug, owner=run_id, lease_seconds=cfg.sync_lease_seconds
            )
            row = await CatalogSourceRepository.get(session, slug) if claimed else None
            watermark = row.watermark if row is not None else None
            # A pending forced-sync request rides on whichever run claims next.
            if claimed and await CatalogSourceRepository.take_forced(session, slug):
                forced = True
        if not claimed:
            return SyncOutcome(slug=slug, status=OUTCOME_BUSY)
        if stream is None:
            try:
                stream = self.build_source(cfg).list_entries(since=watermark)
            except Exception:
                logger.exception("catalog_source_build_failed", source=slug)
                outcome = await self._fail(
                    slug, run_id, status=RUN_STATUS_FAILED, error_code="build_error"
                )
                metrics.record_sync(slug, outcome.status, 0.0, outcome.error_code)
                return outcome
        if listing_timeout_s is not None:
            stream = _BoundedStream(stream, listing_timeout_s)
        return await self.run_claimed(cfg, stream, run_id=run_id, forced=forced)

    async def run_claimed(
        self, cfg: CatalogSourceConfig, stream: ListStream, *, run_id: str, forced: bool = False
    ) -> SyncOutcome:
        """Stage and apply ``stream`` for a source whose lease ``run_id`` already holds."""
        slug = cfg.slug
        clock_start = time.monotonic()
        async with self._ctx.registry_db.transaction() as session:
            started_at = await CatalogSourceRepository.run_clock(session)
            await CatalogSyncRunRepository.create(
                session, run_id=run_id, slug=slug, started_at=started_at, forced=forced
            )
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(cfg, run_id, lease_lost))
        staging = asyncio.create_task(self._stage(slug, stream, run_id))
        lost = asyncio.create_task(lease_lost.wait())
        try:
            try:
                await asyncio.wait({staging, lost}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (heartbeat, lost, staging):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(heartbeat, lost, staging, return_exceptions=True)
        except asyncio.CancelledError:
            # The caller gave up mid-listing: end the run as failed (so backoff
            # applies) and release the lease, even though this task is cancelled.
            logger.warning("catalog_source_run_cancelled", source=slug, run_id=run_id)
            await asyncio.shield(
                self._fail(slug, run_id, status=RUN_STATUS_FAILED, error_code=ERROR_CANCELLED)
            )
            raise

        if lease_lost.is_set():
            logger.warning("catalog_source_run_lease_lost", source=slug, run_id=run_id)
            outcome = await self._fail(
                slug, run_id, status=RUN_STATUS_FAILED, error_code=ERROR_LEASE_LOST
            )
        elif (exc := staging.exception()) is not None:
            code = _error_code(exc)
            logger.warning(
                "catalog_source_listing_failed",
                source=slug,
                error_code=code,
                error_type=type(exc).__name__,
            )
            outcome = await self._fail(slug, run_id, status=RUN_STATUS_FAILED, error_code=code)
        else:
            staged, is_full, watermark = staging.result()
            apply = asyncio.ensure_future(
                self._apply(
                    cfg,
                    run_id=run_id,
                    started_at=started_at,
                    is_full=is_full,
                    watermark=watermark,
                    forced=forced,
                    staged=staged,
                )
            )
            try:
                # One short transaction: a cancelled caller lets it finish
                # rather than leave the lease held until it lapses.
                outcome = await asyncio.shield(apply)
            except asyncio.CancelledError:
                apply.add_done_callback(
                    lambda task: _record_detached_apply(task, slug, run_id, clock_start)
                )
                raise
            except Exception:
                logger.exception("catalog_source_apply_failed", source=slug, run_id=run_id)
                outcome = await self._fail(
                    slug, run_id, status=RUN_STATUS_FAILED, error_code="apply_error", staged=staged
                )
        metrics.record_sync(
            slug, outcome.status, time.monotonic() - clock_start, outcome.error_code
        )
        return outcome

    async def _heartbeat(
        self, cfg: CatalogSourceConfig, run_id: str, lease_lost: asyncio.Event
    ) -> None:
        """Renew the lease every third of its length; any failure to renew loses it."""
        interval = max(1.0, cfg.sync_lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                async with self._ctx.registry_db.transaction() as session:
                    held = await CatalogSourceRepository.renew_lease(
                        session, slug=cfg.slug, owner=run_id, lease_seconds=cfg.sync_lease_seconds
                    )
            except Exception:
                logger.warning("catalog_source_lease_renew_failed", source=cfg.slug, exc_info=True)
                held = False
            if not held:
                logger.warning("catalog_source_lease_lost", source=cfg.slug, run_id=run_id)
                lease_lost.set()
                return

    async def _stage(
        self, slug: str, stream: ListStream, run_id: str
    ) -> tuple[int, bool, str | None]:
        """Stage every change of ``stream``; returns (staged, is_full, watermark)."""
        is_full = bool(stream.is_full)
        seen: set[str] = set()
        batch: list[CatalogStagedEntry] = []
        staged = 0
        skipped = 0

        async def _flush() -> None:
            if not batch:
                return
            async with self._ctx.registry_db.transaction() as session:
                await CatalogSyncRunRepository.stage(session, batch)
            batch.clear()

        async for change in stream:
            external_id = (
                change.entry.external_id if isinstance(change, Upserted) else change.external_id
            )
            if not _valid_external_id(external_id) or external_id in seen:
                skipped += 1
                continue
            seen.add(external_id)
            batch.append(_staged_row(run_id, change))
            staged += 1
            if len(batch) >= STAGE_BATCH_SIZE:
                await _flush()
        await _flush()
        summary = await stream.summary()
        if summary.is_full != is_full:
            raise _ListingError("summary_mismatch")
        if skipped:
            logger.warning("catalog_source_entries_skipped", source=slug, skipped=skipped)
        return staged, is_full, summary.watermark

    async def _fail(
        self, slug: str, run_id: str, *, status: str, error_code: str | None, staged: int = 0
    ) -> SyncOutcome:
        """End a run that applied nothing, in a fresh transaction.

        The source's failure count (and so the scanner's backoff) only moves when
        this run still held the lease; a run that lost it leaves the source to
        the run that holds it now.
        """
        async with self._ctx.registry_db.transaction() as session:
            await CatalogSyncRunRepository.delete_staged(session, run_id)
            await CatalogSyncRunRepository.finish(
                session,
                run_id,
                status=status,
                error_code=error_code,
                staged=staged,
                finished_at=utcnow(),
            )
            if await CatalogSourceRepository.release_lease(session, slug=slug, owner=run_id):
                source_status = (
                    SOURCE_STATUS_SUSPECT_EMPTY
                    if status == RUN_STATUS_SUSPECT_EMPTY
                    else SOURCE_STATUS_FAILED
                )
                await CatalogSourceRepository.record_failure(
                    session, slug=slug, status=source_status
                )
        return SyncOutcome(
            slug=slug, status=status, run_id=run_id, staged=staged, error_code=error_code
        )

    async def _release(self, slug: str, run_id: str) -> None:
        async with self._ctx.registry_db.transaction() as session:
            await CatalogSourceRepository.release_lease(session, slug=slug, owner=run_id)

    async def _apply(
        self,
        cfg: CatalogSourceConfig,
        *,
        run_id: str,
        started_at: datetime,
        is_full: bool,
        watermark: str | None,
        forced: bool,
        staged: int,
    ) -> SyncOutcome:
        slug = cfg.slug

        async def _end(session: Any, status: str, error: str | None) -> SyncOutcome:
            await CatalogSyncRunRepository.delete_staged(session, run_id)
            await CatalogSyncRunRepository.finish(
                session, run_id, status=status, error_code=error, finished_at=utcnow()
            )
            return SyncOutcome(
                slug=slug, status=status, run_id=run_id, staged=staged, error_code=error
            )

        async with self._source_transaction(slug) as session:
            # The lease is re-checked (and row-locked on Postgres) under the apply
            # lock, so a run whose lease lapsed and was claimed elsewhere never
            # applies over the new owner.
            if not await CatalogSourceRepository.renew_lease(
                session, slug=slug, owner=run_id, lease_seconds=cfg.sync_lease_seconds
            ):
                logger.warning("catalog_source_apply_lease_lost", source=slug, run_id=run_id)
                return await _end(session, RUN_STATUS_FAILED, ERROR_LEASE_LOST)
            source = await CatalogSourceRepository.get(session, slug)
            if source is None:
                return await _end(session, RUN_STATUS_FAILED, ERROR_LEASE_LOST)
            if source.last_applied_run_id is not None:
                last = await CatalogSyncRunRepository.get(session, source.last_applied_run_id)
                if last is not None and last.started_at > started_at:
                    outcome = await _end(session, RUN_STATUS_SUPERSEDED, None)
                    await CatalogSourceRepository.release_lease(session, slug=slug, owner=run_id)
                    return outcome

            rows = await CatalogEntryRepository.rows_for_source(session, slug)
            by_external = {r.external_id: r for r in rows}
            live = {r.external_id for r in rows if r.removed_at is None}
            staged_rows = await CatalogSyncRunRepository.staged_rows(session, run_id)
            upserts = [s for s in staged_rows if s.op == STAGED_OP_UPSERT]
            if is_full:
                removals = live - {s.external_id for s in upserts}
            else:
                removals = live & {s.external_id for s in staged_rows if s.op == STAGED_OP_REMOVE}

            if not forced and guard_trips(
                live=len(live),
                removals=len(removals),
                max_removal_ratio=cfg.max_removal_ratio,
                min_removal_count=cfg.min_removal_count,
            ):
                logger.warning(
                    "catalog_source_suspect_empty",
                    source=slug,
                    live=len(live),
                    removals=len(removals),
                )
                await CatalogSyncRunRepository.delete_staged(session, run_id)
                await CatalogSyncRunRepository.finish(
                    session,
                    run_id,
                    status=RUN_STATUS_SUSPECT_EMPTY,
                    is_full=is_full,
                    staged=staged,
                    finished_at=utcnow(),
                )
                await CatalogSourceRepository.record_failure(
                    session, slug=slug, status=SOURCE_STATUS_SUSPECT_EMPTY
                )
                await CatalogSourceRepository.release_lease(session, slug=slug, owner=run_id)
                return SyncOutcome(
                    slug=slug, status=RUN_STATUS_SUSPECT_EMPTY, run_id=run_id, staged=staged
                )

            previous_listing = source.listing_digest
            resolver = await self._resolver(session)
            now = await CatalogSourceRepository.run_clock(session)
            for staged_row in upserts:
                _upsert_entry(
                    session,
                    by_external,
                    staged_row,
                    slug=slug,
                    resolver=resolver,
                    seen_at=started_at,
                )
            await CatalogEntryRepository.flush(session)
            if is_full:
                removed = await CatalogEntryRepository.tombstone_unseen(
                    session, slug=slug, seen_before=started_at, now=now
                )
            else:
                removed = 0
                for external_id in removals:
                    entry = by_external[external_id]
                    entry.removed_at = now
                    removed += 1
                await CatalogEntryRepository.flush(session)

            live_rows = await CatalogEntryRepository.rows_for_source(session, slug, live_only=True)
            await CatalogSourceRepository.record_applied(
                session,
                row=source,
                run_id=run_id,
                synced_at=now,
                watermark=watermark,
                content_digest=digests.content_digest(live_rows),
                listing_digest=digests.listing_digest(live_rows),
                live_entries=len(live_rows),
            )
            if is_full:
                source.resolver_inputs_digest = self.current_resolver_inputs_digest()
            await CatalogSyncRunRepository.finish(
                session,
                run_id,
                status=RUN_STATUS_APPLIED,
                is_full=is_full,
                staged=staged,
                upserted=len(upserts),
                removed=removed,
                finished_at=utcnow(),
            )
            await CatalogSyncRunRepository.delete_staged(session, run_id)
            await CatalogSourceRepository.release_lease(session, slug=slug, owner=run_id)
            if self._is_curated(cfg):
                if source.listing_digest != previous_listing:
                    await self._flag_conflicting(session)
            else:
                await self._record_ownership_conflicts(session, slug, resolver)
        metrics.record_entries(slug, len(live_rows))
        logger.info(
            "catalog_source_synced",
            source=slug,
            upserted=len(upserts),
            removed=removed,
            live=len(live_rows),
        )
        return SyncOutcome(
            slug=slug,
            status=RUN_STATUS_APPLIED,
            run_id=run_id,
            staged=staged,
            upserted=len(upserts),
            removed=removed,
        )

    # ── re-resolve ────────────────────────────────────────────────────────────

    @staticmethod
    def _is_curated(cfg: CatalogSourceConfig) -> bool:
        return cfg.enabled and cfg.trust == "curated"

    async def _flag_conflicting(self, session: Any) -> None:
        """After the curated id set changed, flag every non-curated source with live rows."""
        slugs = [
            row.slug
            for row in await CatalogSourceRepository.list_all(session)
            if row.slug in self._sources
            and not self._is_curated(self._sources[row.slug])
            and row.live_entries > 0
        ]
        await CatalogSourceRepository.mark_reresolve_wanted(session, slugs)

    @staticmethod
    async def _ownership_conflicts(
        session: Any, slug: str, resolver: CatalogApiIdResolver
    ) -> list[OwnershipConflict]:
        """Imported APIs from ``slug`` that hold a curated-owned id they are not mapped to."""
        prefix = f"{slug}/"
        conflicts: list[OwnershipConflict] = []
        for vendor, name, version, api_id, entry_id in await ApiRepository.imported_from_source(
            session, slug
        ):
            if not api_id or not resolver.is_curated_owned(api_id):
                continue
            mapped = resolver.mapped(slug, entry_id.removeprefix(prefix))
            if mapped is not None and mapped.lower() == api_id.lower():
                continue
            conflicts.append(
                OwnershipConflict(
                    entry_id=entry_id,
                    catalog_api_id=api_id,
                    vendor=vendor,
                    name=name,
                    version=version,
                )
            )
        return conflicts

    async def _record_ownership_conflicts(
        self, session: Any, slug: str, resolver: CatalogApiIdResolver
    ) -> int:
        """Count imported APIs from ``slug`` that hold a curated-owned id they are not mapped to.

        Such an API keeps its ``catalog_api_id`` (re-keying it would orphan its
        credentials), so each one is logged, and the source's conflict gauge is
        set to the count (zero included, so a cleared conflict clears the gauge).
        """
        conflicts = await self._ownership_conflicts(session, slug, resolver)
        for conflict in conflicts:
            logger.warning(
                "catalog_ownership_conflict",
                source=slug,
                entry_id=conflict.entry_id,
                catalog_api_id=conflict.catalog_api_id,
                api_vendor=conflict.vendor,
                api_name=conflict.name,
                api_version=conflict.version,
            )
        metrics.record_ownership_conflicts(slug, len(conflicts))
        return len(conflicts)

    async def source_warnings(self) -> dict[str, list[SourceWarning]]:
        """Each configured source's operator warnings, computed from the current rows.

        Mapping rows that match no curated id (ignored, so their entries stay
        namespaced) and, for non-curated sources, imported APIs whose
        ``catalog_api_id`` a curated source now owns. Names local APIs, so it is
        for operators only.
        """
        warnings: dict[str, list[SourceWarning]] = {slug: [] for slug in self._sources}
        async with self._ctx.registry_db.session() as session:
            resolver = await self._resolver(session)
            for row in resolver.unmatched_mappings:
                if row.source in warnings:
                    warnings[row.source].append(
                        SourceWarning(
                            code=WARNING_MAPPING_UNMATCHED,
                            external_id=row.external_id,
                            catalog_api_id=row.catalog_api_id,
                        )
                    )
            for cfg in self._sources.values():
                if self._is_curated(cfg):
                    continue
                for conflict in await self._ownership_conflicts(session, cfg.slug, resolver):
                    warnings[cfg.slug].append(
                        SourceWarning(
                            code=WARNING_OWNERSHIP_CONFLICT,
                            external_id=conflict.entry_id.removeprefix(f"{cfg.slug}/"),
                            catalog_api_id=conflict.catalog_api_id,
                            local_api=f"{conflict.vendor}/{conflict.name}/{conflict.version}",
                        )
                    )
        return warnings

    async def reresolve_source(self, slug: str) -> int:
        """Re-run the resolver over a source's stored rows; returns how many ids changed.

        Runs under the source's apply lock (not its sync lease), so it never
        interleaves with an apply. An already imported API keeps its own
        ``catalog_api_id``; one that now holds a curated-owned id it is not mapped
        to is counted as an ownership conflict. A curated source whose ids change
        flags every non-curated source in turn.
        """
        cfg = self._sources.get(slug)
        if cfg is None:
            return 0
        changed = 0
        async with self._source_transaction(slug) as session:
            source = await CatalogSourceRepository.get(session, slug)
            if source is None:
                return 0
            previous_listing = source.listing_digest
            resolver = await self._resolver(session)
            rows = await CatalogEntryRepository.rows_for_source(session, slug, live_only=True)
            for row in rows:
                resolved = resolver.resolve(
                    source_slug=slug, external_id=row.external_id, hint=row.catalog_api_id_hint
                )
                if resolved == row.catalog_api_id:
                    continue
                row.catalog_api_id = resolved
                changed += 1
            await CatalogEntryRepository.flush(session)
            source.content_digest = digests.content_digest(rows)
            source.listing_digest = digests.listing_digest(rows)
            source.reresolve_wanted_at = None
            source.resolver_inputs_digest = self.current_resolver_inputs_digest()
            await CatalogEntryRepository.flush(session)
            if self._is_curated(cfg):
                if source.listing_digest != previous_listing:
                    await self._flag_conflicting(session)
            else:
                await self._record_ownership_conflicts(session, slug, resolver)
        if changed:
            logger.info("catalog_source_reresolved", source=slug, changed=changed)
        return changed

    async def sweep_abandoned_staging(
        self,
        *,
        staging_age: timedelta = ABANDONED_STAGING_AGE,
        run_retention: timedelta | None = None,
    ) -> int:
        """Clean up after runs whose process died before finishing them.

        Every cutoff is measured from the database clock (``run_clock``), the
        clock lease expiries and run start times are written with. First clears
        leases that expired more than ``staging_age`` ago: a live run renews its
        lease every third of its length and stops when a renewal fails, so such a
        lease belongs to a dead run, and nothing else would clear it if its
        source is never synced again. Then deletes staged rows older than
        ``staging_age`` and marks ``running`` rows started before then
        ``failed`` (``abandoned``); a run still holding its source's lease is
        left alone. With ``run_retention`` it also prunes sync history finished
        longer ago than that. Returns the number of staged rows deleted.
        """
        async with self._ctx.registry_db.transaction() as session:
            now = await CatalogSourceRepository.run_clock(session)
            cutoff = now - staging_age
            leases = await CatalogSourceRepository.clear_expired_leases(
                session, expired_before=cutoff
            )
            swept = await CatalogSyncRunRepository.sweep_staged(session, older_than=cutoff)
            closed = await CatalogSyncRunRepository.close_abandoned(
                session, older_than=cutoff, now=now
            )
            pruned = 0
            if run_retention is not None:
                pruned = await CatalogSyncRunRepository.prune_finished(
                    session, finished_before=now - run_retention
                )
        if leases or swept or closed or pruned:
            logger.info(
                "catalog_source_runs_swept",
                leases_cleared=leases,
                staged_deleted=swept,
                runs_abandoned=closed,
                runs_pruned=pruned,
            )
        return swept


def is_due(cfg: CatalogSourceConfig, row: CatalogSourceRow, *, now: datetime) -> bool:
    """Whether an auto-syncing source's last sync is older than its max age.

    After a failed run the next attempt backs off exponentially (one minute,
    doubling, capped at the max age), counted from the row's last update.
    """
    if not cfg.enabled or cfg.max_age_seconds <= 0:
        return False
    if row.consecutive_failures > 0 and row.updated_at is not None:
        backoff = min(cfg.max_age_seconds, 60 * 2 ** min(row.consecutive_failures - 1, 10))
        if (now - row.updated_at).total_seconds() < backoff:
            return False
    if row.last_sync_at is None:
        return True
    return (now - row.last_sync_at).total_seconds() > cfg.max_age_seconds


_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def _raw(value: str | None) -> str | None:
    """Connector text as staged: verbatim except what no database text column stores.

    NUL is dropped and a lone surrogate (which cannot be encoded as UTF-8)
    becomes U+FFFD.
    """
    if value is None:
        return None
    return _SURROGATE_RE.sub("\ufffd", value.replace("\x00", ""))


def _raw_json(value: Any) -> Any:
    """``_raw`` applied to every string (keys included) of a JSON value."""
    if isinstance(value, str):
        return _raw(value)
    if isinstance(value, Mapping):
        return {_raw(str(k)): _raw_json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_raw_json(v) for v in value]
    return value


def _staged_row(run_id: str, change: Upserted | Removed) -> CatalogStagedEntry:
    if isinstance(change, Removed):
        return CatalogStagedEntry(
            sync_run_id=run_id, op=STAGED_OP_REMOVE, external_id=change.external_id, tags=[]
        )
    entry = change.entry
    return CatalogStagedEntry(
        sync_run_id=run_id,
        op=STAGED_OP_UPSERT,
        external_id=entry.external_id,
        catalog_api_id_hint=_raw(entry.catalog_api_id_hint),
        title=_raw(entry.title),
        version=_raw(entry.version),
        spec_ref=_raw_json(entry.spec_ref) if entry.spec_ref is not None else None,
        human_url=_raw(entry.human_url),
        tags=[_raw(t) for t in entry.tags if isinstance(t, str)],
        content_hint=_raw(entry.content_hint),
    )


def _upsert_entry(
    session: Any,
    by_external: dict[str, CatalogEntryRow],
    staged: CatalogStagedEntry,
    *,
    slug: str,
    resolver: CatalogApiIdResolver,
    seen_at: datetime,
) -> None:
    """Fold one staged upsert into ``catalog_entries`` (sanitised and resolved)."""
    hint = sanitize.clean_hint(staged.catalog_api_id_hint)
    values: dict[str, Any] = {
        "catalog_api_id_hint": hint,
        "catalog_api_id": resolver.resolve(
            source_slug=slug, external_id=staged.external_id, hint=staged.catalog_api_id_hint
        ),
        "title": sanitize.clean_text(staged.title, max_length=sanitize.TITLE_MAX_LENGTH),
        "version": sanitize.clean_text(staged.version, max_length=sanitize.VERSION_MAX_LENGTH),
        "spec_ref": staged.spec_ref,
        "human_url": sanitize.clean_human_url(staged.human_url),
        "tags": sanitize.clean_tags(staged.tags or []),
        "content_hint": sanitize.clean_token(
            staged.content_hint, max_length=sanitize.HINT_MAX_LENGTH
        ),
        "seen_at": seen_at,
        "removed_at": None,
    }
    row = by_external.get(staged.external_id)
    if row is None:
        row = CatalogEntryRow(
            entry_id=make_entry_id(slug, staged.external_id),
            source_slug=slug,
            external_id=staged.external_id,
            **values,
        )
        CatalogEntryRepository.add(session, row)
        by_external[staged.external_id] = row
        return
    for key, value in values.items():
        setattr(row, key, value)


def summarise(outcomes: Sequence[SyncOutcome]) -> dict[str, str]:
    """``{slug: status}`` for logs."""
    return {o.slug: o.status for o in outcomes}
