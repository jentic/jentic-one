"""One-shot boot reconcile of the legacy catalog source from the last snapshot blob.

Installs that predate catalog sources have a ``catalog_snapshots`` blob and
imported APIs with no ``catalog_entry_id``. At boot, under the legacy source's
sync lease (so one replica runs it and the others skip), the reconcile:

1. seeds ``catalog_entries`` for ``catalog.legacy_source_slug`` from the blob,
   through the normal staged sync (sanitising, resolving apply), when that
   source has never applied a sync; no network is needed;
2. sets ``apis.catalog_entry_id`` to ``{legacy_source_slug}/{catalog_api_id}``
   where ``catalog_api_id`` is set and that entry is live;
3. for rows with no ``catalog_api_id``, matches a revision's ``source_url``
   against the blob's ``spec_url`` values and takes the matching entry.

It only touches rows whose ``catalog_entry_id`` is null, so a second boot is a
no-op. Imported rows carrying a ``catalog_api_id`` that matches no live entry are
counted and logged.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import structlog

from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.catalog_entry_repo import CatalogEntryRepository
from jentic_one.registry.repos.catalog_repo import CatalogRepository
from jentic_one.registry.repos.catalog_source_repo import CatalogSourceRepository
from jentic_one.registry.services.catalog import manifest_builder as mb
from jentic_one.registry.services.catalog.entries import make_entry_id
from jentic_one.registry.services.catalog.sources.git_manifest import (
    changes_watermark,
    manifest_entry_to_source_entry,
)
from jentic_one.registry.services.catalog.sources.protocol import EntryChange, Upserted
from jentic_one.registry.services.catalog.sources.streams import FullListStream
from jentic_one.registry.services.catalog.sources.sync import CatalogSourceSync
from jentic_one.shared.db.ids import generate_ksuid

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    """What one boot reconcile did."""

    #: ``skipped`` (no legacy source / another replica holds the lease) or ``ran``.
    status: str
    seeded: int = 0
    backfilled: int = 0
    unmapped: int = 0


def snapshot_changes(entries: Sequence[dict[str, Any]]) -> list[EntryChange]:
    """The listing a snapshot blob's entries stand for."""
    changes: list[EntryChange] = []
    for raw in entries:
        if not isinstance(raw, dict) or not raw.get("api_id"):
            continue
        changes.append(Upserted(manifest_entry_to_source_entry(mb.ManifestEntry.from_dict(raw))))
    return changes


async def reconcile_legacy_source(sync: CatalogSourceSync) -> ReconcileOutcome:
    """Seed the legacy source from the last snapshot blob and backfill ``catalog_entry_id``.

    The caller has registered the source rows (``CatalogSourceSync.register_sources``).
    """
    ctx = sync.ctx
    slug = ctx.config.catalog.legacy_source_slug
    cfg = sync.sources.get(slug)
    if cfg is None:
        logger.info("catalog_reconcile_skipped", reason="no_legacy_source", source=slug)
        return ReconcileOutcome(status="skipped")

    owner = generate_ksuid("csyn")
    async with ctx.registry_db.transaction() as session:
        claimed = await CatalogSourceRepository.claim_lease(
            session, slug=slug, owner=owner, lease_seconds=cfg.sync_lease_seconds
        )
        if not claimed:
            logger.info("catalog_reconcile_skipped", reason="lease_held", source=slug)
            return ReconcileOutcome(status="skipped")
        source = await CatalogSourceRepository.get(session, slug)
        needs_seed = source is not None and source.last_applied_run_id is None
        blob = await CatalogRepository.entries(session)
        unmapped_before = await ApiRepository.count_unmapped(session)

    seeded = 0
    if needs_seed and blob:
        changes = snapshot_changes(blob)

        async def _load() -> list[EntryChange]:
            return changes

        stream = FullListStream(_load, watermark=changes_watermark)
        outcome = await sync.run_claimed(cfg, stream, run_id=owner)
        seeded = outcome.upserted
        logger.info("catalog_reconcile_seeded", source=slug, status=outcome.status, seeded=seeded)
    else:
        async with ctx.registry_db.transaction() as session:
            await CatalogSourceRepository.release_lease(session, slug=slug, owner=owner)

    if unmapped_before == 0:
        return ReconcileOutcome(status="ran", seeded=seeded)

    backfilled, unmapped = await _backfill_entry_ids(sync, slug=slug, blob=blob)
    if unmapped:
        logger.warning("catalog_reconcile_unmapped", source=slug, unmapped=unmapped)
    logger.info("catalog_reconcile_backfilled", source=slug, backfilled=backfilled)
    return ReconcileOutcome(status="ran", seeded=seeded, backfilled=backfilled, unmapped=unmapped)


async def _backfill_entry_ids(
    sync: CatalogSourceSync, *, slug: str, blob: Sequence[dict[str, Any]]
) -> tuple[int, int]:
    by_spec_url: dict[str, str] = {}
    for raw in blob:
        if isinstance(raw, dict) and raw.get("spec_url") and raw.get("api_id"):
            by_spec_url.setdefault(str(raw["spec_url"]), str(raw["api_id"]))
    backfilled = 0
    catalog_unmapped = 0
    async with sync.ctx.registry_db.transaction() as session:
        by_catalog_id = await ApiRepository.unmapped_catalog_ids(session)
        candidates = {make_entry_id(slug, cat_id) for _, cat_id in by_catalog_id}
        candidates.update(make_entry_id(slug, api_id) for api_id in by_spec_url.values())
        live = await CatalogEntryRepository.live_entry_ids(session, candidates)
        for api_pk, cat_id in by_catalog_id:
            entry_id = make_entry_id(slug, cat_id)
            if entry_id in live and await ApiRepository.set_catalog_entry_id_if_unset(
                session, api_pk, entry_id
            ):
                backfilled += 1
            else:
                catalog_unmapped += 1
        for api_pk, url in await ApiRepository.unmapped_source_urls(session):
            api_id = by_spec_url.get(url)
            if api_id is None:
                continue
            entry_id = make_entry_id(slug, api_id)
            if entry_id in live and await ApiRepository.set_catalog_entry_id_if_unset(
                session, api_pk, entry_id
            ):
                backfilled += 1
    return backfilled, catalog_unmapped
