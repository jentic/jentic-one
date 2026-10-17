"""Repository for ``catalog_sources``: per-source sync state and the sync lease.

Flush-only, never commits. The lease (``sync_lease_owner`` / ``sync_lease_until``)
is a conditional ``UPDATE`` so exactly one process wins a claim; on Postgres the
expiry is computed from the database clock so replicas with skewed clocks agree.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.catalog_sources import CatalogSourceRow
from jentic_one.shared.db.utils import utcnow


def _is_postgres(session: AsyncSession) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


def _now(session: AsyncSession) -> Any:
    return func.now() if _is_postgres(session) else utcnow()


def _now_plus(session: AsyncSession, seconds: int) -> Any:
    if _is_postgres(session):
        return func.now() + func.make_interval(0, 0, 0, 0, 0, 0, seconds)
    return utcnow() + timedelta(seconds=seconds)


class CatalogSourceRepository:
    """Data access for catalog source state — flush-only, never commits."""

    @staticmethod
    async def get(session: AsyncSession, slug: str) -> CatalogSourceRow | None:
        result = await session.execute(
            select(CatalogSourceRow).where(CatalogSourceRow.slug == slug)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(session: AsyncSession) -> list[CatalogSourceRow]:
        result = await session.execute(select(CatalogSourceRow).order_by(CatalogSourceRow.slug))
        return list(result.scalars().all())

    @staticmethod
    async def upsert_config(
        session: AsyncSession,
        *,
        slug: str,
        kind: str,
        config_digest: str,
        enabled: bool,
        visibility: str,
        trust: str,
        precedence: int,
    ) -> CatalogSourceRow:
        """Create the state row for a configured source, or refresh its config columns."""
        row = await CatalogSourceRepository.get(session, slug)
        if row is None:
            row = CatalogSourceRow(
                slug=slug,
                kind=kind,
                config_digest=config_digest,
                enabled=enabled,
                visibility=visibility,
                trust=trust,
                precedence=precedence,
            )
            session.add(row)
        else:
            row.kind = kind
            row.config_digest = config_digest
            row.enabled = enabled
            row.visibility = visibility
            row.trust = trust
            row.precedence = precedence
        await session.flush()
        return row

    @staticmethod
    def _lease_free(session: AsyncSession) -> ColumnElement[bool]:
        return or_(
            CatalogSourceRow.sync_lease_until.is_(None),
            CatalogSourceRow.sync_lease_until < _now(session),
        )

    @staticmethod
    async def claim_lease(
        session: AsyncSession, *, slug: str, owner: str, lease_seconds: int
    ) -> bool:
        """Claim the source's sync lease for ``owner``; ``False`` when another holds it."""
        result = await session.execute(
            update(CatalogSourceRow)
            .where(CatalogSourceRow.slug == slug, CatalogSourceRepository._lease_free(session))
            .values(sync_lease_owner=owner, sync_lease_until=_now_plus(session, lease_seconds))
            .execution_options(synchronize_session=False)
        )
        return bool(getattr(result, "rowcount", 0))

    @staticmethod
    async def renew_lease(
        session: AsyncSession, *, slug: str, owner: str, lease_seconds: int
    ) -> bool:
        """Extend ``owner``'s lease; ``False`` when ``owner`` no longer holds it."""
        result = await session.execute(
            update(CatalogSourceRow)
            .where(CatalogSourceRow.slug == slug, CatalogSourceRow.sync_lease_owner == owner)
            .values(sync_lease_until=_now_plus(session, lease_seconds))
            .execution_options(synchronize_session=False)
        )
        return bool(getattr(result, "rowcount", 0))

    @staticmethod
    async def release_lease(session: AsyncSession, *, slug: str, owner: str) -> None:
        """Clear ``owner``'s lease (a no-op when someone else holds it)."""
        await session.execute(
            update(CatalogSourceRow)
            .where(CatalogSourceRow.slug == slug, CatalogSourceRow.sync_lease_owner == owner)
            .values(sync_lease_owner=None, sync_lease_until=None)
            .execution_options(synchronize_session=False)
        )

    @staticmethod
    async def lock_for_apply(session: AsyncSession, slug: str) -> None:
        """Serialise apply transactions for one source (transaction-scoped).

        Postgres takes an advisory lock keyed on the slug; SQLite serialises
        writers on its own.
        """
        if _is_postgres(session):
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"catalog_source:{slug}"},
            )

    @staticmethod
    async def mark_sync_wanted(session: AsyncSession, slugs: Iterable[str]) -> int:
        """Set ``sync_wanted_at`` where it is not already set."""
        wanted = list(slugs)
        if not wanted:
            return 0
        result = await session.execute(
            update(CatalogSourceRow)
            .where(CatalogSourceRow.slug.in_(wanted), CatalogSourceRow.sync_wanted_at.is_(None))
            .values(sync_wanted_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    @staticmethod
    async def mark_reresolve_wanted(session: AsyncSession, slugs: Iterable[str]) -> int:
        """Ask the scanner to re-resolve these sources' stored rows."""
        wanted = list(slugs)
        if not wanted:
            return 0
        result = await session.execute(
            update(CatalogSourceRow)
            .where(CatalogSourceRow.slug.in_(wanted))
            .values(reresolve_wanted_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    @staticmethod
    async def record_failure(session: AsyncSession, *, slug: str, status: str, owner: str) -> None:
        """A run ended without applying: bump the failure count, keep the rows."""
        row = await CatalogSourceRepository.get(session, slug)
        if row is None:
            return
        row.last_status = status
        row.consecutive_failures = row.consecutive_failures + 1
        if row.sync_lease_owner == owner:
            row.sync_lease_owner = None
            row.sync_lease_until = None
        await session.flush()

    @staticmethod
    async def record_applied(
        session: AsyncSession,
        *,
        row: CatalogSourceRow,
        run_id: str,
        synced_at: datetime,
        watermark: str | None,
        content_digest: str,
        listing_digest: str,
        live_entries: int,
        owner: str,
    ) -> None:
        """A run applied: store its digests and clear the lease and the wanted flag."""
        row.last_status = "ok"
        row.consecutive_failures = 0
        row.last_sync_at = synced_at
        row.last_applied_run_id = run_id
        row.watermark = watermark
        row.content_digest = content_digest
        row.listing_digest = listing_digest
        row.live_entries = live_entries
        row.sync_wanted_at = None
        if row.sync_lease_owner == owner:
            row.sync_lease_owner = None
            row.sync_lease_until = None
        await session.flush()
