"""Repository for the LatestRelease singleton — flush-only, never commits."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.latest_releases import LATEST_RELEASE_KEY, LatestRelease


class LatestReleaseRepository:
    """Data access for the single last-known-latest-release row."""

    @staticmethod
    async def get(session: AsyncSession) -> LatestRelease | None:
        """Return the singleton row, or ``None`` if nothing has been reported yet."""
        stmt = select(LatestRelease).where(LatestRelease.key == LATEST_RELEASE_KEY)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert(
        session: AsyncSession,
        *,
        version: str,
        reported_by: str | None,
    ) -> LatestRelease:
        """Insert or update the singleton row, keyed by the fixed natural key."""
        record = await LatestReleaseRepository.get(session)
        if record is None:
            record = LatestRelease(
                key=LATEST_RELEASE_KEY,
                version=version,
                created_by=reported_by,
            )
            session.add(record)
        else:
            record.version = version
        await session.flush()
        return record
