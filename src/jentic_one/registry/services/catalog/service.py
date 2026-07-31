"""CatalogService — orchestrates the public API catalog (browse / preview / import).

Layering: this service is the only thing in the catalog slice that touches I/O —
the upstream GitHub manifest + spec fetches (``fetch.py``), the catalog cache DB
(``CatalogRepository``), and the import job queue (``enqueue_job``). All parsing /
projection is delegated to the pure ``manifest_builder`` lib so this file stays
about coordination.

Boundaries kept from D-005a:
- imports always resolve to a plain url ``IngestSource`` — no ``cat_…`` opaque IDs
  cross into the importer, no ``ApiSourceCatalog`` discriminator, no ``force_api_id``.
- identity is the spec triple (vendor/name/version) resolved by the importer, so a
  re-import maps onto the same local API.
- APIs only — workflows are out of scope (D-001).
- imports are async (job-poll), never a synchronous swap.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

from jentic_one.registry.repos.catalog_repo import CatalogRepository
from jentic_one.registry.repos.catalog_update_check_repo import CatalogUpdateCheckRepository
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository, RegisteredSpec
from jentic_one.registry.services.catalog import manifest_builder as mb
from jentic_one.registry.services.catalog.fetch import (
    CatalogFetchError,
    fetch_bytes_conditional,
    fetch_json,
)
from jentic_one.registry.services.errors import (
    CatalogEntryNotFoundError,
    CatalogUnavailableError,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.utils import utcnow
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.jobs.enqueue import enqueue_job
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.jobs import JobKind
from jentic_one.shared.models.registry import ORIGIN_CATALOG, ORIGIN_OVERLAY
from jentic_one.shared.pagination import decode_catalog_cursor, encode_catalog_cursor

logger = structlog.get_logger(__name__)

#: Strong references to in-flight fire-and-forget sweep tasks. ``CatalogService``
#: is constructed per-request and GC'd when the request returns, so a task held
#: only on ``self`` could be collected mid-flight (asyncio keeps only weak refs to
#: tasks). Parking them here until they finish keeps them alive; the done-callback
#: discards on completion. Bounded in practice by the max-age + advisory-lock gates
#: on the refresh that spawns them.
_SWEEP_TASKS: set[asyncio.Task[None]] = set()


def _is_upstream_tracked(spec: RegisteredSpec, manifest_urls: set[str]) -> bool:
    """Whether the update-notify sweep should probe this candidate (OQ-3 scope).

    - ``catalog``-origin revisions are always tracked (imported straight from the manifest).
    - ``overlay``-origin revisions are tracked when their ``source_url`` still points at a
      catalog spec — overlay materialization propagates the base revision's ``source_url``
      (#904), so a confirmed overlay over a catalog API stays upstream-tracked.
    - manual imports (``origin is None``) are tracked only if their ``source_url`` now
      matches a catalog manifest entry (a "you could switch to the catalog source" nudge).
    - any other manual import is skipped: we have no upstream-of-record to compare against.
    """
    if spec.origin == ORIGIN_CATALOG:
        return True
    if spec.origin in (ORIGIN_OVERLAY, None):
        return spec.source_url in manifest_urls
    return False


@dataclass(frozen=True)
class CatalogEntryView:
    """A browsable catalog entry with derived `registered` status."""

    api_id: str
    vendor: str | None
    path: str | None
    spec_url: str | None
    github_url: str | None
    registered: bool
    #: True when this entry is registered locally AND its upstream spec has a notified
    #: update the local revision hasn't adopted yet (Flow-3). Always False for
    #: unregistered entries (nothing to update).
    update_available: bool = False


@dataclass(frozen=True)
class CatalogListView:
    """A page of catalog entries plus the status fields the UI status row needs."""

    data: list[CatalogEntryView]
    catalog_total: int
    registered_count: int
    manifest_age_seconds: int | None
    has_more: bool
    next_cursor: str | None
    #: Count of registered entries with an upstream update available (full manifest,
    #: pre-filter/pre-page) so the UI status row is stable across pages.
    outdated_count: int = 0


@dataclass(frozen=True)
class CatalogPreviewView:
    """A capped, offset-paginated preview of an entry's operations."""

    operations: list[mb.PreviewOperation]
    total: int
    offset: int
    truncated: bool
    info: mb.PreviewInfo
    security_schemes: dict[str, dict[str, object]]


@dataclass(frozen=True)
class CatalogRefreshResult:
    """Outcome of a manifest refresh."""

    count: int


class CatalogService:
    """Read + import operations for the public API catalog."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._cfg = ctx.config.catalog
        self._ingest_cfg = ctx.config.ingest

    # ── refresh / staleness ──────────────────────────────────────────────────

    async def refresh(self) -> CatalogRefreshResult:
        """Fetch the upstream manifest and atomically replace the catalog snapshot."""
        try:
            doc = await fetch_json(self._cfg.manifest_url, config=self._ingest_cfg)
        except CatalogFetchError as exc:
            raise CatalogUnavailableError(f"catalog manifest unavailable: {exc}") from exc

        entries = [e.to_dict() for e in mb.parse_apis_json(doc)]
        async with self._ctx.registry_db.transaction() as session:
            count = await CatalogRepository.replace(
                session, source_url=self._cfg.manifest_url, entries=entries
            )
        return CatalogRefreshResult(count=count)

    def _is_stale(self, fetched_at: datetime | None) -> bool:
        """Whether the cache should be lazily refreshed (D-005a option (a)).

        Stale when empty or older than ``manifest_max_age_seconds``. A max-age of
        0 disables auto-refresh (manual ``:refresh`` only).
        """
        if self._cfg.manifest_max_age_seconds <= 0:
            return False
        if fetched_at is None:
            return True
        return (utcnow() - fetched_at).total_seconds() > self._cfg.manifest_max_age_seconds

    async def _safe_refresh(self) -> None:
        """Refresh with advisory-lock single-flight, swallowing upstream failures.

        Only the first concurrent caller acquires the lock and performs the
        upstream fetch; others return immediately and serve the current snapshot.
        """
        async with self._ctx.registry_db.transaction() as session:
            acquired = await CatalogRepository.try_acquire_refresh_lock(session)
            if not acquired:
                return
            fetched_at = await CatalogRepository.fetched_at(session)
            if not self._is_stale(fetched_at):
                return
        try:
            await self.refresh()
        except CatalogUnavailableError:
            return
        self.trigger_update_notify_sweep()

    def trigger_update_notify_sweep(self) -> None:
        """Fire-and-forget the update-notify sweep off the triggering read path.

        Public entry point for both the lazy refresh-on-read (``_safe_refresh``) and
        the explicit operator ``POST /catalog:refresh`` — an intentional "check
        upstream now" should sweep too.

        The sweep issues up to N conditional GETs, so awaiting it inline would make
        an unlucky user's (max-age-gated) catalog read block on the whole batch. We
        detach it into a background task instead; failures are logged, never raised
        back to the reader. The task is parked in ``_SWEEP_TASKS`` so it isn't GC'd
        before it finishes (this service instance is per-request and short-lived).

        No-ops when the sweep is disabled (kill switch) so a disabled install never
        spawns a throwaway task per refresh.
        """
        if self._cfg.update_check_interval_seconds <= 0:
            return
        try:
            task = asyncio.create_task(self._run_update_notify_sweep())
        except RuntimeError:
            # No running event loop (only reachable from a sync caller, which the
            # async refresh path never is). Skip rather than block; there is no
            # safe inline fallback without a loop.
            logger.debug("catalog_update_sweep_skipped_no_running_loop")
            return
        _SWEEP_TASKS.add(task)

        def _done(t: asyncio.Task[None]) -> None:
            _SWEEP_TASKS.discard(t)
            if not t.cancelled() and (exc := t.exception()) is not None:
                logger.warning("catalog_update_sweep_task_failed", exc_info=exc)

        task.add_done_callback(_done)

    async def run_update_sweep(self) -> None:
        """Run one update-notify sweep to completion (awaitable).

        Public entry point for the standalone ``CatalogUpdateScanner``, which owns the
        periodic cadence and awaits each sweep — unlike ``trigger_update_notify_sweep``,
        the fire-and-forget read-path piggyback. Honors the same kill switch.
        """
        await self._run_update_notify_sweep()

    async def _run_update_notify_sweep(self) -> None:
        """Probe registered specs for upstream changes; emit an event on change.

        Piggybacks on the (rare, max-age-gated) manifest refresh. For each
        registered API with a spec URL, sends a conditional ``GET``
        (``If-None-Match`` with the last-seen ETag) at most once per
        ``update_check_interval_seconds``; a ``304`` or an unchanged digest is a
        no-op, a changed digest emits ``catalog.update_available`` once (deduped on
        ``last_notified_digest``). Upstream/DB failures are swallowed per API so a
        flaky host never breaks the refresh or the rest of the batch. A ``0``
        interval disables the sweep entirely (air-gapped kill switch).

        Note the refresh's advisory lock is transaction-scoped (it guards only the
        staleness check). Sweeps are serialized **within a process** by
        ``Context.update_sweep_lock`` (scanner + read-path trigger can't overlap), but this
        is **not** globally single-flight across replicas: a rare concurrent double-refresh on
        two replicas could still emit a duplicate event for the same change. That is
        deliberately tolerated — the event is idempotent for operators and the next sweep on
        either replica dedupes on the persisted digest. Probes fan out with bounded
        concurrency (``update_sweep_max_concurrency``, kept below the DB pool) under
        a wall-clock budget (``update_sweep_deadline_seconds``) so a large or hostile
        candidate set can't turn one refresh into an unbounded stall.
        """
        interval = self._cfg.update_check_interval_seconds
        if interval <= 0:
            return

        # Serialize sweeps in-process: the scanner and the read-path trigger can both fire.
        # The lock.locked() pre-check is a best-effort fast-skip, not a guarantee — it is
        # TOCTOU, so two triggers racing past it both reach ``async with lock`` and the
        # second *queues* (waits) rather than skipping. That is harmless: by the time the
        # queued sweep runs, the first has bumped each candidate's ``last_checked_at``, so
        # the queued pass no-ops on the per-API interval gate in ``_probe_one``. The
        # pre-check just avoids the common non-racing back-to-back trigger paying for a
        # full re-probe (see Context.update_sweep_lock).
        lock = self._ctx.update_sweep_lock
        if lock.locked():
            logger.debug("catalog_update_sweep_skipped_in_flight")
            return
        async with lock:
            await self._do_update_notify_sweep(interval)

    async def _do_update_notify_sweep(self, interval: int) -> None:
        """Sweep body — always called under ``Context.update_sweep_lock``."""
        async with self._ctx.registry_db.session() as session:
            candidates = await ApiRevisionRepository.registered_specs_for_notify(session)
            # The manifest coverage set is only needed to admit overlay/manual specs
            # whose source_url still points at a catalog spec; skip the query when the
            # candidate set has no such rows (pure-catalog installs, the common case).
            needs_manifest = any(spec.origin != ORIGIN_CATALOG for spec in candidates)
            manifest_urls = (
                await CatalogRepository.manifest_spec_urls(session) if needs_manifest else set()
            )
        candidates = [spec for spec in candidates if _is_upstream_tracked(spec, manifest_urls)]
        if not candidates:
            return

        now = utcnow()
        sem = asyncio.Semaphore(max(1, self._cfg.update_sweep_max_concurrency))
        stats = {"probed": 0, "failed": 0}

        async def _guarded(spec: RegisteredSpec) -> None:
            async with sem:
                try:
                    await self._probe_one(spec, now=now, interval=interval)
                    stats["probed"] += 1
                except Exception:  # best-effort per API, never break the sweep
                    stats["failed"] += 1
                    logger.warning(
                        "catalog_update_probe_failed",
                        api_id=str(spec.api_id),
                        spec_url=spec.source_url,
                        exc_info=True,
                    )

        started = asyncio.get_running_loop().time()
        try:
            await asyncio.wait_for(
                asyncio.gather(*(_guarded(spec) for spec in candidates)),
                timeout=self._cfg.update_sweep_deadline_seconds,
            )
        except TimeoutError:
            logger.warning(
                "catalog_update_sweep_deadline_hit",
                candidates=len(candidates),
                probed=stats["probed"],
            )
        logger.info(
            "catalog_update_sweep_complete",
            candidates=len(candidates),
            probed=stats["probed"],
            failed=stats["failed"],
            duration_ms=int((asyncio.get_running_loop().time() - started) * 1000),
        )

    async def _probe_one(self, spec: RegisteredSpec, *, now: datetime, interval: int) -> None:
        """Conditionally fetch one registered spec and emit on a fresh change."""
        async with self._ctx.registry_db.session() as session:
            check = await CatalogUpdateCheckRepository.get(session, spec.api_id)

        if check is not None and check.last_checked_at is not None:
            age = (now - check.last_checked_at).total_seconds()
            if age < interval:
                return

        prior_etag = check.last_seen_etag if check is not None else None
        result = await fetch_bytes_conditional(
            spec.source_url, config=self._ingest_cfg, etag=prior_etag
        )

        if result.not_modified or result.digest is None:
            async with self._ctx.registry_db.transaction() as session:
                await CatalogUpdateCheckRepository.upsert(
                    session,
                    local_api_id=spec.api_id,
                    spec_url=spec.source_url,
                    etag=result.etag,
                    digest=None,
                    checked_at=now,
                )
            return

        upstream_digest = result.digest
        last_notified = check.last_notified_digest if check is not None else None
        # A change worth notifying: the upstream bytes differ from what backs the
        # registered revision, and we have not already notified for this exact
        # upstream digest. Comparing against spec_digest (not just last_seen)
        # means a spec that reverts to the registered content stops notifying.
        in_sync = upstream_digest == spec.spec_digest
        changed = not in_sync and upstream_digest != last_notified

        notified_digest = upstream_digest if changed else None
        async with self._ctx.registry_db.transaction() as session:
            await CatalogUpdateCheckRepository.upsert(
                session,
                local_api_id=spec.api_id,
                spec_url=spec.source_url,
                etag=result.etag,
                digest=upstream_digest,
                checked_at=now,
                notified_digest=notified_digest,
                # Upstream matches the served revision again (e.g. a bad publish was
                # reverted): pin last_notified_digest to it so the outdated read surface
                # clears. Otherwise a revert leaves the badge stuck lit with no operator
                # action able to resolve it. Dedupe is preserved — a later genuinely
                # different upstream digest still re-fires exactly once.
                sync_notified=in_sync,
            )

        if not changed:
            return

        async with self._ctx.admin_db.transaction() as session:
            await emit_event_best_effort(
                session,
                type=EventType.CATALOG_UPDATE_AVAILABLE,
                severity=EventSeverity.INFO,
                summary=(f"Upstream spec updated for {spec.vendor}/{spec.name} ({spec.version})"),
                # Actionable: an operator can resolve it by re-importing the upstream spec
                # (one-click in the UI / `jentic catalog outdated` + import in the CLI),
                # which the ImportHandler settles via ``settle_actionable_events`` keyed on
                # this ``api_id``. A catalog re-import that adopts the upstream also drops the
                # API out of the outdated set (the served revision's digest now equals the
                # notified one), so the badge/count clear even if the settle is missed. Caveat:
                # when the served revision is a *manually PUBLISHED* one (not a catalog import),
                # a catalog re-import is blocked by ``ix_api_revisions_one_active`` — the
                # operator must archive/replace the published revision to resolve; until then
                # the outdated flag correctly stays lit and the settle only clears the inbox.
                requires_action=True,
                created_by=None,
                data={
                    "api_id": str(spec.api_id),
                    "vendor": spec.vendor,
                    "name": spec.name,
                    "version": spec.version,
                    "current_digest": spec.spec_digest,
                    "upstream_digest": upstream_digest,
                    "spec_url": spec.source_url,
                },
            )

    async def _refresh_if_stale(self) -> None:
        """Lazy refresh-on-read seam for the single-entry reads (``get``)."""
        async with self._ctx.registry_db.session() as session:
            fetched_at = await CatalogRepository.fetched_at(session)
        if self._is_stale(fetched_at):
            await self._safe_refresh()

    async def _load_snapshot(
        self,
    ) -> tuple[list[dict[str, Any]], set[str], set[str], datetime | None]:
        """Read entries, coverage URLs, outdated URLs, and freshness in one session."""
        async with self._ctx.registry_db.session() as session:
            raw = await CatalogRepository.entries(session)
            registered_urls = await CatalogRepository.registered_spec_urls(session)
            outdated_urls = await CatalogUpdateCheckRepository.outdated_spec_urls(session)
            fetched_at = await CatalogRepository.fetched_at(session)
        return raw, registered_urls, outdated_urls, fetched_at

    # ── browse / get ─────────────────────────────────────────────────────────

    async def list_all(
        self,
        *,
        q: str | None = None,
        registered_only: bool = False,
        unregistered_only: bool = False,
        outdated_only: bool = False,
        cursor: str | None = None,
        limit: int = 50,
    ) -> CatalogListView:
        """List a keyset page of catalog entries (optionally filtered/ranked).

        Paging is an in-memory keyset over the cached snapshot blob: entries are
        ordered (``api_id`` for browse, ``(-score, api_id)`` for search), the
        registration filter is applied, then a ``limit``-sized window after the
        ``cursor`` position is returned. ``catalog_total``/``registered_count``
        always reflect the full manifest (pre-filter, pre-page) so the UI status
        row is stable across pages. Raises ``InvalidCursorError`` on a bad cursor.

        A cursor is only meaningful for the **same** ``(q, registered_only,
        unregistered_only)`` it was issued under — it encodes a position in that
        specific ordering, not the query — so callers must hold those constant
        while paging (changing them mid-scroll yields a valid but meaningless
        slice, never an error). Cursors are also relative to the snapshot at read
        time: a refresh between pages may skip/repeat entries near the cursor
        (the standard keyset-vs-mutating-snapshot trade-off), but never crashes
        or loops. Refresh is rare (lazy, max-age gated), so this is acceptable.
        """
        raw, registered_urls, outdated_urls, fetched_at = await self._load_snapshot()
        if self._is_stale(fetched_at):
            await self._safe_refresh()
            raw, registered_urls, outdated_urls, fetched_at = await self._load_snapshot()

        all_entries = [mb.ManifestEntry.from_dict(d) for d in raw]
        catalog_total = len(all_entries)
        registered_count = sum(1 for e in all_entries if mb.is_registered(e, registered_urls))
        # Outdated = registered AND its spec_url is in the outdated set. Count over the
        # whole manifest (pre-filter/pre-page) so the status row is page-stable.
        outdated_count = sum(
            1
            for e in all_entries
            if mb.is_registered(e, registered_urls) and e.spec_url in outdated_urls
        )

        scored = mb.score_entries(all_entries, q)
        if registered_only:
            scored = [(e, s) for e, s in scored if mb.is_registered(e, registered_urls)]
        elif unregistered_only:
            scored = [(e, s) for e, s in scored if not mb.is_registered(e, registered_urls)]
        if outdated_only:
            scored = [(e, s) for e, s in scored if e.spec_url in outdated_urls]

        after_api_id, after_score = decode_catalog_cursor(cursor) if cursor else (None, None)
        page = mb.paginate_entries(
            scored, after_api_id=after_api_id, after_score=after_score, limit=limit
        )
        views = [self._to_view(e, registered_urls, outdated_urls) for e in page.items]
        next_cursor = (
            encode_catalog_cursor(page.next_api_id, page.next_score)
            if page.has_more and page.next_api_id is not None
            else None
        )

        age = None if fetched_at is None else int((utcnow() - fetched_at).total_seconds())
        return CatalogListView(
            data=views,
            catalog_total=catalog_total,
            registered_count=registered_count,
            manifest_age_seconds=age,
            has_more=page.has_more,
            next_cursor=next_cursor,
            outdated_count=outdated_count,
        )

    async def get(self, api_id: str) -> CatalogEntryView:
        """Resolve a single catalog entry by api_id."""
        await self._refresh_if_stale()
        async with self._ctx.registry_db.session() as session:
            raw = await CatalogRepository.entries(session)
            registered_urls = await CatalogRepository.registered_spec_urls(session)
            outdated_urls = await CatalogUpdateCheckRepository.outdated_spec_urls(session)
        match = next((d for d in raw if d.get("api_id") == api_id), None)
        if match is None:
            raise CatalogEntryNotFoundError(api_id)
        return self._to_view(mb.ManifestEntry.from_dict(match), registered_urls, outdated_urls)

    @staticmethod
    def _to_view(
        entry: mb.ManifestEntry,
        registered_spec_urls: set[str],
        outdated_spec_urls: set[str] | None = None,
    ) -> CatalogEntryView:
        registered = mb.is_registered(entry, registered_spec_urls)
        return CatalogEntryView(
            api_id=entry.api_id,
            vendor=entry.vendor,
            path=entry.path or None,
            spec_url=entry.spec_url,
            github_url=entry.github_url or None,
            registered=registered,
            update_available=(
                registered
                and outdated_spec_urls is not None
                and entry.spec_url in outdated_spec_urls
            ),
        )

    # ── preview ──────────────────────────────────────────────────────────────

    async def preview(
        self,
        api_id: str,
        *,
        offset: int = 0,
        limit: int = mb.PREVIEW_MAX_OPERATIONS,
        tag: str | None = None,
        q: str | None = None,
    ) -> CatalogPreviewView:
        """Fetch the entry's spec and project a capped, paginated operation list.

        ``tag`` and ``q`` filter the full operation set server-side *before*
        windowing, so the search box / tag chips in the UI cover every operation
        in the spec — not just the loaded page — and ``total`` reflects the
        filtered count the "Load more" affordance pages through.
        """
        entry = await self.get(api_id)
        if not entry.spec_url:
            raise CatalogUnavailableError(f"catalog entry '{api_id}' has no spec url")
        try:
            doc = await fetch_json(entry.spec_url, config=self._ingest_cfg)
        except CatalogFetchError as exc:
            raise CatalogUnavailableError(f"catalog spec unavailable: {exc}") from exc

        projection = mb.project_preview(doc, tag=tag, q=q)
        total = len(projection.operations)
        capped_limit = max(0, min(limit, mb.PREVIEW_MAX_OPERATIONS))
        start = max(0, offset)
        window = projection.operations[start : start + capped_limit]
        return CatalogPreviewView(
            operations=window,
            total=total,
            offset=start,
            truncated=start + len(window) < total,
            info=projection.info,
            security_schemes=projection.security_schemes,
        )

    # ── import ───────────────────────────────────────────────────────────────

    def _to_import_source(self, entry: CatalogEntryView) -> dict[str, str]:
        """Build a plain url IngestSource payload — never a catalog-shaped one.

        The catalog already knows the vendor and api_name from the manifest folder
        structure (``apis/openapi/{domain}/{sub}/…`` → ``extract_vendor(api_id)``),
        so we pass them through as overrides. Many catalog specs (e.g. coincap)
        omit ``x-vendor``/``contact.name`` in their ``info`` block, which would
        otherwise fail api_identifier resolution with "missing vendor" or "missing
        name". Threading the catalog vendor and api_name makes identity (and
        re-import dedup) deterministic from the catalog id rather than dependent on
        the upstream spec's info.
        """
        if not entry.spec_url:
            raise CatalogUnavailableError(f"catalog entry '{entry.api_id}' has no spec url")
        source: dict[str, str] = {
            "type": "url",
            "url": entry.spec_url,
            "origin": ORIGIN_CATALOG,
        }
        if entry.vendor:
            source["vendor"] = entry.vendor
        if entry.api_id:
            source["api_name"] = entry.api_id
            # Also carried verbatim: `api_name` above only seeds the slugified
            # vendor/name identity (the separable `domain/sub` structure is
            # destroyed by slugification), while this copy is persisted as-is
            # on the Api row for friendly-title derivation.
            source["catalog_api_id"] = entry.api_id
        return source

    async def import_entry(self, api_id: str, identity: Identity) -> str:
        """Enqueue an async url-import for a catalog entry; return the job id."""
        entry = await self.get(api_id)
        source = self._to_import_source(entry)
        async with self._ctx.admin_db.transaction() as session:
            return await enqueue_job(
                session,
                JobKind.IMPORT,
                created_by=identity.sub,
                actor_type=identity.actor_type,
                payload={"sources": [source]},
            )

    async def ensure_imported(self, api_id: str, identity: Identity) -> str | None:
        """Hand-off seam for the Credentials PR (B6, deferred).

        Idempotently enqueue an import for ``api_id`` unless it is already
        registered locally (its ``spec_url`` already backs a non-archived
        revision — the same coverage key ``GET /catalog`` uses, not a vendor
        guess). Returns the job id when enqueued, ``None`` when the entry is
        already registered (no-op). APIs only — no workflow side effects.
        """
        entry = await self.get(api_id)
        if entry.registered:
            return None
        source = self._to_import_source(entry)
        async with self._ctx.admin_db.transaction() as session:
            return await enqueue_job(
                session,
                JobKind.IMPORT,
                created_by=identity.sub,
                actor_type=identity.actor_type,
                payload={"sources": [source]},
            )
