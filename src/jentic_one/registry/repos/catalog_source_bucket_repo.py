"""Repository for ``catalog_source_buckets`` — the shared per-source rate bucket.

Flush-only, never commits; the caller owns the transaction. A take is
:meth:`lock` (``SELECT … FOR UPDATE`` on the source's row) followed by
:meth:`write` of the refilled level in the same transaction, so concurrent
takers across processes serialise on the row and the rate holds. The bucket
arithmetic lives with the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.catalog_source_buckets import CatalogSourceBucket
from jentic_one.shared.db.ids import generate_ksuid


@dataclass(frozen=True, slots=True)
class BucketState:
    """A locked bucket row's state."""

    tokens: float
    refilled_at: datetime
    blocked_until: datetime | None


class CatalogSourceBucketRepository:
    """Data access for the per-source rate bucket rows."""

    @staticmethod
    async def ensure(session: AsyncSession, slug: str, *, burst: int, now: datetime) -> None:
        """Create ``slug``'s row with a full bucket when it does not exist yet.

        ``INSERT … ON CONFLICT DO NOTHING``, so concurrent first takers and the
        registry's boot-time ``ensure`` never race. Only the registry role
        inserts; a role with ``SELECT, UPDATE`` alone uses the row the registry
        created.
        """
        stmt = (
            pg_insert(CatalogSourceBucket)
            .values(
                id=generate_ksuid("cbkt"),
                source_slug=slug,
                tokens=float(burst),
                refilled_at=now,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["source_slug"])
        )
        await session.execute(stmt)

    @staticmethod
    async def lock(session: AsyncSession, slug: str) -> BucketState | None:
        """Lock ``slug``'s row for this transaction; ``None`` when it does not exist."""
        row = (
            await session.execute(
                select(
                    CatalogSourceBucket.tokens,
                    CatalogSourceBucket.refilled_at,
                    CatalogSourceBucket.blocked_until,
                )
                .where(CatalogSourceBucket.source_slug == slug)
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            return None
        return BucketState(tokens=row[0], refilled_at=row[1], blocked_until=row[2])

    @staticmethod
    async def write(
        session: AsyncSession,
        slug: str,
        *,
        tokens: float,
        refilled_at: datetime,
        blocked_until: datetime | None,
    ) -> None:
        """Store ``slug``'s bucket state (after :meth:`lock` in the same transaction)."""
        await session.execute(
            update(CatalogSourceBucket)
            .where(CatalogSourceBucket.source_slug == slug)
            .values(
                tokens=tokens,
                refilled_at=refilled_at,
                blocked_until=blocked_until,
                updated_at=refilled_at,
            )
        )
