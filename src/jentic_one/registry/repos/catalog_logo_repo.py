"""Repository for ``catalog_logos`` — the server-side catalog logo cache.

Flush-only, never commits (the caller owns the transaction), matching the rest
of the registry repositories. Rows are keyed by ``source_url`` (unique).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.catalog_logos import CatalogLogo


class CatalogLogoRepository:
    """Data access for cached catalog logos — flush-only, never commits."""

    @staticmethod
    async def get(session: AsyncSession, source_url: str) -> CatalogLogo | None:
        """Return the cache row for an upstream logo URL, or ``None`` when never fetched."""
        result = await session.execute(
            select(CatalogLogo).where(CatalogLogo.source_url == source_url)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert(
        session: AsyncSession,
        *,
        source_url: str,
        status: str,
        fetched_at: datetime,
        content: bytes | None = None,
        content_type: str | None = None,
        digest: str | None = None,
        upstream_etag: str | None = None,
    ) -> None:
        """Insert or fully replace the cache row for ``source_url``.

        ``INSERT … ON CONFLICT`` on the unique ``source_url`` so two requests
        fetching the same uncached logo concurrently both succeed; the later
        write wins, and both wrote the same upstream bytes.
        """
        values = {
            "status": status,
            "fetched_at": fetched_at,
            "content": content,
            "content_type": content_type,
            "digest": digest,
            "upstream_etag": upstream_etag,
        }
        stmt = insert(CatalogLogo).values(source_url=source_url, **values)
        stmt = stmt.on_conflict_do_update(index_elements=["source_url"], set_=values)
        await session.execute(stmt)
        await session.flush()

    @staticmethod
    async def mark(
        session: AsyncSession,
        *,
        source_url: str,
        status: str,
        fetched_at: datetime,
        upstream_etag: str | None = None,
    ) -> None:
        """Record a fetch outcome on an existing row, keeping its cached bytes.

        Used for a ``304`` revalidation (``status="ok"``, refreshed validator) and
        for a failed refetch of a logo that is already cached (``status="error"``),
        so the stale image keeps being served until a refetch succeeds.
        ``upstream_etag`` is only overwritten when given.
        """
        values: dict[str, object] = {"status": status, "fetched_at": fetched_at}
        if upstream_etag is not None:
            values["upstream_etag"] = upstream_etag
        await session.execute(
            update(CatalogLogo).where(CatalogLogo.source_url == source_url).values(**values)
        )
        await session.flush()
