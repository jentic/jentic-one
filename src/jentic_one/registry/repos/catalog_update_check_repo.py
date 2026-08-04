"""Repository for ``catalog_update_checks`` — per-API update-notify bookkeeping.

Flush-only, never commits (the caller owns the transaction), matching the rest
of the registry repositories. Rows are keyed by ``local_api_id`` (unique), so
reads/writes go through :meth:`get` / :meth:`upsert` on that key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Select, case, func, select, update
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
    def _outdated_base(now: datetime | None = None) -> Select[tuple[uuid.UUID, str]]:
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

        Snooze (C1, #925): a row is *also* excluded while an operator snooze covers the exact
        change currently prompting — ``snoozed_digest == last_notified_digest`` and the snooze
        hasn't expired (``snoozed_until IS NULL`` = mute-until-newer, or ``snoozed_until`` in
        the future). Keying the snooze on ``last_notified_digest`` (not ``last_seen``) means a
        genuinely *newer* upstream digest — which the sweep records as a new
        ``last_notified_digest`` — no longer matches ``snoozed_digest``, so the badge re-lights
        automatically for a real new change. ``include_snoozed=True`` on the callers bypasses
        this so an operator can still list snoozed rows.
        """
        return CatalogUpdateCheckRepository._served_outdated_core().where(
            CatalogUpdateCheckRepository._not_snoozed(now)
        )

    @staticmethod
    def _served_outdated_core() -> Select[tuple[uuid.UUID, str]]:
        """The outdated selectable *without* the snooze predicate — the shared core.

        Ranks each API's non-archived revisions exactly as the notify sweep does (current
        revision first, then newest ``created_at``, then ``id`` as the deterministic
        tiebreak), keeps only rank 1 (the served revision), and returns the
        ``(local_api_id, spec_url)`` rows whose ``last_notified_digest`` differs from that
        served revision's digest. :meth:`_outdated_base` appends :meth:`_not_snoozed`;
        :meth:`_outdated_base_unsnoozed` returns this as-is. Kept single-sourced so the
        served-revision selection can never drift between the snoozed and include-snoozed
        variants.
        """
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
    def _not_snoozed(now: datetime | None):  # type: ignore[no-untyped-def]
        """WHERE predicate: the row's outstanding notify is NOT currently snoozed.

        A snooze is active when it pins the currently-notified digest
        (``snoozed_digest == last_notified_digest``) and hasn't expired. ``snoozed_until IS
        NULL`` means mute-until-newer (no time expiry); a non-null ``snoozed_until`` is a
        time-boxed snooze that lapses at that instant.

        ``now`` defaults to the current UTC time when not supplied, so a caller that doesn't
        thread a clock still correctly re-lights an *expired* time-boxed snooze rather than
        pinning it forever. (Earlier this defaulted to "treat any non-null ``snoozed_until``
        as active", which silently hid lapsed snoozes on the per-API and single-entry
        surfaces — the callers that don't pass ``now``.) Pass an explicit ``now`` only to
        pin evaluation to a fixed instant (e.g. one clock read shared across a sweep, or a
        test).
        """
        if now is None:
            now = datetime.now(UTC)
        active_window: Any = CatalogUpdateCheck.snoozed_until.is_(None) | (
            CatalogUpdateCheck.snoozed_until > now
        )
        snoozed = (
            CatalogUpdateCheck.snoozed_digest.is_not(None)
            & (CatalogUpdateCheck.snoozed_digest == CatalogUpdateCheck.last_notified_digest)
            & active_window
        )
        return ~snoozed

    @staticmethod
    async def outdated_api_ids(
        session: AsyncSession, *, now: datetime | None = None, include_snoozed: bool = False
    ) -> set[uuid.UUID]:
        """Local ``api_id``s whose upstream has a notified update the served revision lacks.

        The API-keyed form of the outdated set. **Use this for per-API surfaces** (the
        ``/apis`` list and single-API view): those are keyed on the local ``api_id``, and two
        distinct local APIs can share one upstream ``source_url`` (umbrella specs split across
        vendor + sub-APIs, or the same URL re-imported under a different identity). Testing
        ``source_url`` membership would then flag *every* API sharing that URL — including ones
        already up to date. Testing ``api_id`` membership is exact. See :meth:`_outdated_base`.

        ``include_snoozed=True`` bypasses the snooze exclusion (C1) so an operator surface can
        still list snoozed rows.
        """
        base = (
            CatalogUpdateCheckRepository._outdated_base_unsnoozed()
            if include_snoozed
            else CatalogUpdateCheckRepository._outdated_base(now)
        )
        result = await session.execute(select(base.subquery().c.local_api_id))
        return {api_id for (api_id,) in result.all() if api_id is not None}

    @staticmethod
    async def outdated_spec_urls(
        session: AsyncSession, *, now: datetime | None = None, include_snoozed: bool = False
    ) -> set[str]:
        """Spec URLs whose upstream has a notified update the served revision hasn't adopted.

        The manifest-keyed form. **Use this only for the catalog list**, which is keyed on the
        manifest ``spec_url`` (each manifest entry maps to a distinct ``spec_url``), so URL
        membership is exact there. For per-API surfaces use :meth:`outdated_api_ids` instead —
        ``source_url`` is not unique across local APIs. See :meth:`_outdated_base`.

        ``include_snoozed=True`` bypasses the snooze exclusion (C1).
        """
        base = (
            CatalogUpdateCheckRepository._outdated_base_unsnoozed()
            if include_snoozed
            else CatalogUpdateCheckRepository._outdated_base(now)
        )
        result = await session.execute(select(base.subquery().c.spec_url))
        return {url for (url,) in result.all() if url}

    @staticmethod
    def _outdated_base_unsnoozed() -> Select[tuple[uuid.UUID, str]]:
        """:meth:`_outdated_base` without the snooze exclusion (include-snoozed surfaces)."""
        return CatalogUpdateCheckRepository._served_outdated_core()

    @staticmethod
    async def snooze(
        session: AsyncSession,
        local_api_id: uuid.UUID,
        *,
        digest: str,
        until: datetime | None,
    ) -> int:
        """Snooze the outstanding update for one API (C1, #925). Returns rowcount.

        Pins ``snoozed_digest = digest`` (the operator-accepted upstream digest, normally the
        row's current ``last_notified_digest``) and ``snoozed_until = until`` (None =
        mute-until-newer). A newer upstream digest later overwrites ``last_notified_digest``,
        so the ``snoozed_digest == last_notified_digest`` match in :meth:`_not_snoozed` no
        longer holds and the badge re-lights — a real new change is never hidden. Operator-
        gated + audited by the caller.
        """
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                update(CatalogUpdateCheck)
                .where(CatalogUpdateCheck.local_api_id == local_api_id)
                .values(snoozed_digest=digest, snoozed_until=until)
            ),
        )
        await session.flush()
        return result.rowcount or 0

    @staticmethod
    async def unsnooze(session: AsyncSession, local_api_id: uuid.UUID) -> int:
        """Clear any snooze for one API (C1). Returns rowcount."""
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                update(CatalogUpdateCheck)
                .where(CatalogUpdateCheck.local_api_id == local_api_id)
                .values(snoozed_digest=None, snoozed_until=None)
            ),
        )
        await session.flush()
        return result.rowcount or 0

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
        notified_event_class: str | None = None,
        sync_notified: bool = False,
    ) -> CatalogUpdateCheck:
        """Insert or update the observation for ``local_api_id`` and return the row.

        ``notified_digest`` is only written when non-``None`` — a probe that
        observes no change (or a ``304``) must not clear the digest that last
        produced an event, or the dedupe would re-fire on the next real change.

        ``notified_event_class`` records *which* event class that digest fired under
        (``catalog.update_available`` vs ``catalog.update_conflicts_overlay``). It is
        written on the same branches as ``last_notified_digest`` (real notify or a
        sync-revert) so the dedupe pair ``(last_notified_digest, last_notified_event_class)``
        stays consistent — a digest that re-classifies between the two classes is not
        wrongly deduped against the prior class.

        ``sync_notified`` is the one exception to that "never lower the digest"
        rule: when the caller observes that the *upstream is back in sync with the
        served revision* (``upstream_digest == spec_digest``), it sets
        ``last_notified_digest = digest`` so the read surface
        (:meth:`_outdated_base`, ``!= served digest``) drops the API out of the
        outdated set. Without this, an upstream revert leaves ``last_notified_digest``
        pinned at the reverted digest and every surface stays stuck as "update
        available" with no operator action able to clear it (re-importing adopts a
        spec byte-identical to the served one, so the served digest never moves).
        Event dedupe is unaffected — a later, genuinely different upstream digest
        still differs from both the served and the synced ``last_notified_digest``,
        so the next real change re-fires exactly once.

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
                last_notified_digest=digest if sync_notified else notified_digest,
                last_notified_event_class=(
                    notified_event_class if (sync_notified or notified_digest is not None) else None
                ),
                last_checked_at=checked_at,
            )
            session.add(row)
        else:
            row.spec_url = spec_url
            if etag is not None:
                row.last_seen_etag = etag
            if digest is not None:
                row.last_seen_digest = digest
            if sync_notified:
                # Upstream is back in sync with the served revision: pin the notified
                # digest to it so the outdated read surface clears (see docstring).
                row.last_notified_digest = digest
                row.last_notified_event_class = notified_event_class
            elif notified_digest is not None:
                row.last_notified_digest = notified_digest
                row.last_notified_event_class = notified_event_class
            row.last_checked_at = checked_at
        await session.flush()
        return row
