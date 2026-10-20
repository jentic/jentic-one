"""CatalogService — browse, preview and import over the configured catalog sources.

Layering: this service coordinates I/O — the process-local snapshot of
``catalog_entries`` (``snapshot.py``), the spec fetches through each entry's
source (``spec_fetch.py``), the source syncs (``sources/sync.py``) and the import
job queue (``enqueue_job``).
Parsing and projection stay in the pure ``manifest_builder`` lib.

Reads key on ``entry_id`` (``{source_slug}/{external_id}``). Every read first
narrows the snapshot to the sources the caller may see (``allowed_sources``)
and collapses entries sharing a ``catalog_api_id`` to one winner (registered
first, then source precedence, then ``entry_id``). An entry the caller cannot
see is answered exactly like one that does not exist.

Imports:
- enqueue a ``CatalogRefSource`` naming only the entry; the job fetches the
  spec through the entry's source, so no source credential enters a payload;
- land on the API already imported from the same entry, else on a fresh
  identity; an entry whose ``catalog_api_id`` another entry's API already holds
  takes a distinct name unless the caller asks to switch that API's source;
- are async (job-poll), never a synchronous swap.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

from jentic_one.registry.core.schema.catalog_entries import CatalogEntryRow
from jentic_one.registry.core.schema.catalog_sources import (
    SOURCE_STATUS_NEVER_SYNCED,
    CatalogSourceRow,
)
from jentic_one.registry.core.schema.catalog_sync_runs import (
    RUN_STATUS_APPLIED,
    RUN_STATUS_SUPERSEDED,
    RUN_STATUS_SUSPECT_EMPTY,
)
from jentic_one.registry.ingest.exc import IngestStageError
from jentic_one.registry.ingest.fetch import CatalogRefSource, parse_spec_content
from jentic_one.registry.ingest.host_change_guard import may_approve_host_change
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.catalog_entry_repo import CatalogEntryRepository
from jentic_one.registry.repos.catalog_source_repo import CatalogSourceRepository
from jentic_one.registry.repos.catalog_update_check_repo import CatalogUpdateCheckRepository
from jentic_one.registry.repos.overlay_repo import OverlayRepository
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository, RegisteredSpec
from jentic_one.registry.services.catalog import manifest_builder as mb
from jentic_one.registry.services.catalog.entries import (
    CatalogApiIdMapping,
    CatalogEntry,
    resolver_inputs_digest,
)
from jentic_one.registry.services.catalog.flow3_metrics import (
    record_reimport_from_catalog,
    record_update_notified,
    record_update_snoozed,
)
from jentic_one.registry.services.catalog.snapshot import (
    CatalogSnapshot,
    CollapsedView,
    allowed_sources,
    build_snapshot,
    cached_snapshot,
    store_snapshot,
)
from jentic_one.registry.services.catalog.sources import digests
from jentic_one.registry.services.catalog.sources.deps import spec_fetch_lane
from jentic_one.registry.services.catalog.sources.protocol import NotModified
from jentic_one.registry.services.catalog.sources.sync import (
    OUTCOME_BUSY,
    OUTCOME_DISABLED,
    OUTCOME_SKIPPED,
    CatalogSourceSync,
    SyncOutcome,
    is_due,
)
from jentic_one.registry.services.catalog.spec_fetch import CatalogSpecFetcher
from jentic_one.registry.services.errors import (
    CatalogEntryHasNoSpecError,
    CatalogEntryNotFoundError,
    CatalogUnavailableError,
    NothingToSnoozeError,
    OverlaySupersedeForbiddenError,
    SnoozeForbiddenError,
    SourceSwitchForbiddenError,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import CREDENTIALS_WRITE, compute_effective
from jentic_one.shared.auth.permissions import has_effective_permission
from jentic_one.shared.catalog.api_id import NAMESPACE_SEPARATOR
from jentic_one.shared.config import CatalogConfig, CatalogSourceConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.locks import advisory_lock_key
from jentic_one.shared.db.utils import utcnow
from jentic_one.shared.events import emit_event, emit_event_best_effort
from jentic_one.shared.jobs.enqueue import enqueue_job
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.jobs import JobKind
from jentic_one.shared.models.registry import ORIGIN_CATALOG, ORIGIN_OVERLAY
from jentic_one.shared.pagination import decode_catalog_cursor, encode_catalog_cursor

logger = structlog.get_logger(__name__)

#: Strong references to in-flight fire-and-forget sweep tasks. ``CatalogService``
#: is constructed per-request and GC'd when the request returns, so a task held
#: only on ``self`` could be collected mid-flight (asyncio keeps only weak refs to
#: tasks). Parking them here until they finish keeps them alive; the done-callback
#: discards on completion.
_SWEEP_TASKS: set[asyncio.Task[None]] = set()

#: Source config digests per loaded ``CatalogConfig`` (keyed by identity; the
#: entry holds the config, so its id can't be reused while cached). A config is
#: immutable once loaded, and hashing a large one on every read is the costliest
#: step of the snapshot key.
_CONFIG_DIGESTS: dict[int, tuple[CatalogConfig, dict[str, str]]] = {}
_CONFIG_DIGESTS_MAX = 64


def _config_digests(cfg: CatalogConfig) -> dict[str, str]:
    hit = _CONFIG_DIGESTS.get(id(cfg))
    if hit is not None and hit[0] is cfg:
        return hit[1]
    computed = {s.slug: digests.config_digest(s) for s in cfg.sources}
    if len(_CONFIG_DIGESTS) >= _CONFIG_DIGESTS_MAX:
        _CONFIG_DIGESTS.pop(next(iter(_CONFIG_DIGESTS)))
    _CONFIG_DIGESTS[id(cfg)] = (cfg, computed)
    return computed


#: Registry-DB advisory-lock key that single-flights the update-notify sweep across
#: processes (scanner and request-triggered sweeps on every replica/worker).
UPDATE_SWEEP_LOCK_KEY = advisory_lock_key("jentic_one.registry.catalog_update_sweep")


@dataclass(frozen=True)
class CatalogEntryView:
    """A browsable catalog entry with its derived ``registered`` status."""

    entry_id: str
    source_slug: str
    catalog_api_id: str
    #: Identity vendor (credentials match its slug).
    vendor: str | None
    #: The hint's vendor, for grouping in a UI only.
    display_vendor: str | None
    title: str | None
    version: str | None
    tags: tuple[str, ...]
    human_url: str | None
    has_spec: bool
    registered: bool
    #: True when this entry is registered locally AND its upstream spec has a notified
    #: update the local revision hasn't adopted yet. Always False for unregistered entries.
    update_available: bool = False
    #: ``entry_id``s of the visible entries collapsed into this one.
    alternate_ids: tuple[str, ...] = ()

    @property
    def alternates(self) -> int:
        """How many visible entries are collapsed into this one."""
        return len(self.alternate_ids)


@dataclass(frozen=True)
class CatalogListView:
    """A page of catalog entries plus the status fields the UI status row needs."""

    data: list[CatalogEntryView]
    catalog_total: int
    registered_count: int
    #: Age of the least recently synced visible source (None when none has synced).
    oldest_source_sync_age_seconds: int | None
    has_more: bool
    next_cursor: str | None
    #: Registered entries with an upstream update available (pre-filter, pre-page).
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
    server_variables: dict[str, dict[str, object]]
    required_schemes: list[str]


@dataclass(frozen=True)
class CatalogRefreshResult:
    """Outcome of a refresh, over the sources the caller may read.

    ``count`` is their live entries after it; ``partial`` is set when any of
    them failed, was held by another run (busy) or was skipped; ``sources`` is
    each one's run outcome.
    """

    count: int
    partial: bool = False
    sources: tuple[SyncOutcome, ...] = ()


@dataclass(frozen=True)
class _ReadState:
    """One request's view of the catalog: the snapshot plus the per-request sets."""

    snapshot: CatalogSnapshot
    rows: Mapping[str, CatalogSourceRow]
    allowed: frozenset[str]
    registered: frozenset[str]
    outdated_ids: set[str]
    view: CollapsedView


