"""Repositories for ``catalog_entries``, ``catalog_staged_entries`` and ``catalog_sync_runs``.

Flush-only, never commit: the sync service owns transaction boundaries (one
transaction per staging batch, one for the apply).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.catalog_entries import CatalogEntryRow
from jentic_one.registry.core.schema.catalog_staged_entries import CatalogStagedEntry
from jentic_one.registry.core.schema.catalog_sync_runs import CatalogSyncRun


class CatalogEntryRepository:
    """Data access for catalog entries — flush-only, never commits."""

    @staticmethod
    async def rows_for_source(
        session: AsyncSession, slug: str, *, live_only: bool = False
    ) -> list[CatalogEntryRow]:
        stmt = select(CatalogEntryRow).where(CatalogEntryRow.source_slug == slug)
        if live_only:
            stmt = stmt.where(CatalogEntryRow.removed_at.is_(None))
        result = await session.execute(stmt.order_by(CatalogEntryRow.entry_id))
        return list(result.scalars().all())

    @staticmethod
    async def live_rows(session: AsyncSession, slugs: Iterable[str]) -> list[CatalogEntryRow]:
        """Every live row of the given sources, in ``entry_id`` order."""
        wanted = list(slugs)
        if not wanted:
            return []
        result = await session.execute(
            select(CatalogEntryRow)
            .where(CatalogEntryRow.source_slug.in_(wanted), CatalogEntryRow.removed_at.is_(None))
            .order_by(CatalogEntryRow.entry_id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def live_external_ids(session: AsyncSession, slug: str) -> set[str]:
        result = await session.execute(
            select(CatalogEntryRow.external_id).where(
                CatalogEntryRow.source_slug == slug, CatalogEntryRow.removed_at.is_(None)
            )
        )
        return set(result.scalars().all())

    @staticmethod
    async def live_catalog_api_ids(session: AsyncSession, slugs: Iterable[str]) -> frozenset[str]:
        """The live ``catalog_api_id`` values of the given sources."""
        wanted = list(slugs)
        if not wanted:
            return frozenset()
        result = await session.execute(
            select(CatalogEntryRow.catalog_api_id).where(
                CatalogEntryRow.source_slug.in_(wanted), CatalogEntryRow.removed_at.is_(None)
            )
        )
        return frozenset(result.scalars().all())

    @staticmethod
    async def live_entry_ids(session: AsyncSession, entry_ids: Iterable[str]) -> set[str]:
        """Which of ``entry_ids`` exist and are live."""
        wanted = list(entry_ids)
        found: set[str] = set()
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            result = await session.execute(
                select(CatalogEntryRow.entry_id).where(
                    CatalogEntryRow.entry_id.in_(chunk), CatalogEntryRow.removed_at.is_(None)
                )
            )
            found.update(result.scalars().all())
        return found

    @staticmethod
    async def live_entry(session: AsyncSession, entry_id: str) -> CatalogEntryRow | None:
        """The live row ``entry_id``, or ``None`` when it is missing or tombstoned."""
        result = await session.execute(
            select(CatalogEntryRow).where(
                CatalogEntryRow.entry_id == entry_id, CatalogEntryRow.removed_at.is_(None)
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def live_entries(
        session: AsyncSession, entry_ids: Iterable[str]
    ) -> dict[str, CatalogEntryRow]:
        """The live rows among ``entry_ids``, keyed by ``entry_id``."""
        wanted = list(entry_ids)
        found: dict[str, CatalogEntryRow] = {}
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            result = await session.execute(
                select(CatalogEntryRow).where(
                    CatalogEntryRow.entry_id.in_(chunk), CatalogEntryRow.removed_at.is_(None)
                )
            )
            found.update((row.entry_id, row) for row in result.scalars().all())
        return found

    @staticmethod
    def add(session: AsyncSession, row: CatalogEntryRow) -> None:
        session.add(row)

    @staticmethod
    async def tombstone_unseen(
        session: AsyncSession, *, slug: str, seen_before: datetime, now: datetime
    ) -> int:
        """Tombstone live rows not seen by the run that started at ``seen_before``."""
        result = await session.execute(
            update(CatalogEntryRow)
            .where(
                CatalogEntryRow.source_slug == slug,
                CatalogEntryRow.removed_at.is_(None),
                CatalogEntryRow.seen_at < seen_before,
            )
            .values(removed_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    @staticmethod
    async def flush(session: AsyncSession) -> None:
        await session.flush()


class CatalogSyncRunRepository:
    """Data access for sync runs and their staged changes — flush-only."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        run_id: str,
        slug: str,
        started_at: datetime,
        forced: bool,
    ) -> CatalogSyncRun:
        run = CatalogSyncRun(id=run_id, source_slug=slug, started_at=started_at, forced=forced)
        session.add(run)
        await session.flush()
        return run

    @staticmethod
    async def get(session: AsyncSession, run_id: str) -> CatalogSyncRun | None:
        return await session.get(CatalogSyncRun, run_id)

    @staticmethod
    async def finish(session: AsyncSession, run_id: str, **values: Any) -> None:
        await session.execute(
            update(CatalogSyncRun)
            .where(CatalogSyncRun.id == run_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )

    @staticmethod
    async def stage(session: AsyncSession, rows: Sequence[CatalogStagedEntry]) -> None:
        session.add_all(list(rows))
        await session.flush()

    @staticmethod
    async def staged_rows(session: AsyncSession, run_id: str) -> list[CatalogStagedEntry]:
        result = await session.execute(
            select(CatalogStagedEntry)
            .where(CatalogStagedEntry.sync_run_id == run_id)
            .order_by(CatalogStagedEntry.external_id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def delete_staged(session: AsyncSession, run_id: str) -> None:
        await session.execute(
            delete(CatalogStagedEntry).where(CatalogStagedEntry.sync_run_id == run_id)
        )

    @staticmethod
    async def sweep_staged(session: AsyncSession, *, older_than: datetime) -> int:
        """Delete staged rows of runs that never applied (abandoned staging)."""
        result = await session.execute(
            delete(CatalogStagedEntry).where(CatalogStagedEntry.created_at < older_than)
        )
        return int(getattr(result, "rowcount", 0) or 0)
