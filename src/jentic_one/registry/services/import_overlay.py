"""Overlay link and supersede reconciliation for import jobs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import structlog

from jentic_one.registry.core.schema.overlays import Overlay
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.overlay_repo import OverlayRepository
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.registry.repos.search.filters import ACTIVE_REVISION_STATES
from jentic_one.registry.services.catalog.flow3_metrics import (
    record_overlay_auto_deprecated,
    record_overlay_supersede_skipped,
)
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.models import (
    ORIGIN_OVERLAY,
    OverlayDeprecationReason,
    OverlayStatus,
)
from jentic_one.shared.models.events import EventSeverity, EventType

logger = structlog.get_logger(__name__)


class SupersedeSkipReason(StrEnum):
    """Why an A4b supersede re-import left its overlay CONFIRMED (#1078).

    Recorded as ``supersede_skipped`` on the import job result and as the ``reason``
    label of the ``jentic_one.flow3.overlay_supersede_skipped`` counter.
    """

    #: The superseding revision landed on a different ``Api`` row than the overlay's
    #: (e.g. the clean identity of a vendor-doubled API), so nothing the overlay serves
    #: was archived.
    API_MISMATCH = "api_mismatch"
    #: The overlay's confirmed revision is still the active (served) revision of its API.
    REVISION_STILL_SERVED = "revision_still_served"


class _Recovery(StrEnum):
    """Outcome of a duplicate-on-retry recovery attempt."""

    #: The prior attempt's durable effect is in place; the job succeeds.
    RECOVERED = "recovered"
    #: Nothing to recover: the duplicate is a genuine failure.
    NOT_RECOVERABLE = "not_recoverable"
    #: Recovery raised (e.g. a database error) before it could decide; a retry
    #: of the job runs it again.
    ERRORED = "errored"


@dataclass(frozen=True)
class _SupersedeTarget:
    """The overlay an A4b supersede should deprecate, and the API row it superseded.

    ``superseding_api_id`` is the ``Api`` row the superseding revision landed on, or
    ``None`` when it could not be resolved (the same-API guard is then skipped).
    """

    overlay_id: str
    superseding_api_id: uuid.UUID | None


@dataclass(frozen=True)
class _SupersedeRecovery:
    """Outcome of a duplicate-on-retry A4b supersede recovery attempt."""

    outcome: _Recovery
    skipped: SupersedeSkipReason | None = None


class _OverlayReconciliationMixin:
    """Reconciles overlay revision links and supersede deprecation for import jobs."""

    _ctx: Context

    async def _link_overlay_revision(
        self,
        job_id: str,
        overlay_id: str,
        revision_id: str,
        superseded_revision_id: str | None = None,
    ) -> None:
        """Record the materialized revision on the overlay (best-effort).

        Runs in its own registry_db transaction — the Ingestor already committed the
        revision, so this is a follow-up write. A failure here does not undo the
        (durable) re-ingest, so it is logged rather than raised: the served spec is
        already correct; only the overlay->revision back-reference is missing and can
        be repaired by re-confirming.

        ``superseded_revision_id`` (the revision this materialization archived) is
        recorded alongside so a later un-confirm/rollback (A5b) has a deterministic
        prior-revision target. ``None`` when the materialize superseded nothing (a
        first-ever current revision).
        """
        try:
            async with self._ctx.registry_db.transaction() as session:
                updated = await OverlayRepository.set_confirmed_revision(
                    session,
                    overlay_id,
                    uuid.UUID(revision_id),
                    superseded_revision_id=(
                        uuid.UUID(superseded_revision_id)
                        if superseded_revision_id is not None
                        else None
                    ),
                )
            if updated == 0:
                logger.warning(
                    "overlay_confirmed_revision_not_linked",
                    job_id=job_id,
                    overlay_id=overlay_id,
                    reason="overlay_not_found",
                )
        except Exception:
            logger.exception(
                "overlay_confirmed_revision_link_failed",
                job_id=job_id,
                overlay_id=overlay_id,
            )

    async def _deprecate_superseded_overlay(
        self,
        job_id: str,
        overlay_id: str,
        *,
        superseding_revision: dict[str, Any] | None = None,
        superseding_api_id: uuid.UUID | None = None,
        actor_id: str,
        actor_type: str | None,
    ) -> SupersedeSkipReason | None:
        """Auto-deprecate an overlay superseded by an authorized catalog re-import (A4b).

        Flips the overlay's own lifecycle to DEPRECATED when the re-ingest archived its
        materialized revision and made the fresh upstream the served spec — the
        condition the same-API guard below checks rather than assumes. Runs in its own
        registry_db transaction — a failure here does not undo the durable re-ingest, so
        it is logged rather than raised (the served spec is already correct; only the
        overlay's status label lags and can be repaired).

        Which overlay to deprecate: prefer the one that *actually backed the revision the
        supersede just archived* (``superseding_revision["superseded_revision_id"]``),
        re-resolved here rather than trusting the enqueue-time ``overlay_id``. A concurrent
        confirm can make a *different* overlay live between the A4b authorization gate and
        this worker; the ingest stage archives whatever is current *now*, so deprecating the
        stale enqueue-time id would leave the actually-superseded overlay stuck CONFIRMED
        over an archived revision (#940). Fall back to the enqueue-time ``overlay_id`` when
        the archived revision or its backing overlay can't be re-resolved.

        Same-API guard (#1078): the deprecate only fires when the re-import actually
        superseded the overlay — the overlay lives on the API row the superseding revision
        landed on (``superseding_api_id``, else the row the resolver read from
        ``superseding_revision``'s identity) and its ``confirmed_revision_id`` is not an
        active (served) revision. A re-import that resolved a *different* API row (e.g. the
        clean identity of a vendor-doubled API) archives nothing on the overlay's row, so
        the overlay's revision is still served and deprecating it would make its status
        lie. The guard reads under a row lock and the demote's compare-and-swap re-checks
        the same fields (API row, confirmed revision, revision not active), so a concurrent
        relink cannot slip in between check and write. The CAS also requires that the
        overlay's API is not serving an overlay-origin revision: in every legitimate
        supersede the served revision is the fresh upstream, so a re-materialize that
        committed its revision as current before linking it blocks the demote. A skip is
        logged as ``overlay_supersede_skipped``, counted, and its reason returned so the
        caller can record it on the job result. Returns ``None`` otherwise.

        L2 (#921): on a successful demote we emit an attributed ``overlay.deprecated``
        event so the overlay is not *silently* flipped behind the audit log. ``actor_id``/
        ``actor_type`` are the authorizing operator (the job's ``created_by``); the event
        also carries the overlay's own author (``created_by``) so a UI can notify them and
        show "deprecated by re-import on <date>" (L3). Emitted best-effort in the admin DB
        (events live there), requires_action=False — a notification, not an inbox item.
        """
        # Resolve which overlay to deprecate first, isolating a *resolution* failure (a DB
        # read error while re-resolving) from a *demote* failure. On a resolver error, fall
        # back to the enqueue-time id and still attempt the demote — a transient resolver
        # error must not skip the deprecate entirely (which would leave the overlay
        # CONFIRMED over an archived revision). The resolver's own miss-cases already
        # return the enqueue-time id internally; this guard only covers it *raising*.
        try:
            target = await self._resolve_superseded_overlay_id(
                job_id, overlay_id, superseding_revision
            )
        except Exception:
            logger.exception(
                "overlay_supersede_resolve_failed",
                job_id=job_id,
                overlay_id=overlay_id,
            )
            target = _SupersedeTarget(overlay_id=overlay_id, superseding_api_id=None)
        target_overlay_id = target.overlay_id
        if superseding_api_id is None:
            superseding_api_id = target.superseding_api_id
        try:
            async with self._ctx.registry_db.transaction() as session:
                overlay = await OverlayRepository.get_by_id_for_update(session, target_overlay_id)
                skip_reason = await self._supersede_skip_reason(
                    session, overlay, superseding_api_id
                )
                if skip_reason is not None:
                    assert overlay is not None  # a skip always names an existing overlay
                    logger.warning(
                        "overlay_supersede_skipped",
                        job_id=job_id,
                        overlay_id=target_overlay_id,
                        reason=skip_reason.value,
                        overlay_api_id=str(overlay.api_id),
                        superseding_api_id=(
                            str(superseding_api_id) if superseding_api_id is not None else None
                        ),
                        confirmed_revision_id=(
                            str(overlay.confirmed_revision_id)
                            if overlay.confirmed_revision_id is not None
                            else None
                        ),
                        detail=(
                            "the re-import did not supersede this overlay's served revision "
                            "(different API row, or its revision is still active); leaving "
                            "the overlay confirmed (#1078)"
                        ),
                    )
                    record_overlay_supersede_skipped(skip_reason.value)
                    return skip_reason
                demoted = 0
                if overlay is not None:
                    demoted = await OverlayRepository.set_status(
                        session,
                        target_overlay_id,
                        OverlayStatus.DEPRECATED,
                        deprecated_at=datetime.now(UTC),
                        deprecated_reason=OverlayDeprecationReason.SUPERSEDED_BY_REIMPORT,
                        expected_status=OverlayStatus.CONFIRMED,
                        expected_api_id=superseding_api_id,
                        expected_confirmed_revision_id=overlay.confirmed_revision_id,
                        require_confirmed_revision_inactive=True,
                        require_api_serves_non_overlay=True,
                    )
        except Exception:
            logger.exception(
                "overlay_supersede_deprecate_failed",
                job_id=job_id,
                overlay_id=target_overlay_id,
            )
            return None
        if demoted == 0:
            logger.warning(
                "overlay_supersede_not_deprecated",
                job_id=job_id,
                overlay_id=target_overlay_id,
                reason="cas_rejected",
                detail=(
                    "the overlay is missing, no longer confirmed, or changed since the "
                    "guard read (relinked, its revision active again, or its API "
                    "serving a re-materialized overlay revision)"
                ),
            )
            return None
        # The demote has committed, so the event and the counter are strictly
        # after the durable work. A failure here must stay logged: reporting it
        # as a failed supersede makes the caller fail the whole import job, and
        # the worker then requeues an import whose writes are already done.
        try:
            await self._emit_overlay_deprecated(
                job_id=job_id,
                overlay=overlay,
                overlay_id=target_overlay_id,
                actor_id=actor_id,
                actor_type=actor_type,
            )
            record_overlay_auto_deprecated()
        except Exception:
            logger.exception(
                "overlay_supersede_notify_failed",
                job_id=job_id,
                overlay_id=target_overlay_id,
            )
        return None

    @staticmethod
    async def _supersede_skip_reason(
        session: Any,
        overlay: Overlay | None,
        superseding_api_id: uuid.UUID | None,
    ) -> SupersedeSkipReason | None:
        """Why the A4b deprecate must not fire for ``overlay``, or ``None`` if it may (#1078).

        ``API_MISMATCH``: the superseding revision landed on a different API row than the
        overlay's, so the supersede archived nothing the overlay serves (skipped when
        ``superseding_api_id`` is unknown). ``REVISION_STILL_SERVED``: the overlay's
        confirmed revision is still active (published/imported), i.e. it is the served
        revision. An overlay with no linked revision (lazy-link window) on the superseding
        API, or a missing overlay, passes — the CAS demote decides those.
        """
        if overlay is None:
            return None
        if superseding_api_id is not None and overlay.api_id != superseding_api_id:
            return SupersedeSkipReason.API_MISMATCH
        if overlay.confirmed_revision_id is not None:
            state = await ApiRevisionRepository.state_of(session, overlay.confirmed_revision_id)
            if state in ACTIVE_REVISION_STATES:
                return SupersedeSkipReason.REVISION_STILL_SERVED
        return None

    async def _resolve_superseded_overlay_id(
        self,
        job_id: str,
        enqueue_overlay_id: str,
        superseding_revision: dict[str, Any] | None,
    ) -> _SupersedeTarget:
        """Resolve the overlay that actually backed the supersede-archived revision (#940).

        Returns the CONFIRMED overlay whose ``confirmed_revision_id`` is the revision the
        worker just archived (captured pre-archive as
        ``superseding_revision["superseded_revision_id"]``), re-resolved here rather than
        trusting the enqueue-time id (a concurrent confirm may have changed the live overlay
        after enqueue), together with the ``Api`` row the superseding revision landed on
        (resolved from its ``(vendor, name, version)``) so the same-API guard keys on the
        same row read.

        Two-stage resolution:
        1. Strict: :meth:`OverlayRepository.get_live_confirmed_for_revision` on the archived
           revision — unambiguous when the backing overlay is linked.
        2. Lazy-link recovery: the strict lookup misses in the lazy-link window (the confirm
           path makes the overlay's revision current *before* it stamps
           ``confirmed_revision_id``; that link is a separate, best-effort txn, so a live
           CONFIRMED overlay can transiently — or durably, on a failed link — have a NULL
           link). When the archived revision was overlay-origin and there is *exactly one*
           NULL-linked CONFIRMED overlay for the API, that is the live overlay → deprecate
           it. This mirrors why :meth:`get_live_confirmed_for_api` refuses to key strictly.

        Falls back to the enqueue-time ``enqueue_overlay_id`` when neither stage resolves
        (no archived revision captured, API/overlay unresolved, or an *ambiguous*
        lazy-link set). Logs when the re-resolved overlay differs from the enqueue-time one,
        or when it can't safely re-resolve, so the concurrent-confirm race is observable.
        """
        fallback = _SupersedeTarget(overlay_id=enqueue_overlay_id, superseding_api_id=None)
        if superseding_revision is None:
            return fallback
        api = superseding_revision.get("api") or {}
        vendor, name, version = api.get("vendor"), api.get("name"), api.get("version")
        if not (vendor and name and version):
            return fallback
        archived_id_raw = superseding_revision.get("superseded_revision_id")
        archived_revision_id: uuid.UUID | None = None
        if archived_id_raw:
            try:
                archived_revision_id = uuid.UUID(str(archived_id_raw))
            except ValueError:
                archived_revision_id = None

        overlay: Overlay | None = None
        async with self._ctx.registry_db.session() as session:
            api_row = await ApiRepository.get_by_identifier(session, vendor, name, version)
            if api_row is None:
                return fallback
            superseding_api_id = api_row.id
            if archived_revision_id is None:
                # Nothing archived on the superseding row (it had no current revision).
                return _SupersedeTarget(enqueue_overlay_id, superseding_api_id)
            overlay = await OverlayRepository.get_live_confirmed_for_revision(
                session, superseding_api_id, archived_revision_id
            )
            if overlay is None:
                # Strict miss — try the lazy-link recovery. Only meaningful when the archived
                # revision was itself overlay-origin (an overlay backed it); a catalog/manual
                # base has no overlay to re-resolve.
                archived_origin = await ApiRevisionRepository.origin_of(
                    session, archived_revision_id
                )
                if archived_origin == ORIGIN_OVERLAY:
                    unlinked = await OverlayRepository.list_confirmed_unlinked_for_api(
                        session, superseding_api_id
                    )
                    if len(unlinked) == 1:
                        overlay = unlinked[0]

        if overlay is None:
            # Could not safely re-resolve. Do NOT silently trust the enqueue-time id — a
            # concurrent confirm may have made it stale. Fall back to it (#940), but warn so
            # the wrong-overlay risk is observable rather than silent. The same-API guard
            # still checks the fallback against ``superseding_api_id``.
            logger.warning(
                "overlay_supersede_target_unresolved",
                job_id=job_id,
                enqueue_overlay_id=enqueue_overlay_id,
                archived_revision_id=str(archived_revision_id),
                superseding_api_id=str(superseding_api_id),
                detail=(
                    "no confirmed overlay on the superseding API backed the archived "
                    "revision (a catalog base, a lazy-link window or ambiguous confirmed "
                    "set, or the re-import landed on a different API row than the "
                    "overlay's); falling back to the enqueue-time overlay id (#940)"
                ),
            )
            return _SupersedeTarget(enqueue_overlay_id, superseding_api_id)
        if overlay.id != enqueue_overlay_id:
            logger.info(
                "overlay_supersede_target_reresolved",
                job_id=job_id,
                enqueue_overlay_id=enqueue_overlay_id,
                resolved_overlay_id=overlay.id,
                archived_revision_id=str(archived_revision_id),
                detail=(
                    "a concurrent confirm changed the live overlay after enqueue; "
                    "deprecating the overlay that backed the archived revision (#940)"
                ),
            )
        return _SupersedeTarget(overlay.id, superseding_api_id)

    async def _emit_overlay_deprecated(
        self,
        *,
        job_id: str,
        overlay: Overlay | None,
        overlay_id: str,
        actor_id: str,
        actor_type: str | None,
    ) -> None:
        """Emit the attributed ``overlay.deprecated`` notification (L2/L3).

        Best-effort in the admin DB. ``author`` is the overlay's own ``created_by`` (whom
        to notify); ``actor_id``/``actor_type`` is the operator who ran the superseding
        re-import. Swallows failures — the demote already committed; a missed notification
        must not fail (or retry-loop) the import job.
        """
        author = overlay.created_by if overlay is not None else None
        api_id = overlay.api_id if overlay is not None else None
        try:
            async with self._ctx.admin_db.transaction() as session:
                await emit_event_best_effort(
                    session,
                    type=EventType.OVERLAY_DEPRECATED,
                    severity=EventSeverity.INFO,
                    summary=(
                        f"Overlay {overlay_id} was deprecated by an authorized catalog "
                        "re-import that adopted the upstream spec"
                    ),
                    requires_action=False,
                    created_by=actor_id,
                    actor_id=actor_id,
                    actor_type=actor_type,
                    data={
                        "overlay_id": overlay_id,
                        "api_id": str(api_id) if api_id is not None else None,
                        "author": author,
                        "reason": "superseded_by_catalog_reimport",
                    },
                )
        except Exception:
            logger.exception("overlay_deprecated_emit_failed", job_id=job_id, overlay_id=overlay_id)

    async def _recover_supersede(
        self, job_id: str, overlay_id: str, source: Any, *, actor_id: str, actor_type: str | None
    ) -> _SupersedeRecovery:
        """Recover an interrupted A4b supersede after a duplicate-on-retry re-ingest.

        Mirrors ``_recover_overlay_link`` for the supersede path. A prior attempt already
        committed the fresh upstream revision and made it current, then crashed before the
        separate-transaction deprecate ran; the retry re-ingests identical content and
        fails with ``DuplicateRevisionError`` (no new revision). Returns
        ``_SupersedeRecovery(outcome=_Recovery.RECOVERED, skipped=...)`` — so the caller
        completes the job — when a *non-overlay* (upstream) revision from the source's
        ``url`` is served, meaning the supersede's durable effect already landed, and
        finishes the interrupted step through ``_deprecate_superseded_overlay`` (idempotent,
        CAS on CONFIRMED), whose same-API guard decides whether the overlay is actually
        superseded. Returns ``_Recovery.NOT_RECOVERABLE`` for a genuine failure (no
        upstream revision from ``url`` is served, or identity can't be resolved), and
        ``_Recovery.ERRORED`` when the lookup raised so the job is retried.

        Identity is resolved by the source's upstream ``url`` (the catalog ``spec_url``),
        *not* the (vendor, name, version) triple: the production supersede source is a
        ``type:"url"`` payload built by ``CatalogService._to_import_source`` that has no
        ``version`` key (the version isn't known until the spec is fetched). Keying on the
        served revision's ``source_url`` — the same provenance the enqueue-time gate uses
        (``current_revision_for_source_url``) — is what lets this fire on the real path.

        Several API rows can serve the same ``url`` (a vendor-doubled row and its clean
        identity, #1078), so the lookup is scoped to the overlay's own API row first: if
        that row serves the fresh upstream, the supersede landed there and the overlay is
        deprecated. Only otherwise does it fall back to the (deterministic, oldest-first)
        unscoped lookup — the fresh upstream landed on a different row, the job's effect
        still holds, and the guard skips the deprecate as ``api_mismatch``.
        """
        src = source if isinstance(source, dict) else {}
        url = src.get("url")
        if not isinstance(url, str):
            return _SupersedeRecovery(outcome=_Recovery.NOT_RECOVERABLE)
        try:
            async with self._ctx.registry_db.session() as session:
                overlay = await OverlayRepository.get_by_id(session, overlay_id)
                current = None
                if overlay is not None:
                    current = await ApiRevisionRepository.current_revision_for_source_url(
                        session, url, api_id=overlay.api_id, exclude_origin=ORIGIN_OVERLAY
                    )
                if current is None:
                    current = await ApiRevisionRepository.current_revision_for_source_url(
                        session, url, exclude_origin=ORIGIN_OVERLAY
                    )
                # The supersede's durable effect is "served revision is the fresh upstream"
                # (non-overlay). If no API serves a non-overlay revision from ``url``, the
                # prior attempt did not actually supersede — this is a real duplicate failure.
                if current is None:
                    return _SupersedeRecovery(outcome=_Recovery.NOT_RECOVERABLE)
                api_id, current_revision_id = current
            logger.info(
                "overlay_supersede_recovered_existing_revision",
                job_id=job_id,
                overlay_id=overlay_id,
                api_id=str(api_id),
                revision_id=str(current_revision_id),
            )
        except Exception:
            logger.exception(
                "overlay_supersede_recovery_failed", job_id=job_id, overlay_id=overlay_id
            )
            return _SupersedeRecovery(outcome=_Recovery.ERRORED)
        # Finish the interrupted deprecate (idempotent: CAS on CONFIRMED — a no-op if a
        # prior attempt already demoted it). The same-API guard runs here too, keyed on the
        # API row that serves the fresh upstream: a different row skips as ``api_mismatch``,
        # and an overlay whose revision is still active skips as ``revision_still_served``.
        #
        # Residual double fault (#1810): this recovery path passes no
        # ``superseding_revision``, so ``_deprecate_superseded_overlay`` cannot re-resolve
        # and checks the enqueue-time ``overlay_id`` against the guard. Under a *concurrent
        # confirm that changed the live overlay after enqueue* AND a crash mid-supersede,
        # that id can be stale. We do not re-resolve because by recovery time the archived
        # overlay revision is no longer served, so there is no reliable
        # ``superseded_revision_id`` to key on. The guard and CAS still limit the demote to
        # a CONFIRMED overlay on the serving API row whose revision is no longer active, so
        # state is never corrupted; the narrow cost is a possibly-wrong-overlay demote in
        # this double-fault corner. Left as a known residual rather than adding a heuristic
        # that could misfire.
        skipped = await self._deprecate_superseded_overlay(
            job_id,
            overlay_id,
            superseding_api_id=api_id,
            actor_id=actor_id,
            actor_type=actor_type,
        )
        return _SupersedeRecovery(outcome=_Recovery.RECOVERED, skipped=skipped)

    async def _recover_overlay_link(self, job_id: str, overlay_id: str, source: Any) -> _Recovery:
        """Link an overlay to its already-materialized revision after a duplicate re-ingest.

        Returns ``_Recovery.RECOVERED`` if the re-ingest failed *only* because the
        overlaid content already exists and the current revision is the overlay revision
        we linked (so the job is a successful no-op recovery),
        ``_Recovery.NOT_RECOVERABLE`` for a genuine failure that should still surface, and
        ``_Recovery.ERRORED`` when the lookup or the link raised, so the job is retried
        and the recovery runs again.
        """
        src = source if isinstance(source, dict) else {}
        vendor = src.get("vendor")
        name = src.get("api_name")
        version = src.get("version")
        if not (isinstance(vendor, str) and isinstance(name, str) and isinstance(version, str)):
            return _Recovery.NOT_RECOVERABLE
        try:
            async with self._ctx.registry_db.transaction() as session:
                api = await ApiRepository.get_by_identifier_with_current_revision(
                    session, vendor, name, version
                )
                if api is None or api.current_revision is None:
                    return _Recovery.NOT_RECOVERABLE
                # Only claim recovery when the live revision is the overlaid one; a
                # non-overlay current revision means the duplicate was something else.
                if api.current_revision.origin != ORIGIN_OVERLAY:
                    return _Recovery.NOT_RECOVERABLE
                assert api.current_revision_id is not None
                # Reconstruct the revision this materialize superseded. The original
                # confirm archived it before crashing pre-link, so it isn't in memory
                # here — recover it as the newest archived non-overlay revision. Passed
                # best-effort: set_confirmed_revision won't clobber an already-captured
                # value, and None (first-ever materialize) leaves it NULL as intended.
                superseded_id = await ApiRevisionRepository.latest_archived_non_overlay(
                    session, api.id, ORIGIN_OVERLAY
                )
                updated = await OverlayRepository.set_confirmed_revision(
                    session,
                    overlay_id,
                    api.current_revision_id,
                    superseded_revision_id=superseded_id,
                )
            logger.info(
                "overlay_materialize_recovered_existing_revision",
                job_id=job_id,
                overlay_id=overlay_id,
                revision_id=str(api.current_revision_id),
                superseded_revision_id=str(superseded_id) if superseded_id else None,
                linked=updated > 0,
            )
            return _Recovery.RECOVERED if updated > 0 else _Recovery.NOT_RECOVERABLE
        except Exception:
            logger.exception(
                "overlay_materialize_recovery_failed", job_id=job_id, overlay_id=overlay_id
            )
            return _Recovery.ERRORED