def _entry_key(entry: CatalogEntry) -> str:
    return entry.entry_id


def _outcome_codes(outcomes: list[SyncOutcome]) -> str:
    """``slug: code`` for each outcome, for a refresh's error detail."""
    return ", ".join(f"{o.slug}: {o.error_code or o.status}" for o in outcomes)


def _effective(identity: Identity) -> frozenset[str]:
    return frozenset(compute_effective(set(identity.permissions)))


class CatalogService:
    """Read + import operations for the catalog."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._cfg = ctx.config.catalog
        self._ingest_cfg = ctx.config.ingest
        self._sources: dict[str, CatalogSourceConfig] = {s.slug: s for s in self._cfg.sources}
        self._mappings = [
            CatalogApiIdMapping.from_config(m) for m in self._cfg.catalog_api_id_mappings
        ]

    # ── refresh ──────────────────────────────────────────────────────────────

    async def refresh(
        self,
        *,
        permissions: frozenset[str] | None = None,
        actor_id: str | None = None,
        force: bool = False,
    ) -> CatalogRefreshResult:
        """Sync now every enabled source the caller may read; ``force`` overrides
        the mass-removal guard.

        Sources the caller cannot read are left to the scanner, so the result
        (counts, outcomes and error detail) never covers a hidden source.
        ``permissions=None`` (internal callers only) refreshes every source. Each
        source's listing is bounded by ``ingest.fetch_timeout_s`` and all the
        listings together by ``catalog.refresh_deadline_seconds``; a source
        reached after that deadline is skipped and flagged for the scanner. A
        listing timeout here leaves the source's scheduled backoff alone.

        Raises ``CatalogUnavailableError`` when no source applied and at least
        one failed; its detail lists the failed sources, then any that were not
        refreshed (busy or skipped). Otherwise the result is ``partial`` when a
        source failed, was held by another run (busy) or was skipped, and the
        last good rows stay. A run held by the mass-removal guard is not a
        failure: the source was reachable and keeps serving its last applied rows.
        """
        allowed = (
            frozenset(s.slug for s in self._enabled())
            if permissions is None
            else allowed_sources(self._sources.values(), permissions=permissions, actor_id=actor_id)
        )
        sync = CatalogSourceSync(self._ctx)
        await sync.register_sources()
        outcomes = await sync.sync_all(
            forced=force,
            listing_timeout_s=self._ingest_cfg.fetch_timeout_s,
            deadline_s=self._cfg.refresh_deadline_seconds,
            slugs=allowed,
            count_timeouts=False,
        )
        applied = [o for o in outcomes if o.status == RUN_STATUS_APPLIED]
        held = (RUN_STATUS_APPLIED, RUN_STATUS_SUSPECT_EMPTY, RUN_STATUS_SUPERSEDED)
        pending = (OUTCOME_BUSY, OUTCOME_SKIPPED)
        failed = [o for o in outcomes if o.status not in (*held, *pending, OUTCOME_DISABLED)]
        not_run = [o for o in outcomes if o.status in pending]
        if failed and not applied:
            detail = f"catalog sources unavailable ({_outcome_codes(failed)})"
            if not_run:
                detail += f"; not refreshed ({_outcome_codes(not_run)})"
            raise CatalogUnavailableError(detail)
        snapshot = await self._snapshot()
        count = sum(1 for e in snapshot.entries if e.source_slug in allowed)
        return CatalogRefreshResult(
            count=count, partial=bool(failed or not_run), sources=tuple(outcomes)
        )

    # ── snapshot ─────────────────────────────────────────────────────────────

    def _enabled(self) -> list[CatalogSourceConfig]:
        return [s for s in self._sources.values() if s.enabled]

    async def _source_rows(self) -> dict[str, CatalogSourceRow]:
        async with self._ctx.registry_db.session() as session:
            rows = {r.slug: r for r in await CatalogSourceRepository.list_all(session)}
        if any(s.slug not in rows for s in self._sources.values()):
            await CatalogSourceSync(self._ctx).register_sources()
            async with self._ctx.registry_db.session() as session:
                rows = {r.slug: r for r in await CatalogSourceRepository.list_all(session)}
        return rows

    async def _sync_never_synced(
        self, rows: Mapping[str, CatalogSourceRow], visible: frozenset[str] | None
    ) -> bool:
        """Sync a never-synced, auto-syncing ``git-manifest`` source inline.

        Only such a source may hold up a read, so a fresh install's first list
        is not empty. The listing fetch is bounded by ``ingest.fetch_timeout_s``
        (staging and the apply are not). A busy lease or a failure leaves the
        source absent until the scanner syncs it; a failed or timed-out run is
        recorded, so the source is no longer never-synced and later reads do not
        wait on it while the scanner backs off.
        """
        due = [
            cfg.slug
            for cfg in self._enabled()
            if cfg.kind == "git-manifest"
            and cfg.max_age_seconds > 0
            and (visible is None or cfg.slug in visible)
            and (row := rows.get(cfg.slug)) is not None
            and row.last_status == SOURCE_STATUS_NEVER_SYNCED
            and row.last_applied_run_id is None
        ]
        if not due:
            return False
        sync = CatalogSourceSync(self._ctx)
        for slug in due:
            try:
                await sync.sync_source(slug, listing_timeout_s=self._ingest_cfg.fetch_timeout_s)
            except Exception:
                logger.warning("catalog_first_sync_failed", source=slug, exc_info=True)
        return True

    async def _flag_stale(self, rows: Mapping[str, CatalogSourceRow]) -> None:
        """Ask the scanner to sync sources past their max age (never syncs inline)."""
        now = utcnow()
        stale = [
            cfg.slug
            for cfg in self._enabled()
            if (row := rows.get(cfg.slug)) is not None
            and row.sync_wanted_at is None
            and is_due(cfg, row, now=now)
        ]
        if stale:
            async with self._ctx.registry_db.transaction() as session:
                await CatalogSourceRepository.mark_sync_wanted(session, stale)

    def _snapshot_key(self, rows: Mapping[str, CatalogSourceRow]) -> tuple[str, str]:
        config_digests = _config_digests(self._cfg)
        inputs = [
            digests.SourceVersionInputs(
                slug=cfg.slug,
                enabled=cfg.enabled,
                visibility=cfg.visibility,
                precedence=cfg.precedence,
                content_digest=row.content_digest if (row := rows.get(cfg.slug)) else None,
                listing_digest=row.listing_digest if row else None,
                config_digest=config_digests[cfg.slug],
            )
            for cfg in self._sources.values()
        ]
        return (
            digests.catalog_version(inputs),
            resolver_inputs_digest(list(self._sources.values()), self._mappings),
        )

    async def _load_snapshot(self, rows: Mapping[str, CatalogSourceRow]) -> CatalogSnapshot:
        key = self._snapshot_key(rows)
        owner = self._ctx.registry_db
        snapshot = cached_snapshot(owner, key)
        if snapshot is not None:
            return snapshot
        async with self._ctx.registry_db.session() as session:
            live = await CatalogEntryRepository.live_rows(
                session, [s.slug for s in self._enabled()]
            )
        snapshot = build_snapshot(key, live, sources=self._sources, mappings=self._mappings)
        store_snapshot(owner, snapshot)
        return snapshot

    async def _snapshot(self) -> CatalogSnapshot:
        """The whole snapshot, every enabled source included (internal reads only)."""
        return await self._load_snapshot(await self._source_rows())

    async def _read(
        self,
        *,
        permissions: frozenset[str],
        actor_id: str | None,
        include_alternates: bool = False,
        include_snoozed: bool = False,
    ) -> _ReadState:
        allowed = allowed_sources(
            self._sources.values(), permissions=permissions, actor_id=actor_id
        )
        rows = await self._source_rows()
        if await self._sync_never_synced(rows, allowed):
            rows = await self._source_rows()
        await self._flag_stale(rows)
        snapshot = await self._load_snapshot(rows)
        async with self._ctx.registry_db.session() as session:
            registered = await ApiRepository.registered_catalog_entry_ids(session)
            outdated_ids = await CatalogUpdateCheckRepository.outdated_entry_ids(
                session, now=utcnow(), include_snoozed=include_snoozed
            )
        view = snapshot.collapse(allowed, registered, include_alternates=include_alternates)
        return _ReadState(
            snapshot=snapshot,
            rows=rows,
            allowed=allowed,
            registered=registered,
            outdated_ids=outdated_ids,
            view=view,
        )

    @staticmethod
    def _to_view(state: _ReadState, entry: CatalogEntry) -> CatalogEntryView:
        registered = entry.entry_id in state.registered
        return CatalogEntryView(
            entry_id=entry.entry_id,
            source_slug=entry.source_slug,
            catalog_api_id=entry.catalog_api_id,
            vendor=entry.vendor,
            display_vendor=entry.display_vendor,
            title=entry.title,
            version=entry.version,
            tags=entry.tags,
            human_url=entry.human_url,
            has_spec=entry.has_spec,
            registered=registered,
            update_available=registered and entry.entry_id in state.outdated_ids,
            alternate_ids=state.view.alternates.get(entry.entry_id, ()),
        )

    def _oldest_sync_age(self, state: _ReadState) -> int | None:
        synced = [
            row.last_sync_at
            for slug in state.allowed
            if (row := state.rows.get(slug)) is not None and row.last_sync_at is not None
        ]
        if not synced:
            return None
        return max(0, int((utcnow() - min(synced)).total_seconds()))

    def trigger_update_notify_sweep(self) -> None:
        """Fire-and-forget the update-notify sweep after an operator refresh.

        Called by ``POST /catalog:refresh``: an intentional "check upstream now"
        should sweep too. Catalog reads never trigger it; the periodic sweep is
        ``CatalogUpdateScanner``'s (:meth:`run_update_sweep`).

        The sweep issues up to N conditional GETs, so awaiting it inline would hold
        the refresh response on the whole batch. We detach it into a background
        task instead; failures are logged, never raised back to the caller. The
        task is parked in ``_SWEEP_TASKS`` so it isn't GC'd
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
        the fire-and-forget ``:refresh`` piggyback. Honors the same kill switch.
        """
        await self._run_update_notify_sweep()

    async def _run_update_notify_sweep(self) -> None:
        """Probe registered specs for upstream changes; emit an event on change.

        For each API imported from a still-listed catalog entry, at most once per
        ``update_check_interval_seconds``: an entry whose listing ``content_hint``
        equals the one last probed is unchanged and is not fetched; otherwise the
        spec is fetched through the entry's source with the last-seen validator. A
        "not modified" answer or an unchanged digest is a no-op, a changed digest
        emits ``catalog.update_available`` once (deduped on
        ``last_notified_digest``). Upstream/DB failures are swallowed per API so a
        flaky host never breaks the refresh or the rest of the batch. A ``0``
        interval disables the sweep entirely (air-gapped kill switch).

        Single-flight: sweeps are serialized **within a process** by
        ``Context.update_sweep_lock`` (scanner + ``:refresh`` trigger can't overlap) and
        **across processes** by a non-blocking registry-DB advisory lock
        (:data:`UPDATE_SWEEP_LOCK_KEY`, a no-op on single-process SQLite). A sweep that
        loses either race skips quietly — another sweep is already doing the work — so
        at most one sweep at a time reads and advances the per-API dedupe state, and a
        changed digest emits once. The residual duplicate is the emit-then-mark crash
        window described in :meth:`_probe_one`. Probes fan out with bounded
        concurrency (``update_sweep_max_concurrency``, kept below the DB pool) under
        a wall-clock budget (``update_sweep_deadline_seconds``) so a large or hostile
        candidate set can't turn one refresh into an unbounded stall.
        """
        interval = self._cfg.update_check_interval_seconds
        if interval <= 0:
            return

        # In-process guard first (cheap, no DB connection). Every caller checks
        # locked() before acquiring and there is no await in between, so nobody ever
        # queues on this lock: a concurrent trigger either skips here or holds it.
        lock = self._ctx.update_sweep_lock
        if lock.locked():
            logger.debug("catalog_update_sweep_skipped_in_flight")
            return
        # The cross-process guard (advisory lock) is taken only once the in-process one
        # is held; another replica/worker sweeping right now holds it.
        async with (
            lock,
            self._ctx.registry_db.try_advisory_lock(UPDATE_SWEEP_LOCK_KEY) as held,
        ):
            if not held:
                logger.debug("catalog_update_sweep_skipped_locked_elsewhere")
                return
            await self._do_update_notify_sweep(interval)

    async def _do_update_notify_sweep(self, interval: int) -> None:
        """Sweep body — always called under both single-flight locks."""
        async with self._ctx.registry_db.session() as session:
            candidates = await ApiRevisionRepository.registered_specs_for_notify(session)
            entries = await CatalogEntryRepository.live_entries(
                session, {spec.catalog_entry_id for spec in candidates}
            )
        # Tracked: the API's entry is still listed by an enabled source.
        enabled = {s.slug for s in self._enabled()}
        tracked = [
            (spec, row)
            for spec in candidates
            if (row := entries.get(spec.catalog_entry_id)) is not None
            and row.source_slug in enabled
        ]
        if not tracked:
            return
        fetcher = CatalogSpecFetcher(self._ctx)

        now = utcnow()
        sem = asyncio.Semaphore(max(1, self._cfg.update_sweep_max_concurrency))
        stats = {"probed": 0, "failed": 0}

        async def _guarded(spec: RegisteredSpec, row: CatalogEntryRow) -> None:
            async with sem:
                try:
                    await self._probe_one(spec, row, fetcher, now=now, interval=interval)
                    stats["probed"] += 1
                except Exception:  # best-effort per API, never break the sweep
                    stats["failed"] += 1
                    logger.warning(
                        "catalog_update_probe_failed",
                        api_id=str(spec.api_id),
                        catalog_entry_id=spec.catalog_entry_id,
                        exc_info=True,
                    )

        started = asyncio.get_running_loop().time()
        try:
            await asyncio.wait_for(
                asyncio.gather(*(_guarded(spec, row) for spec, row in tracked)),
                timeout=self._cfg.update_sweep_deadline_seconds,
            )
        except TimeoutError:
            logger.warning(
                "catalog_update_sweep_deadline_hit",
                candidates=len(tracked),
                probed=stats["probed"],
            )
        logger.info(
            "catalog_update_sweep_complete",
            candidates=len(tracked),
            probed=stats["probed"],
            failed=stats["failed"],
            duration_ms=int((asyncio.get_running_loop().time() - started) * 1000),
        )

    def _classify_update(self, spec: RegisteredSpec, upstream_digest: str) -> str:
        """Classify a detected upstream change as plain-update vs overlay-conflict.

        The served revision carries an overlay only when it is overlay-origin. In that
        case the overlay was materialized over a *base* whose digest we persist as
        ``overlay_base_digest`` (A2). If the upstream now differs from that base, adopting
        it would supersede the operator's overlay — an operator decision, not a routine
        nudge — so we classify it as ``CATALOG_UPDATE_CONFLICTS_OVERLAY``.

        Everything else is a plain ``CATALOG_UPDATE_AVAILABLE``:
        - non-overlay revisions (catalog/manual imports) have no overlay to conflict with;
        - overlay revisions materialized before A2 have a NULL base digest (unknown base):
          we cannot prove a conflict, so we fall back to the safe, non-alarming class and
          let the next re-materialize self-heal the column.
        """
        if (
            spec.origin == ORIGIN_OVERLAY
            and spec.overlay_base_digest is not None
            and upstream_digest != spec.overlay_base_digest
        ):
            return EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY
        return EventType.CATALOG_UPDATE_AVAILABLE

    @staticmethod
    def _is_snoozed(check: Any, upstream_digest: str, now: datetime) -> bool:
        """True if an active operator snooze covers this exact upstream digest (C1).

        A snooze pins ``snoozed_digest`` (the accepted upstream digest) and an optional
        ``snoozed_until`` expiry (None = mute-until-newer). It suppresses the sweep emit only
        while it matches the digest currently being observed and hasn't lapsed — a genuinely
        newer upstream digest won't match and re-notifies normally.

        Keyed on ``upstream_digest`` (the digest the sweep is *emitting* for), whereas the
        repo-side outdated-set exclusion (:meth:`CatalogUpdateCheckRepository._not_snoozed`)
        keys on ``last_notified_digest``. Those agree because a snoozed change marks
        ``last_notified_digest = upstream_digest`` (inline, since it emits no event) before it
        returns — see the snooze branch in :meth:`_probe_one`. The *emitting* (non-snoozed)
        path marks after a successful emit (emit-then-mark, #941), which does not affect this
        predicate because a snoozed row never reaches the emit path.
        """
        snoozed_digest = getattr(check, "snoozed_digest", None)
        if snoozed_digest is None or snoozed_digest != upstream_digest:
            return False
        snoozed_until = getattr(check, "snoozed_until", None)
        return snoozed_until is None or snoozed_until > now

    @staticmethod
    def _update_summary(
        event_class: str, spec: RegisteredSpec, overlay_id: str | None = None
    ) -> str:
        who = f"{spec.vendor}/{spec.name} ({spec.version})"
        if event_class == EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY:
            ovl = f" {overlay_id}" if overlay_id else ""
            return (
                f"Upstream spec changed under confirmed overlay{ovl} for {who} — adopt "
                "upstream (needs catalog:import + overlays:confirm) or keep the overlay"
            )
        return f"Upstream spec updated for {who}"

    def _unnotified(self, spec: RegisteredSpec, check: Any) -> bool:
        """True if ``check`` recorded an upstream change that was never notified."""
        seen = check.last_seen_digest
        if seen is None or seen == spec.spec_digest:
            return False
        return (seen, self._classify_update(spec, seen)) != (
            check.last_notified_digest,
            check.last_notified_event_class,
        )

    async def _record_unchanged(
        self, spec: RegisteredSpec, *, now: datetime, validator: str | None, hint: str | None
    ) -> None:
        """Record a probe that found the entry's spec unchanged since the last one."""
        async with self._ctx.registry_db.transaction() as session:
            await CatalogUpdateCheckRepository.upsert(
                session,
                local_api_id=spec.api_id,
                catalog_entry_id=spec.catalog_entry_id,
                validator=validator,
                digest=None,
                checked_at=now,
                content_hint=hint,
            )

    async def _probe_one(
        self,
        spec: RegisteredSpec,
        entry: CatalogEntryRow,
        fetcher: CatalogSpecFetcher,
        *,
        now: datetime,
        interval: int,
    ) -> None:
        """Probe one API's catalog entry and emit on a fresh change.

        Unchanged without a fetch when the entry still lists the (non-null)
        ``content_hint`` last probed; otherwise fetched through the entry's source,
        conditionally on the validator last seen for the same entry. Either way, a
        change an earlier probe recorded but never notified is re-decided.
        """
        async with self._ctx.registry_db.session() as session:
            check = await CatalogUpdateCheckRepository.get(session, spec.api_id)

        if check is not None and check.last_checked_at is not None:
            age = (now - check.last_checked_at).total_seconds()
            if age < interval:
                return

        same_entry = check is not None and check.catalog_entry_id == spec.catalog_entry_id
        hint = entry.content_hint
        # A change seen by an earlier probe whose notification never went out
        # (the emit failed after the probe was recorded). An unchanged hint or a
        # not-modified answer must still re-run the notify decision for it,
        # otherwise the update would never be announced.
        pending = (
            check.last_seen_digest
            if same_entry and check is not None and self._unnotified(spec, check)
            else None
        )
        validator: str | None
        if (
            same_entry
            and hint is not None
            and check is not None
            and check.last_seen_content_hint == hint
        ):
            if pending is None:
                await self._record_unchanged(spec, now=now, validator=None, hint=hint)
                return
            upstream_digest, validator = pending, check.last_seen_validator
        else:
            prior_validator = (
                check.last_seen_validator if same_entry and check is not None else None
            )
            # A bulk probe: the background lane, so it never takes the slots and
            # tokens a user's preview or import waits on.
            lane = spec_fetch_lane.set("background")
            try:
                result = await fetcher.fetch_row(entry, validator=prior_validator)
            finally:
                spec_fetch_lane.reset(lane)
            if isinstance(result, NotModified):
                if pending is None:
                    await self._record_unchanged(
                        spec, now=now, validator=result.validator, hint=hint
                    )
                    return
                upstream_digest, validator = pending, result.validator
            else:
                upstream_digest, validator = result.digest, result.validator

        last_notified = check.last_notified_digest if check is not None else None
        last_notified_class = check.last_notified_event_class if check is not None else None
        # A change worth notifying: the upstream bytes differ from what backs the
        # registered revision, and we have not already notified for this exact
        # (upstream digest, event class) pair. Comparing against spec_digest (not just
        # last_seen) means a spec that reverts to the registered content stops
        # notifying. The event class is part of the dedupe key so a digest that
        # re-classifies (e.g. an overlaid API whose upstream now collides with the
        # overlay's base) still fires the new class exactly once.
        in_sync = upstream_digest == spec.spec_digest
        event_class = self._classify_update(spec, upstream_digest)
        changed = not in_sync and (upstream_digest, event_class) != (
            last_notified,
            last_notified_class,
        )

        async with self._ctx.registry_db.transaction() as session:
            await CatalogUpdateCheckRepository.upsert(
                session,
                local_api_id=spec.api_id,
                catalog_entry_id=spec.catalog_entry_id,
                validator=validator,
                digest=upstream_digest,
                checked_at=now,
                content_hint=hint,
                # Deliberately NOT stamping notified_digest/class here: the event is
                # emitted below in a *separate* (admin-DB) transaction, so marking notified
                # before the emit could permanently suppress the notification if the process
                # crashes in between (#941). We mark via mark_notified only *after* a
                # successful emit — emit-then-mark.
                #
                # sync_notified is the one exception: when upstream matches the served
                # revision again (e.g. a bad publish was reverted) it fires *no* event, so
                # pinning last_notified_digest to it here is safe and clears the outdated
                # read surface. Otherwise a revert leaves the badge stuck lit with no
                # operator action able to resolve it. Dedupe is preserved — a later
                # genuinely different upstream digest still re-fires exactly once.
                sync_notified=in_sync,
            )

        if not changed:
            return

        # Snooze/mute (C1, #925): an operator accepted this exact upstream digest without
        # adopting it, so suppress the *notification* while the snooze is active. This path
        # emits no event, so we mark notified inline (emit-then-mark only defers the marker
        # to guard the cross-DB emit; there is nothing to guard here). Marking is required:
        # the read-surface snooze exclusion keys on ``snoozed_digest == last_notified_digest``
        # (see :meth:`CatalogUpdateCheckRepository._not_snoozed`), so without advancing
        # ``last_notified_digest`` to the snoozed digest the badge would wrongly re-light
        # despite the active snooze. We skip the emit so no new inbox/rail item is created. A
        # genuinely newer upstream digest won't match ``snoozed_digest`` and will emit
        # normally. ``snoozed_until`` in the past means the snooze lapsed → emit again.
        if check is not None and self._is_snoozed(check, upstream_digest, now):
            async with self._ctx.registry_db.transaction() as session:
                await CatalogUpdateCheckRepository.mark_notified(
                    session,
                    local_api_id=spec.api_id,
                    notified_digest=upstream_digest,
                    notified_event_class=event_class,
                )
            logger.info(
                "catalog_update_notify_snoozed",
                api_id=str(spec.api_id),
                upstream_digest=upstream_digest,
                event_class=event_class,
            )
            return

        # For a conflict, resolve the live overlay's id so the actionable event can
        # deep-link to the overlay to keep/rollback (parallel to the refuse path). The
        # conflict class is only reached when the current revision *is* the live confirmed
        # overlay, so this resolves to exactly that overlay (best-effort: None if a race
        # already moved it — the event still carries api_id + event_class).
        conflict_overlay_id: str | None = None
        if event_class == EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY:
            async with self._ctx.registry_db.session() as session:
                live_overlay = await OverlayRepository.get_live_confirmed_for_api(
                    session, spec.api_id
                )
            conflict_overlay_id = live_overlay.id if live_overlay is not None else None

        # Emit-then-mark durability (#941) requires the *raising* emit here, NOT
        # emit_event_best_effort: the marker below must only be written when the event
        # actually landed. emit_event_best_effort swallows failures, so a transient
        # admin-DB error would return normally and let mark_notified stamp the dedupe key
        # for an event that was never delivered — permanently suppressing it. Letting
        # emit_event raise rolls back this (empty) admin txn and propagates to the sweep's
        # per-API guard (_guarded), which logs + isolates the failure and leaves the row
        # unmarked, so the next sweep re-emits. Worst case is one duplicate on a crash
        # *after* this commit but before the mark — the accepted trade-off.
        async with self._ctx.admin_db.transaction() as session:
            await emit_event(
                session,
                type=event_class,
                severity=EventSeverity.INFO,
                summary=self._update_summary(event_class, spec, conflict_overlay_id),
                # Actionable: an operator can resolve it by re-importing the upstream spec
                # (one-click in the UI / `jentic catalog outdated` + import in the CLI). A
                # catalog re-import that adopts the upstream drops the API out of the
                # outdated set (the served revision's digest now equals the notified one),
                # so the badge/count clear. Caveat:
                # when the served revision is a *manually PUBLISHED* one (not a catalog import),
                # a catalog re-import is blocked by ``ix_api_revisions_one_active`` — the
                # operator must archive/replace the published revision to resolve; until then
                # the outdated flag correctly stays lit.
                requires_action=True,
                created_by=None,
                data={
                    "api_id": str(spec.api_id),
                    "vendor": spec.vendor,
                    "name": spec.name,
                    "version": spec.version,
                    "current_digest": spec.spec_digest,
                    "upstream_digest": upstream_digest,
                    "catalog_entry_id": spec.catalog_entry_id,
                    "spec_url": spec.source_url,
                    "event_class": event_class,
                    "overlay_base_digest": spec.overlay_base_digest,
                    # Present only for conflicts_overlay so the inbox card can deep-link to
                    # the overlay; None/absent for plain update_available.
                    "overlay_id": conflict_overlay_id,
                    # L1 (#920): a structured "why" so the operator/author sees what
                    # collided rather than a bare "your fix was removed". We do NOT parse +
                    # JSONPath-diff the two specs here — the sweep is a hot per-API tick that
                    # only fetched bytes + digest, and the full diff belongs on the on-demand
                    # conflict view (which loads both specs once). Instead we carry the three
                    # digests that pin the collision: the base the overlay was materialized
                    # over (``base_digest``), what is served now (``served_digest`` = the
                    # overlaid revision), and the diverged upstream (``upstream_digest``).
                    # ``base_digest != upstream_digest`` is exactly the classify condition,
                    # so a UI can state "upstream moved off the base your overlay was built
                    # on" and offer keep/adopt with these anchors. Present only for the
                    # conflict class.
                    "conflict": (
                        {
                            "base_digest": spec.overlay_base_digest,
                            "served_digest": spec.spec_digest,
                            "upstream_digest": upstream_digest,
                        }
                        if event_class == EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY
                        else None
                    ),
                },
            )
        # Emit committed above (admin DB). Now durably record that we notified for this
        # (digest, class) so the next sweep dedupes — emit-then-mark (#941). A crash before
        # this point re-emits next sweep (at worst one duplicate) rather than permanently
        # suppressing the notification.
        async with self._ctx.registry_db.transaction() as session:
            marked = await CatalogUpdateCheckRepository.mark_notified(
                session,
                local_api_id=spec.api_id,
                notified_digest=upstream_digest,
                notified_event_class=event_class,
            )
        if marked == 0:
            # The observation upsert above committed a row, so a 0 here means it was
            # concurrently deleted (operator action / a future concurrent sweep) between
            # the upsert and this mark. The event was emitted but not deduped — it re-fires
            # next sweep (safe direction). Surface it so an "emitted but unmarked" tick is
            # observable rather than silent, mirroring overlay_supersede_not_deprecated.
            logger.warning(
                "catalog_update_marked_zero_rows",
                api_id=str(spec.api_id),
                upstream_digest=upstream_digest,
                event_class=event_class,
            )
        record_update_notified(event_class)

    # ── browse / get / resolve ───────────────────────────────────────────────

    async def list_all(
        self,
        *,
        permissions: frozenset[str],
        actor_id: str | None = None,
        q: str | None = None,
        registered_only: bool = False,
        unregistered_only: bool = False,
        outdated_only: bool = False,
        cursor: str | None = None,
        limit: int = 50,
        include_snoozed: bool = False,
        include_alternates: bool = False,
    ) -> CatalogListView:
        """List a keyset page of the entries visible to the caller.

        ``permissions`` is the caller's effective permission set (an empty set
        sees public sources only). Visible entries sharing a ``catalog_api_id``
        are collapsed to one winner unless ``include_alternates``. The status
        counters (``catalog_total``, ``registered_count``, ``outdated_count``)
        cover the collapsed visible list before the list filters and the page;
        ``registered_count`` and ``outdated_count`` also count registered entries
        collapsed into a winner, and ``registered_only`` / ``outdated_only`` list
        them, so two registered entries for one ``catalog_api_id`` both show.

        Browse orders by ``entry_id``; search by ``(-score, entry_id)``. A cursor
        is only meaningful for the same query and filters it was issued under,
        and a sync between pages may skip or repeat entries near it (never an
        error). Raises ``InvalidCursorError`` on a malformed cursor.
        """
        state = await self._read(
            permissions=permissions,
            actor_id=actor_id,
            include_alternates=include_alternates,
            include_snoozed=include_snoozed,
        )
        visible = state.view.entries
        registered = state.registered
        # The registered set is small next to the catalog: count from its side.
        # A registered entry collapsed into a winner is still the caller's API.
        visible_registered = registered & (state.view.ids | state.view.winner_of.keys())
        outdated = visible_registered & state.outdated_ids
        registered_count = len(visible_registered)

        after_id, after_score = decode_catalog_cursor(cursor) if cursor else (None, None)
        if mb.has_query_tokens(q):
            pool: Sequence[CatalogEntry] = visible
            if registered_only or outdated_only:
                pool = sorted((state.snapshot.by_id[i] for i in visible_registered), key=_entry_key)
            scored = mb.score_entries(pool, q, tokens=state.snapshot.tokens, ordered=True)
            if registered_only:
                scored = [(e, s) for e, s in scored if e.entry_id in registered]
            elif unregistered_only:
                scored = [(e, s) for e, s in scored if e.entry_id not in registered]
            if outdated_only:
                scored = [(e, s) for e, s in scored if e.entry_id in outdated]
            page = mb.paginate_entries(
                scored, after_entry_id=after_id, after_score=after_score, limit=limit
            )
        else:
            browse: Sequence[CatalogEntry] = visible
            if outdated_only:
                browse = sorted((state.snapshot.by_id[i] for i in outdated), key=_entry_key)
            elif registered_only:
                browse = sorted(
                    (state.snapshot.by_id[i] for i in visible_registered), key=_entry_key
                )
            elif unregistered_only:
                browse = [e for e in visible if e.entry_id not in registered]
            page = mb.paginate_browse(browse, after_entry_id=after_id, limit=limit)
        next_cursor = (
            encode_catalog_cursor(page.next_entry_id, page.next_score)
            if page.has_more and page.next_entry_id is not None
            else None
        )
        return CatalogListView(
            data=[self._to_view(state, e) for e in page.items],
            catalog_total=len(visible),
            registered_count=registered_count,
            oldest_source_sync_age_seconds=self._oldest_sync_age(state),
            has_more=page.has_more,
            next_cursor=next_cursor,
            outdated_count=len(outdated),
        )

    @staticmethod
    def _lookup(state: _ReadState, entry_id: str) -> CatalogEntry:
        """The visible entry ``entry_id``; a hidden one is answered like a missing one.

        ``did_you_mean`` is set only when the id's first segment is no visible
        source slug and the whole id is a visible ``catalog_api_id`` (an id in
        the pre-``entry_id`` form); it names that id's collapse winner.
        """
        entry = state.snapshot.by_id.get(entry_id)
        if entry is not None and entry.source_slug in state.allowed:
            return entry
        did_you_mean = None
        if entry_id.split("/", 1)[0] not in state.allowed:
            did_you_mean = state.view.winner_by_api_id.get(entry_id)
        raise CatalogEntryNotFoundError(entry_id, did_you_mean=did_you_mean)

    async def _visible_entry(
        self, entry_id: str, *, permissions: frozenset[str], actor_id: str | None
    ) -> tuple[_ReadState, CatalogEntry]:
        state = await self._read(permissions=permissions, actor_id=actor_id)
        return state, self._lookup(state, entry_id)

    async def get(
        self, entry_id: str, *, permissions: frozenset[str], actor_id: str | None = None
    ) -> CatalogEntryView:
        """The visible entry ``entry_id`` (a collapsed-out alternate included)."""
        state, entry = await self._visible_entry(
            entry_id, permissions=permissions, actor_id=actor_id
        )
        return self._to_view(state, entry)

    async def resolve(
        self,
        catalog_api_id: str,
        *,
        permissions: frozenset[str],
        actor_id: str | None = None,
    ) -> CatalogEntryView:
        """The collapse winner for ``catalog_api_id`` among the visible entries.

        Visibility follows ``permissions`` (and ``actor_id``) as in ``list_all``.

        Raises ``CatalogEntryNotFoundError`` when no visible entry carries it.
        """
        state = await self._read(permissions=permissions, actor_id=actor_id)
        winner = state.view.winner_by_api_id.get(catalog_api_id)
        if winner is None:
            raise CatalogEntryNotFoundError(catalog_api_id)
        return self._to_view(state, state.snapshot.by_id[winner])

    async def public_winner(self, catalog_api_id: str) -> str | None:
        """The ``entry_id`` ``resolve`` picks for a caller with no permissions, if any.

        Reads the current snapshot only: unlike a catalog read it never syncs a
        source inline nor flags stale ones, so a caller on another hot path
        (the connect hand-off) never waits on a listing.
        """
        allowed = allowed_sources(self._sources.values(), permissions=frozenset(), actor_id=None)
        snapshot = await self._load_snapshot(await self._source_rows())
        async with self._ctx.registry_db.session() as session:
            registered = await ApiRepository.registered_catalog_entry_ids(session)
        return snapshot.collapse(allowed, registered).winner_by_api_id.get(catalog_api_id)

    # ── preview ──────────────────────────────────────────────────────────────

    @staticmethod
    def _require_spec(entry: CatalogEntry) -> None:
        """A card-only entry (no ``spec_ref``) has nothing to preview or import."""
        if not entry.has_spec:
            raise CatalogEntryHasNoSpecError(entry.entry_id)

    async def preview(
        self,
        entry_id: str,
        *,
        permissions: frozenset[str],
        actor_id: str | None = None,
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

        The spec is fetched through the entry's source after the visibility
        check, so a hidden entry still answers 404. Raises
        ``CatalogSpecGoneError`` when the source says the spec is gone and
        ``CatalogSourceUnavailableError`` when the source is throttled or down.
        """
        _state, entry = await self._visible_entry(
            entry_id, permissions=permissions, actor_id=actor_id
        )
        self._require_spec(entry)
        _row, body = await CatalogSpecFetcher(self._ctx).fetch_body(entry.entry_id)
        try:
            doc = parse_spec_content(
                body.content.decode("utf-8-sig"),
                filename="openapi.json" if body.media_type == "application/json" else None,
            )
        except (UnicodeDecodeError, IngestStageError) as exc:
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
            server_variables=projection.server_variables,
            required_schemes=projection.required_schemes,
        )

    # ── import ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_import_source(
        entry: CatalogEntry,
        identity: Identity,
        *,
        vendor: str | None,
        name: str,
        switch_source: bool,
    ) -> dict[str, Any]:
        """Build the ``CatalogRefSource`` payload: the entry's id, never its spec location.

        The job fetches the spec through the entry's source, so neither the
        source's credentials nor its ``spec_ref`` enter the job payload. The
        entry's identity vendor and an ``api_name`` seed are passed as
        overrides, because many catalog specs omit ``x-vendor``/``contact.name``
        and identity (and re-import dedup) must follow the catalog, not the
        upstream ``info`` block. ``vendor``/``name`` default to the entry's;
        a re-import passes the ones already stored on the local API so it lands
        on that API's ``(vendor, name, version)`` even when the derivation has
        since changed, keeping the credentials and permission rules keyed on it.

        ``catalog_api_id`` and the entry id are persisted verbatim on the Api
        row; ``submitted_by`` attributes the revision to the caller.
        """
        resolved_vendor = vendor or entry.vendor
        source = CatalogRefSource(
            type="catalog",
            catalog_entry_id=entry.entry_id,
            catalog_api_id=entry.catalog_api_id,
            origin=ORIGIN_CATALOG,
            api_name=name,
            vendor=resolved_vendor or None,
            submitted_by=identity.sub or None,
            switch_source=switch_source,
        )
        return source.model_dump(mode="json", exclude_none=True)

    async def _import_target(
        self,
        entry: CatalogEntry,
        *,
        catalog_ids: Collection[str],
        switch_source: bool,
    ) -> tuple[str | None, str]:
        """The ``(vendor, name)`` an import of ``entry`` lands on.

        ``catalog_ids`` are the snapshot's ``catalog_api_id``s, which the
        clash-aware name derivation reads.

        1. The API already imported from this entry (``catalog_entry_id``): a
           re-import updates it.
        2. An API holding the entry's ``catalog_api_id`` without a recorded entry
           (imported before entry ids were recorded, so from the legacy source):
           an import of a legacy-source entry adopts it. For any other source it
           counts as imported from another entry (step 3).
        3. An API holding the ``catalog_api_id`` imported from another entry: with
           ``switch_source`` the import supersedes that API (its credentials now
           point at this entry's spec); without, it takes a distinct name seeded
           from ``{source_slug}~…`` and leaves that API untouched.
        4. Otherwise a fresh, clash-aware name.
        """
        async with self._ctx.registry_db.session() as session:
            own = await ApiRepository.identity_for_catalog_entry_id(session, entry.entry_id)
            holders = (
                []
                if own is not None
                else await ApiRepository.catalog_holders(session, entry.catalog_api_id)
            )
        if own is not None:
            return own
        if entry.source_slug == self._cfg.legacy_source_slug:
            unrecorded = [h for h in holders if h.catalog_entry_id is None]
            if unrecorded:
                return unrecorded[0].vendor, unrecorded[0].name
        derived = mb.catalog_api_name(entry.catalog_api_id, catalog_ids)
        if not holders:
            return None, derived
        if switch_source:
            target = next((h for h in holders if h.is_current), holders[0])
            logger.info(
                "catalog_source_switch",
                entry_id=entry.entry_id,
                api_id=str(target.id),
                previous_entry_id=target.catalog_entry_id,
            )
            return target.vendor, target.name
        return None, f"{entry.source_slug}{NAMESPACE_SEPARATOR}{derived}"

    async def _authorize_overlay_supersede(
        self, entry: CatalogEntry, identity: Identity
    ) -> str | None:
        """Gate a re-import that would supersede a live confirmed overlay.

        Returns the overlay id to auto-deprecate when the caller is authorized to
        supersede it (so the enqueued job can stamp ``supersede_overlay_id``), ``None``
        when there is no live overlay to supersede (ordinary re-import), or raises
        :class:`OverlaySupersedeForbiddenError` when a supersede is required but the
        caller lacks ``overlays:confirm`` — re-emitting an operator-facing conflict event
        so the fix is not silently discarded.

        The check keys on the entry (the API imported from it) to find the locally
        served current revision and asks whether that revision *is* a confirmed
        overlay's materialization. Only that case is a supersede.
        """
        async with self._ctx.registry_db.session() as session:
            current = await ApiRevisionRepository.current_revision_for_catalog_entry_id(
                session, entry.entry_id
            )
            if current is None:
                return None
            api_id, current_revision_id = current
            overlay = await OverlayRepository.get_live_confirmed_for_revision(
                session, api_id, current_revision_id
            )
        if overlay is None:
            return None

        if has_effective_permission(identity.permissions, "overlays:confirm"):
            return overlay.id

        # Refuse: do not enqueue a silent revert. Re-surface the conflict for an operator
        # who can decide (confirm-scope holder), keyed on api_id so it settles on resolve.
        # Each refused attempt is a distinct operator-relevant signal, so it is not
        # digest-deduped; they all settle together on the eventual authorized import.
        #
        # The refusing actor goes to structlog (server-side) rather than the event
        # ``data``: event payloads are readable by any ``events:read`` holder.
        logger.info(
            "overlay_supersede_refused",
            api_id=str(api_id),
            overlay_id=overlay.id,
            refused_actor=identity.sub,
        )
        async with self._ctx.admin_db.transaction() as session:
            await emit_event_best_effort(
                session,
                type=EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY,
                severity=EventSeverity.INFO,
                summary=(
                    f"Re-import of {entry.entry_id} was refused: adopting upstream would "
                    f"supersede confirmed overlay {overlay.id} "
                    "(needs catalog:import + overlays:confirm)"
                ),
                requires_action=True,
                created_by=None,
                data={
                    "api_id": str(api_id),
                    "overlay_id": overlay.id,
                    "catalog_entry_id": entry.entry_id,
                    "event_class": EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY,
                },
            )
        raise OverlaySupersedeForbiddenError(entry.entry_id, overlay.id)

    async def import_entry(
        self,
        entry_id: str,
        identity: Identity,
        *,
        switch_source: bool = False,
        permissions: frozenset[str] | None = None,
    ) -> str:
        """Enqueue an async import of a visible catalog entry; return the job id.

        ``permissions`` is the visibility set (the caller's effective permissions
        by default). ``switch_source`` lets the import supersede an API imported
        from another entry with the same ``catalog_api_id``; it needs
        ``credentials:write`` (``SourceSwitchForbiddenError`` otherwise) and is a
        no-op when there is no such API.

        If re-importing would supersede a live confirmed overlay, the caller must hold
        ``overlays:confirm``; an authorized supersede stamps ``supersede_overlay_id``
        on the job so the worker auto-deprecates the overlay in the re-ingest
        transaction, and an unauthorized caller is refused
        (``OverlaySupersedeForbiddenError``) rather than silently reverting the fix.

        If the re-import would change the server hosts of an API with bound credentials,
        only a caller holding ``credentials:write`` makes it current; otherwise the worker
        keeps the new revision as a draft for operator review (see
        ``registry/ingest/host_change_guard.py``).
        """
        effective = _effective(identity)
        if switch_source and CREDENTIALS_WRITE not in effective:
            raise SourceSwitchForbiddenError()
        state, entry = await self._visible_entry(
            entry_id,
            permissions=effective if permissions is None else permissions,
            actor_id=identity.sub or None,
        )
        self._require_spec(entry)
        supersede_overlay_id = await self._authorize_overlay_supersede(entry, identity)
        vendor, name = await self._import_target(
            entry,
            catalog_ids={e.catalog_api_id for e in state.snapshot.entries},
            switch_source=switch_source,
        )
        source = self._to_import_source(
            entry, identity, vendor=vendor, name=name, switch_source=switch_source
        )
        if supersede_overlay_id is not None:
            source["supersede_active"] = True
        if may_approve_host_change(identity.permissions):
            # Server-host change guard: an operator (``credentials:write``) may let a
            # re-import that changes the API's server hosts become current even when
            # the API has bound credentials. Everyone else gets such a revision held
            # as a draft for review. Server-set only; client schemas cannot carry it.
            source["host_change_approved"] = True
        payload: dict[str, Any] = {"sources": [source]}
        if supersede_overlay_id is not None:
            payload["supersede_overlay_id"] = supersede_overlay_id
        async with self._ctx.admin_db.transaction() as session:
            job_id = await enqueue_job(
                session,
                JobKind.IMPORT,
                created_by=identity.sub,
                actor_type=identity.actor_type,
                payload=payload,
            )
        record_reimport_from_catalog()
        return job_id

    # ── snooze ───────────────────────────────────────────────────────────────

    async def snooze_entry(
        self, entry_id: str, identity: Identity, *, until: datetime | None = None
    ) -> None:
        """Snooze the outstanding update notification for a visible catalog entry.

        Operator action: quiet the "update available" badge for a known-and-accepted
        upstream change without adopting it. Requires ``events:write`` — a
        low-privilege agent must not be able to hide a real upstream drift. Resolves
        the entry to the local API imported from it, pins the snooze
        to the digest the sweep last notified for (so a genuinely newer change
        re-lights the badge), and audits it.

        ``until=None`` mutes until a newer upstream digest lands; a future ``until`` is
        a time-boxed snooze.
        """
        self._require_snooze_permission(identity)
        _state, entry = await self._visible_entry(
            entry_id, permissions=_effective(identity), actor_id=identity.sub or None
        )
        async with self._ctx.registry_db.transaction() as session:
            resolved = await self._resolve_local_api(session, entry)
            if resolved is None:
                raise CatalogEntryNotFoundError(entry_id)
            local_api_id, check = resolved
            digest = check.last_notified_digest if check is not None else None
            if digest is None:
                raise NothingToSnoozeError(entry_id)
            await CatalogUpdateCheckRepository.snooze(
                session, local_api_id, digest=digest, until=until
            )
        record_update_snoozed()
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.API,
            target_id=str(local_api_id),
            actor_type=ActorType(identity.actor_type) if identity.actor_type else ActorType.USER,
            actor_id=identity.sub,
            after={
                "catalog_update_snoozed": True,
                "snoozed_until": until.isoformat() if until else None,
            },
            origin=None,
        )

    async def unsnooze_entry(self, entry_id: str, identity: Identity) -> None:
        """Clear a snooze for a visible catalog entry. Operator-gated (``events:write``)."""
        self._require_snooze_permission(identity)
        _state, entry = await self._visible_entry(
            entry_id, permissions=_effective(identity), actor_id=identity.sub or None
        )
        async with self._ctx.registry_db.transaction() as session:
            resolved = await self._resolve_local_api(session, entry)
            if resolved is None:
                raise CatalogEntryNotFoundError(entry_id)
            local_api_id, _ = resolved
            await CatalogUpdateCheckRepository.unsnooze(session, local_api_id)
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.API,
            target_id=str(local_api_id),
            actor_type=ActorType(identity.actor_type) if identity.actor_type else ActorType.USER,
            actor_id=identity.sub,
            after={"catalog_update_snoozed": False},
            origin=None,
        )

    @staticmethod
    def _require_snooze_permission(identity: Identity) -> None:
        """Snooze/unsnooze is an operator event-management action → ``events:write``."""
        if not has_effective_permission(identity.permissions, "events:write"):
            raise SnoozeForbiddenError()

    @staticmethod
    async def _resolve_local_api(session: Any, entry: CatalogEntry) -> tuple[uuid.UUID, Any] | None:
        """Resolve an entry to its local ``(api_id, check_row)``: the API imported from it.

        ``None`` when no API with a current revision records the entry — the
        caller maps that to a 404.
        """
        current = await ApiRevisionRepository.current_revision_for_catalog_entry_id(
            session, entry.entry_id
        )
        if current is None:
            return None
        local_api_id, _revision_id = current
        check = await CatalogUpdateCheckRepository.get(session, local_api_id)
        return local_api_id, check
