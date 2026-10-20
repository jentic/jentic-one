"""Repository for ``catalog_sources``: per-source sync state, the sync lease and sync requests.

Flush-only, never commits. The lease (``sync_lease_owner`` / ``sync_lease_until``)
is a conditional ``UPDATE`` so exactly one process wins a claim; on Postgres the
expiry is computed from the database clock so replicas with skewed clocks agree.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, case, func, literal, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.catalog_sources import CatalogSourceRow
from jentic_one.shared.db.utils import utcnow


def _is_postgres(session: AsyncSession) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


def _now(session: AsyncSession) -> Any:
    return func.now() if _is_postgres(session) else utcnow()


def _typed_time(value: Any) -> Any:
    """``value`` as a SQL expression of the timestamp column type (a Python time is bound)."""
    if isinstance(value, datetime):
        return literal(value, type_=CatalogSourceRow.__table__.c.sync_due_at.type)
    return value


def _now_plus(session: AsyncSession, seconds: int) -> Any:
    if _is_postgres(session):
        return func.now() + func.make_interval(0, 0, 0, 0, 0, 0, seconds)
    return utcnow() + timedelta(seconds=seconds)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


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
    async def run_clock(session: AsyncSession) -> datetime:
        """The start time of a sync run, from one clock every replica agrees on.

        Postgres answers ``clock_timestamp()`` (the wall clock now, not the
        transaction's start); SQLite, which has one process, uses the Python clock.
        """
        if not _is_postgres(session):
            return utcnow()
        value: datetime = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

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
        """Create the state row for a configured source, or refresh its config columns.

        ``INSERT … ON CONFLICT (slug) DO NOTHING`` first, so two replicas booting
        at once never race on the unique slug; then the config columns are set.
        """
        insert = pg_insert if _is_postgres(session) else sqlite_insert
        await session.execute(
            insert(CatalogSourceRow)
            .values(
                slug=slug,
                kind=kind,
                config_digest=config_digest,
                enabled=enabled,
                visibility=visibility,
                trust=trust,
                precedence=precedence,
            )
            .on_conflict_do_nothing(index_elements=["slug"])
        )
        await session.execute(
            update(CatalogSourceRow)
            .where(CatalogSourceRow.slug == slug)
            .values(
                kind=kind,
                config_digest=config_digest,
                enabled=enabled,
                visibility=visibility,
                trust=trust,
                precedence=precedence,
            )
            .execution_options(synchronize_session=False)
        )
        result = await session.execute(
            select(CatalogSourceRow)
            .where(CatalogSourceRow.slug == slug)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one()

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
    async def release_lease(session: AsyncSession, *, slug: str, owner: str) -> bool:
        """Clear ``owner``'s lease; ``False`` (and a no-op) when someone else holds it."""
        result = await session.execute(
            update(CatalogSourceRow)
            .where(CatalogSourceRow.slug == slug, CatalogSourceRow.sync_lease_owner == owner)
            .values(sync_lease_owner=None, sync_lease_until=None)
            .execution_options(synchronize_session=False)
        )
        return bool(getattr(result, "rowcount", 0))

    @staticmethod
    async def clear_expired_leases(session: AsyncSession, *, expired_before: datetime) -> int:
        """Drop every lease whose expiry is before ``expired_before``.

        ``expired_before`` comes from :meth:`run_clock`, so it is on the same
        clock the lease expiry was written with. Returns the number cleared.
        """
        result = await session.execute(
            update(CatalogSourceRow)
            .where(
                CatalogSourceRow.sync_lease_owner.is_not(None),
                CatalogSourceRow.sync_lease_until < expired_before,
            )
            .values(sync_lease_owner=None, sync_lease_until=None)
            .execution_options(synchronize_session=False)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    @staticmethod
    async def lock_for_apply(session: AsyncSession, slug: str) -> None:
        """Serialise apply and re-resolve transactions for one source (Postgres).

        Takes a transaction-scoped advisory lock keyed on the slug. SQLite has a
        single process, so the caller serialises with an in-process lock instead.
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
    async def request_sync(
        session: AsyncSession, *, slug: str, due_at: datetime | None, forced: bool
    ) -> None:
        """Record a requested sync of ``slug``, startable once ``due_at`` passes.

        ``due_at=None`` means now. An earlier pending due time is kept, so a
        request never postpones one already queued. ``forced`` marks the next
        run as skipping the mass-removal guard.
        """
        now = _now(session)
        due = _typed_time(now if due_at is None else due_at)
        values: dict[str, Any] = {
            "sync_due_at": case(
                (CatalogSourceRow.sync_due_at.is_(None), due),
                (CatalogSourceRow.sync_due_at > due, due),
                else_=CatalogSourceRow.sync_due_at,
            )
        }
        if forced:
            values["sync_forced_at"] = now
        await session.execute(
            update(CatalogSourceRow)
            .where(CatalogSourceRow.slug == slug)
            .values(**values)
            .execution_options(synchronize_session=False)
        )

    @staticmethod
    async def sync_due_at(session: AsyncSession, slug: str) -> datetime | None:
        """The source's pending requested-sync time, read from the database."""
        result = await session.execute(
            select(CatalogSourceRow.sync_due_at).where(CatalogSourceRow.slug == slug)
        )
        value: datetime | None = result.scalar_one_or_none()
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value

    @staticmethod
    async def next_sync_due_at(session: AsyncSession) -> datetime | None:
        """The earliest time a pending requested sync can start across sources.

        A request on a source whose lease a run still holds cannot start before
        the lease ends, so it counts from the lease's expiry, not its own due
        time (the scanner then sleeps instead of re-polling a held lease).
        """
        result = await session.execute(
            select(CatalogSourceRow.sync_due_at, CatalogSourceRow.sync_lease_until).where(
                CatalogSourceRow.sync_due_at.is_not(None)
            )
        )
        earliest: datetime | None = None
        for due, lease_until in result.all():
            start = _aware(due)
            if lease_until is not None:
                start = max(start, _aware(lease_until))
            earliest = start if earliest is None else min(earliest, start)
        return earliest

    @staticmethod
    async def promote_due_syncs(session: AsyncSession) -> list[str]:
        """Turn every requested sync that is due, on a source no run holds, into a wanted sync.

        A request made while a run is in progress waits for that run to end, so
        it is answered by a run that starts after it.
        """
        now = _now(session)
        due = (
            select(CatalogSourceRow.slug)
            .where(
                CatalogSourceRow.sync_due_at.is_not(None),
                CatalogSourceRow.sync_due_at <= now,
                CatalogSourceRepository._lease_free(session),
            )
            .order_by(CatalogSourceRow.slug)
        )
        slugs = list((await session.execute(due)).scalars().all())
        if not slugs:
            return []
        await session.execute(
            update(CatalogSourceRow)
            .where(
                CatalogSourceRow.slug.in_(slugs),
                CatalogSourceRow.sync_due_at <= now,
                CatalogSourceRepository._lease_free(session),
            )
            .values(
                sync_wanted_at=func.coalesce(CatalogSourceRow.sync_wanted_at, _typed_time(now)),
                sync_due_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        return slugs

    @staticmethod
    async def take_forced(session: AsyncSession, slug: str) -> bool:
        """Consume a pending forced-sync request; ``True`` when there was one.

        Called by the run that has just claimed the lease, in the claim's
        transaction, so exactly one run is forced per request.
        """
        result = await session.execute(
            update(CatalogSourceRow)
            .where(CatalogSourceRow.slug == slug, CatalogSourceRow.sync_forced_at.is_not(None))
            .values(sync_forced_at=None)
            .execution_options(synchronize_session=False)
        )
        return bool(getattr(result, "rowcount", 0))

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
    async def record_failure(session: AsyncSession, *, slug: str, status: str) -> None:
        """A run ended without applying: bump the failure count, keep the rows.

        Never touches the lease; the caller releases it with ``release_lease``.
        """
        await session.execute(
            update(CatalogSourceRow)
            .where(CatalogSourceRow.slug == slug)
            .values(
                last_status=status,
                consecutive_failures=CatalogSourceRow.consecutive_failures + 1,
                sync_wanted_at=None,
            )
            .execution_options(synchronize_session=False)
        )

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
    ) -> None:
        """A run applied: store its digests and clear the wanted flag.

        Never touches the lease; the caller releases it with ``release_lease``.
        """
        row.last_status = "ok"
        row.consecutive_failures = 0
        row.last_sync_at = synced_at
        row.last_applied_run_id = run_id
        row.watermark = watermark
        row.content_digest = content_digest
        row.listing_digest = listing_digest
        row.live_entries = live_entries
        row.sync_wanted_at = None
        await session.flush()
