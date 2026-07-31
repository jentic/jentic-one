"""Repository for ``catalog_update_checks`` — per-API update-notify bookkeeping.

Flush-only, never commits (the caller owns the transaction), matching the rest
of the registry repositories. Rows are keyed by ``local_api_id`` (unique), so
reads/writes go through :meth:`get` / :meth:`upsert` on that key.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_update_checks import CatalogUpdateCheck
from jentic_one.shared.models import ApiRevisionState


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
    def _outdated_base() -> Select[tuple[uuid.UUID, str]]:
        """Shared selectable of ``(local_api_id, spec_url)`` for genuinely-outdated APIs.

        An API is *outdated* when its check row carries a ``last_notified_digest`` (a real
        ``catalog.update_available`` fired for it) that differs from the digest of the API's
        **served** revision — the same single revision the notify sweep compares against
        (:meth:`ApiRevisionRepository.registered_specs_for_notify`): the API's
        ``current_revision_id`` when set, else the newest non-archived revision (``created_at``
        then ``id`` as the deterministic tiebreak). Re-importing the upstream makes that
        served digest equal the notified digest, so the API silently drops out — the read-time
        "resolve on re-import" without touching the event row.

        Matching that *single* revision (not "any non-archived revision") is essential: an API
        can legitimately keep a stale, never-promoted ``draft`` alongside its live
        ``published``/``imported`` revision, and comparing against every non-archived row would
        leave the API stuck as outdated forever even after the served revision adopts the
        upstream. An API with no non-archived revision is excluded (nothing served to be
        outdated). Exposes both the ``local_api_id`` (for API-keyed surfaces) and the
        ``spec_url`` (for the manifest-keyed catalog list).
        """
        # Rank each API's non-archived revisions exactly as the notify sweep does — current
        # revision first, then newest — and keep only rank 1 (the served revision).
        served_rank = func.row_number().over(
            partition_by=ApiRevision.api_id,
            order_by=(
                case((ApiRevision.id == Api.current_revision_id, 0), else_=1),
                ApiRevision.created_at.desc(),
                ApiRevision.id,
            ),
        )
        served = (
            select(
                ApiRevision.api_id.label("api_id"),
                ApiRevision.spec_digest.label("spec_digest"),
                served_rank.label("rnk"),
            )
            .join(Api, Api.id == ApiRevision.api_id)
            .where(ApiRevision.state != ApiRevisionState.ARCHIVED)
            .subquery()
        )
        return (
            select(
                CatalogUpdateCheck.local_api_id.label("local_api_id"),
                CatalogUpdateCheck.spec_url.label("spec_url"),
            )
            .join(served, served.c.api_id == CatalogUpdateCheck.local_api_id)
            .where(served.c.rnk == 1)
            .where(CatalogUpdateCheck.last_notified_digest.is_not(None))
            .where(CatalogUpdateCheck.last_notified_digest != served.c.spec_digest)
        )

    @staticmethod
    async def outdated_api_ids(session: AsyncSession) -> set[uuid.UUID]:
        """Local ``api_id``s whose upstream has a notified update the served revision lacks.

        The API-keyed form of the outdated set. **Use this for per-API surfaces** (the
        ``/apis`` list and single-API view): those are keyed on the local ``api_id``, and two
        distinct local APIs can share one upstream ``source_url`` (umbrella specs split across
        vendor + sub-APIs, or the same URL re-imported under a different identity). Testing
        ``source_url`` membership would then flag *every* API sharing that URL — including ones
        already up to date. Testing ``api_id`` membership is exact. See :meth:`_outdated_base`.
        """
        sub = CatalogUpdateCheckRepository._outdated_base().subquery()
        result = await session.execute(select(sub.c.local_api_id))
        return {api_id for (api_id,) in result.all() if api_id is not None}

    @staticmethod
    async def outdated_spec_urls(session: AsyncSession) -> set[str]:
        """Spec URLs whose upstream has a notified update the served revision hasn't adopted.

        The manifest-keyed form. **Use this only for the catalog list**, which is keyed on the
        manifest ``spec_url`` (each manifest entry maps to a distinct ``spec_url``), so URL
        membership is exact there. For per-API surfaces use :meth:`outdated_api_ids` instead —
        ``source_url`` is not unique across local APIs. See :meth:`_outdated_base`.
        """
        sub = CatalogUpdateCheckRepository._outdated_base().subquery()
        result = await session.execute(select(sub.c.spec_url))
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
