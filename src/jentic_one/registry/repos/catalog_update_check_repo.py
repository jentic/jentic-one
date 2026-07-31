"""Repository for ``catalog_update_checks`` — per-API update-notify bookkeeping.

Flush-only, never commits (the caller owns the transaction), matching the rest
of the registry repositories. Rows are keyed by ``local_api_id`` (unique), so
reads/writes go through :meth:`get` / :meth:`upsert` on that key.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.catalog_update_checks import CatalogUpdateCheck


class CatalogUpdateCheckRepository:
    """Data access for the update-notify check rows — flush-only, never commits."""

    @staticmethod
    async def get(session: AsyncSession, local_api_id: uuid.UUID) -> CatalogUpdateCheck | None:
        """Return the check row for a local API, or ``None`` when never probed."""
        result = await session.execute(
            select(CatalogUpdateCheck).where(CatalogUpdateCheck.local_api_id == local_api_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def outdated_spec_urls(session: AsyncSession) -> set[str]:
        """Spec URLs whose upstream has a notified update the local revision hasn't adopted.

        An API is *outdated* when its check row carries a ``last_notified_digest`` (a real
        ``catalog.update_available`` fired for it) that differs from the current
        (non-archived) local revision's ``spec_digest``. Re-importing the upstream makes
        the local digest equal the notified digest, so the API silently drops out of this
        set — the read-time "resolve on re-import" without touching the event row.

        Returned as a set of ``spec_url`` so the catalog list (keyed on manifest
        ``spec_url``, not local ``api_id``) can flag ``update_available`` with a single
        membership test. Joins on ``local_api_id`` → ``api_revisions.api_id``; an API with
        no active revision (only archived) is excluded (nothing served to be outdated).
        """
        result = await session.execute(
            select(CatalogUpdateCheck.spec_url)
            .join(ApiRevision, ApiRevision.api_id == CatalogUpdateCheck.local_api_id)
            .where(CatalogUpdateCheck.last_notified_digest.is_not(None))
            .where(CatalogUpdateCheck.last_notified_digest != ApiRevision.spec_digest)
            .where(ApiRevision.state != "archived")
        )
        return {url for (url,) in result.all() if url}

    @staticmethod
    async def upsert(
        session: AsyncSession,
        *,
        local_api_id: uuid.UUID,
        spec_url: str,
        etag: str | None,
        digest: str | None,
        checked_at: datetime,
        notified_digest: str | None = None,
    ) -> CatalogUpdateCheck:
        """Insert or update the observation for ``local_api_id`` and return the row.

        ``notified_digest`` is only written when non-``None`` — a probe that
        observes no change (or a ``304``) must not clear the digest that last
        produced an event, or the dedupe would re-fire on the next real change.

        Read-then-insert (no ``ON CONFLICT``) is acceptable because the
        update-notify sweep is triggered only from the (rare, max-age-gated) lazy
        manifest refresh and probes each API at most once per interval, so a true
        concurrent write to the same ``local_api_id`` is unlikely. If two refreshes
        do race, the unique ``local_api_id`` index turns the losing INSERT into an
        ``IntegrityError`` (no duplicate row) — the worst case is one duplicate
        ``catalog.update_available`` event, which the next sweep dedupes. Promote to
        ``INSERT … ON CONFLICT`` if the sweep is ever hoisted onto a shared lock or
        run concurrently.
        """
        row = await CatalogUpdateCheckRepository.get(session, local_api_id)
        if row is None:
            row = CatalogUpdateCheck(
                local_api_id=local_api_id,
                spec_url=spec_url,
                last_seen_etag=etag,
                last_seen_digest=digest,
                last_notified_digest=notified_digest,
                last_checked_at=checked_at,
            )
            session.add(row)
        else:
            row.spec_url = spec_url
            if etag is not None:
                row.last_seen_etag = etag
            if digest is not None:
                row.last_seen_digest = digest
            if notified_digest is not None:
                row.last_notified_digest = notified_digest
            row.last_checked_at = checked_at
        await session.flush()
        return row
