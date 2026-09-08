"""Repository for the catalog snapshot — single-blob data access for the cache.

The catalog is cached as **one** ``catalog_snapshots`` row holding the whole parsed
manifest (see ``CatalogSnapshot``). This repository owns:

- ``replace`` — upsert the single fixed-id snapshot row with a freshly fetched one;
- ``current`` / ``fetched_at`` — read the snapshot blob and its explicit freshness;
- ``registered_spec_urls`` — the reliable coverage key for "is this catalog entry
  already imported?": the set of ``source_url``s recorded on local API revisions
  (any non-archived revision, including un-promoted drafts).
  A catalog entry is registered iff its ``spec_url`` was the source of such a
  revision. (Vendor/domain guessing is intentionally avoided — jentic-one's local
  identity is the slugified spec triple, which does not map back to a catalog
  domain id, so a URL match is the only reliable join.)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.catalog_snapshots import SINGLETON_ID, CatalogSnapshot
from jentic_one.shared.db.utils import utcnow


class CatalogRepository:
    """Data access for the catalog snapshot — flush-only, never commits."""

    @staticmethod
    async def try_acquire_refresh_lock(session: AsyncSession) -> bool:
        """Attempt a transaction-scoped advisory lock for single-flight refresh.

        Returns True if this session acquired the lock (caller should refresh),
        False if another transaction already holds it (caller should skip).
        On SQLite: always returns True (no advisory lock support).
        """
        dialect = session.bind.dialect.name if session.bind else "sqlite"
        if dialect != "postgresql":
            return True
        result = await session.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext('catalog_refresh'))")
        )
        return bool(result.scalar())

    @staticmethod
    async def replace(
        session: AsyncSession,
        *,
        source_url: str,
        entries: list[dict[str, Any]],
    ) -> int:
        """Upsert the single fixed-id snapshot row with a freshly fetched one.

        The table is structurally single-row (fixed ``SINGLETON_ID`` primary key),
        so this updates the existing row in place when present and inserts it
        otherwise — within the caller's transaction. A concurrent refresh racing
        to insert collides on the primary key rather than leaving a duplicate.
        Returns the entry count.
        """
        snapshot = await session.get(CatalogSnapshot, SINGLETON_ID)
        if snapshot is None:
            session.add(
                CatalogSnapshot(
                    id=SINGLETON_ID,
                    source_url=source_url,
                    entry_count=len(entries),
                    entries=entries,
                )
            )
        else:
            snapshot.source_url = source_url
            snapshot.entry_count = len(entries)
            snapshot.entries = entries
            snapshot.created_at = utcnow()
        await session.flush()
        return len(entries)

    @staticmethod
    async def current(session: AsyncSession) -> CatalogSnapshot | None:
        """Return the current snapshot, or ``None`` when the cache is empty."""
        return await session.get(CatalogSnapshot, SINGLETON_ID)

    @staticmethod
    async def entries(session: AsyncSession) -> list[dict[str, Any]]:
        """Return the current snapshot's entry list (empty when no snapshot)."""
        snapshot = await CatalogRepository.current(session)
        return list(snapshot.entries) if snapshot is not None else []

    @staticmethod
    async def manifest_spec_urls(session: AsyncSession) -> set[str]:
        """Spec URLs advertised by the current catalog manifest snapshot.

        Distinct from :meth:`registered_spec_urls` (which is the *local* coverage key —
        source URLs of imported revisions). This is the set of ``spec_url`` the upstream
        catalog *offers*, used by the update-notify scanner to decide whether an
        overlay-origin or manual revision is still catalog-tracked (its ``source_url``
        appears in the manifest). Empty when the cache is empty.
        """
        snapshot = await CatalogRepository.current(session)
        if snapshot is None:
            return set()
        urls: set[str] = set()
        for entry in snapshot.entries:
            url = entry.get("spec_url") if isinstance(entry, dict) else None
            if url:
                urls.add(url)
        return urls

    @staticmethod
    async def fetched_at(session: AsyncSession) -> datetime | None:
        """Explicit freshness of the current snapshot (``None`` when empty).

        Backed by ``created_at`` — the row is rewritten at fetch time, so creation
        time *is* the manifest fetch time.
        """
        result = await session.execute(
            select(CatalogSnapshot.created_at).where(CatalogSnapshot.id == SINGLETON_ID)
        )
        return result.scalars().first()

    @staticmethod
    async def registered_spec_urls(session: AsyncSession) -> set[str]:
        """Source URLs of any non-archived local revision — the coverage key.

        A catalog entry is "registered" iff its ``spec_url`` matches one of these.
        We deliberately count *any* non-archived revision, not just the current
        (published) one: importing a catalog entry creates a ``draft`` revision and
        does **not** promote it to current (promotion is a separate manual step).
        Keying on the current revision alone would leave every freshly imported
        entry showing ``registered=false`` until someone promotes it — which is
        both wrong and inconsistent with ``GET /apis``, where un-promoted drafts
        already appear. Archived revisions are excluded so a deleted/superseded
        import doesn't keep masking a catalog entry forever.
        """
        result = await session.execute(
            select(ApiRevision.source_url)
            .where(ApiRevision.source_url.is_not(None))
            .where(ApiRevision.state != "archived")
        )
        return {url for (url,) in result.all() if url}
