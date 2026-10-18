"""Catalog source operations: list the configured sources, request syncs, take push notifications.

Nothing here syncs on the request path. ``:sync`` and ``:notify`` record a
request on the source's state row (``sync_due_at``, plus ``sync_forced_at`` for a
forced run) and wake this process's ``CatalogSourceSyncScanner``; the scanner
starts the run through the source's lease once the request is due and no run
holds the lease, so a request made mid-run is answered by a fresh run.

A source the caller cannot see answers exactly like one that does not exist,
and so does a source that cannot take the action (``:notify`` on a kind
without push support).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog

from jentic_one.registry.core.schema.catalog_sources import (
    SOURCE_STATUS_NEVER_SYNCED,
    CatalogSourceRow,
)
from jentic_one.registry.repos.catalog_entry_repo import CatalogSyncRunRepository
from jentic_one.registry.repos.catalog_source_repo import CatalogSourceRepository
from jentic_one.registry.services.catalog.snapshot import allowed_sources
from jentic_one.registry.services.catalog.sources.protocol import SourceCapabilities
from jentic_one.registry.services.catalog.sources.registry import resolve_catalog_source_kind
from jentic_one.registry.services.catalog.sources.scanner import wake_source_sync_scanner
from jentic_one.registry.services.catalog.sources.sync import CatalogSourceSync, SourceWarning
from jentic_one.registry.services.errors import (
    CatalogSourceNotFoundError,
    ForcedSyncForbiddenError,
)
from jentic_one.shared.auth.permission_catalog import ORG_ADMIN
from jentic_one.shared.config import CatalogSourceConfig, SourceReadersConfig
from jentic_one.shared.context import Context

logger = structlog.get_logger(__name__)

#: Most ``external_ids`` hints one ``:notify`` call may carry.
MAX_NOTIFY_EXTERNAL_IDS = 100

#: ``SourceSyncRequestView.status`` values.
SYNC_REQUEST_QUEUED = "queued"
SYNC_REQUEST_COALESCED = "coalesced"


@dataclass(frozen=True)
class CatalogSourceView:
    """One configured source as ``GET /catalog/sources`` shows it.

    The operator fields (``enabled`` to ``warnings``) are set only for an
    ``org:admin`` caller.
    """

    slug: str
    kind: str
    visibility: str
    status: str
    last_sync_at: datetime | None
    live_entries: int
    consecutive_failures: int
    sync_pending: bool
    enabled: bool | None = None
    trust: str | None = None
    precedence: int | None = None
    readers: SourceReadersConfig | None = None
    warnings: tuple[SourceWarning, ...] | None = field(default=None)


@dataclass(frozen=True)
class SourceSyncRequestView:
    """The outcome of a ``:sync`` or ``:notify`` request."""

    slug: str
    #: ``queued``: this call recorded the request; ``coalesced``: it joined one pending.
    status: str
    forced: bool
    #: Earliest start of the requested run (``None`` once the sync is already wanted).
    due_at: datetime | None


class CatalogSourceOps:
    """List sources and record sync requests; per-request, never syncs inline."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._sources: dict[str, CatalogSourceConfig] = {
            s.slug: s for s in ctx.config.catalog.sources
        }

    # ── list ───────────────────────────────────────────────────────────────────

    async def _rows(self) -> dict[str, CatalogSourceRow]:
        async with self._ctx.registry_db.session() as session:
            rows = {r.slug: r for r in await CatalogSourceRepository.list_all(session)}
        if any(slug not in rows for slug in self._sources):
            await CatalogSourceSync(self._ctx).register_sources()
            async with self._ctx.registry_db.session() as session:
                rows = {r.slug: r for r in await CatalogSourceRepository.list_all(session)}
        return rows

    async def list_sources(
        self, *, permissions: frozenset[str], actor_id: str | None
    ) -> list[CatalogSourceView]:
        """The sources the caller can see; every configured source for an ``org:admin``."""
        admin = ORG_ADMIN in permissions
        visible = allowed_sources(
            self._sources.values(), permissions=permissions, actor_id=actor_id
        )
        shown = [cfg for cfg in self._sources.values() if admin or cfg.slug in visible]
        rows = await self._rows()
        warnings = await CatalogSourceSync(self._ctx).source_warnings() if admin else {}
        return [
            _source_view(cfg, rows.get(cfg.slug), warnings.get(cfg.slug, []) if admin else None)
            for cfg in sorted(shown, key=lambda c: c.slug)
        ]

    # ── requests ──────────────────────────────────────────────────────────────

    def _visible_source(
        self, slug: str, *, permissions: frozenset[str], actor_id: str | None
    ) -> CatalogSourceConfig:
        cfg = self._sources.get(slug)
        if cfg is None or slug not in allowed_sources(
            [cfg], permissions=permissions, actor_id=actor_id
        ):
            raise CatalogSourceNotFoundError(slug)
        return cfg

    async def request_sync(
        self,
        slug: str,
        *,
        permissions: frozenset[str],
        actor_id: str | None,
        force: bool = False,
    ) -> SourceSyncRequestView:
        """Ask for a sync of ``slug`` now.

        ``force`` (``org:admin`` only) makes the run skip the mass-removal guard once.
        """
        if force and ORG_ADMIN not in permissions:
            raise ForcedSyncForbiddenError()
        self._visible_source(slug, permissions=permissions, actor_id=actor_id)
        await self._rows()
        async with self._ctx.registry_db.transaction() as session:
            before = await CatalogSourceRepository.get(session, slug)
            joined = before is not None and before.sync_due_at is not None
            await CatalogSourceRepository.request_sync(
                session, slug=slug, due_at=None, forced=force
            )
            due = await CatalogSourceRepository.sync_due_at(session, slug)
        logger.info("catalog_source_sync_requested", source=slug, forced=force, actor_id=actor_id)
        wake_source_sync_scanner(self._ctx)
        return SourceSyncRequestView(
            slug=slug,
            status=SYNC_REQUEST_COALESCED if joined else SYNC_REQUEST_QUEUED,
            forced=force,
            due_at=due,
        )

    async def notify(
        self,
        slug: str,
        *,
        permissions: frozenset[str],
        actor_id: str | None,
        external_ids: Sequence[str] = (),
    ) -> SourceSyncRequestView:
        """Take a push notification for ``slug``: single-flight, coalesced per source.

        A notification joins a sync that is already pending. Otherwise, within
        ``min_notify_interval_seconds`` of the last run's start it queues one
        sync due when that interval ends (every later notification joins it),
        else a sync due now. The run still lists the whole source, so a caller
        can never inject content; ``external_ids`` would only narrow a delta
        listing, and no kind takes them yet, so they are accepted and unused.
        """
        cfg = self._visible_source(slug, permissions=permissions, actor_id=actor_id)
        kind = resolve_catalog_source_kind(cfg.kind)
        if SourceCapabilities.PUSH_NOTIFY not in kind.capabilities:
            raise CatalogSourceNotFoundError(slug)
        await self._rows()
        async with self._ctx.registry_db.transaction() as session:
            row = await CatalogSourceRepository.get(session, slug)
            pending = row is not None and (
                row.sync_due_at is not None or row.sync_wanted_at is not None
            )
            if not pending:
                now = await CatalogSourceRepository.run_clock(session)
                last = await CatalogSyncRunRepository.last_started_at(session, slug)
                window_end = (
                    last + timedelta(seconds=cfg.min_notify_interval_seconds)
                    if last is not None
                    else None
                )
                due_at = window_end if window_end is not None and window_end > now else None
                await CatalogSourceRepository.request_sync(
                    session, slug=slug, due_at=due_at, forced=False
                )
            due = await CatalogSourceRepository.sync_due_at(session, slug)
        logger.info(
            "catalog_source_notified",
            source=slug,
            coalesced=pending,
            hints=len(external_ids),
            actor_id=actor_id,
        )
        if not pending:
            wake_source_sync_scanner(self._ctx)
        return SourceSyncRequestView(
            slug=slug,
            status=SYNC_REQUEST_COALESCED if pending else SYNC_REQUEST_QUEUED,
            forced=False,
            due_at=due,
        )


def _source_view(
    cfg: CatalogSourceConfig,
    row: CatalogSourceRow | None,
    warnings: Sequence[SourceWarning] | None,
) -> CatalogSourceView:
    admin = warnings is not None
    return CatalogSourceView(
        slug=cfg.slug,
        kind=cfg.kind,
        visibility=cfg.visibility,
        status=row.last_status if row is not None else SOURCE_STATUS_NEVER_SYNCED,
        last_sync_at=row.last_sync_at if row is not None else None,
        live_entries=row.live_entries if row is not None else 0,
        consecutive_failures=row.consecutive_failures if row is not None else 0,
        sync_pending=row is not None
        and (row.sync_wanted_at is not None or row.sync_due_at is not None),
        enabled=cfg.enabled if admin else None,
        trust=cfg.trust if admin else None,
        precedence=cfg.precedence if admin else None,
        readers=cfg.readers if admin else None,
        warnings=tuple(warnings) if warnings is not None else None,
    )
