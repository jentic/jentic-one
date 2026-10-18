"""Repositories for ``catalog_entries``, ``catalog_staged_entries`` and ``catalog_sync_runs``.

Flush-only, never commit: the sync service owns transaction boundaries (one
transaction per staging batch, one for the apply).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.catalog_entries import CatalogEntryRow
from jentic_one.registry.core.schema.catalog_sources import CatalogSourceRow
from jentic_one.registry.core.schema.catalog_staged_entries import CatalogStagedEntry
from jentic_one.registry.core.schema.catalog_sync_runs import (
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    CatalogSyncRun,
)

#: ``error_code`` of a run closed because its process died before finishing it.
ERROR_ABANDONED = "abandoned"


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
    async def last_started_at(session: AsyncSession, slug: str) -> datetime | None:
        """When the source's most recent run (finished or not) started."""
        result = await session.execute(
            select(func.max(CatalogSyncRun.started_at)).where(CatalogSyncRun.source_slug == slug)
        )
        value: datetime | None = result.scalar_one_or_none()
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value

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
        """Delete staged rows older than ``older_than`` whose run no longer holds a lease.

        A run that still owns its source's lease can still apply, and a full
        listing's apply treats every unstaged live row as removed, so its staged
        rows are never touched however long it has been staging. Leases that
        expired long ago are cleared first (see
        ``CatalogSourceRepository.clear_expired_leases``), so a dead run whose
        source is never synced again does not keep its rows.
        """
        result = await session.execute(
            delete(CatalogStagedEntry).where(
                CatalogStagedEntry.created_at < older_than,
                CatalogStagedEntry.sync_run_id.not_in(_lease_owners()),
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)

    @staticmethod
    async def close_abandoned(session: AsyncSession, *, older_than: datetime, now: datetime) -> int:
        """End ``running`` runs that started before ``older_than`` and hold no lease.

        A live run owns its source's lease from before its row is created until
        the transaction that finishes it, so a ``running`` row without the lease
        belongs to a process that died mid-run.
        """
        result = await session.execute(
            update(CatalogSyncRun)
            .where(
                CatalogSyncRun.status == RUN_STATUS_RUNNING,
                CatalogSyncRun.started_at < older_than,
                CatalogSyncRun.id.not_in(_lease_owners()),
            )
            .values(status=RUN_STATUS_FAILED, error_code=ERROR_ABANDONED, finished_at=now)
            .execution_options(synchronize_session=False)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    @staticmethod
    async def prune_finished(session: AsyncSession, *, finished_before: datetime) -> int:
        """Delete finished runs older than ``finished_before``.

        A source's last applied run is kept: the apply compares a new run's start
        time against it to drop superseded runs.
        """
        last_applied = select(CatalogSourceRow.last_applied_run_id).where(
            CatalogSourceRow.last_applied_run_id.is_not(None)
        )
        result = await session.execute(
            delete(CatalogSyncRun).where(
                CatalogSyncRun.finished_at.is_not(None),
                CatalogSyncRun.finished_at < finished_before,
                CatalogSyncRun.id.not_in(last_applied),
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)


def _lease_owners() -> Any:
    """The run ids currently holding a source's sync lease."""
    return select(CatalogSourceRow.sync_lease_owner).where(
        CatalogSourceRow.sync_lease_owner.is_not(None)
    )
