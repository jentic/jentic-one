"""Import job handler — processes API import sources.

This handler is registered with the WorkerLoop for kind=import jobs.
It fetches/parses OpenAPI/Arazzo sources and creates draft ApiRevisions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import TypeAdapter
from structlog.contextvars import bind_contextvars, unbind_contextvars

from jentic_one.registry.ingest.exc import DuplicateRevisionError, IngestJobError
from jentic_one.registry.ingest.fetch import IngestSource, load_specification
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.overlay_repo import OverlayRepository
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseIntegrityError
from jentic_one.shared.events import settle_actionable_events
from jentic_one.shared.jobs.handlers import JobResultPayload
from jentic_one.shared.models import ORIGIN_OVERLAY, ActorType, OverlayStatus
from jentic_one.shared.models.events import EventType

logger = structlog.get_logger(__name__)

_source_adapter: TypeAdapter[IngestSource] = TypeAdapter(IngestSource)

# The unique constraint that guards one revision per (api_id, spec_digest).
_DIGEST_CONSTRAINT = "uq_api_revisions_api_id_spec_digest"


def _readable_source_error(exc: Exception) -> str:
    """Map a source-level ingest failure to a message safe to show a user.

    Raw ``IntegrityError`` strings leak SQL and get truncated mid-word in the
    job record; translate the duplicate-digest collision to a clear message and
    keep other failures as their (domain) exception text.
    """
    if isinstance(exc, DatabaseIntegrityError) and _DIGEST_CONSTRAINT in exc.detail:
        return DuplicateRevisionError().message
    return str(exc)


class ImportHandler:
    """Handles kind=import jobs by processing API sources."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        """Process import sources and create draft ApiRevisions."""
        bind_contextvars(job_id=job_id)
        if created_by is None:
            raise IngestJobError("import job is missing the triggering actor (created_by)")
        try:
            payload = payload or {}
            sources = payload.get("sources", [])
            overlay_id = payload.get("overlay_id")
            supersede_overlay_id = payload.get("supersede_overlay_id")
            resolved_actor_type = ActorType(actor_type) if actor_type else ActorType.USER
            revisions: list[dict[str, Any]] = []
            failures: list[str] = []
            recovered_overlay_link = False

            logger.info("import_handler_start", source_count=len(sources), job_id=job_id)

            for idx, src in enumerate(sources):
                try:
                    source = _source_adapter.validate_python(src)
                    spec = await load_specification(source, config=self._ctx.config.ingest)
                    result = await Ingestor(self._ctx).ingest(spec, created_by=created_by)
                    revisions.append(
                        {
                            "api": {
                                "vendor": result.api_vendor,
                                "name": result.api_name,
                                "version": result.api_version,
                            },
                            "revision_id": str(result.revision_id),
                            "superseded_revision_id": (
                                str(result.superseded_revision_id)
                                if result.superseded_revision_id is not None
                                else None
                            ),
                            "state": result.state,
                        }
                    )
                    await record_audit_best_effort(
                        self._ctx,
                        action=AuditAction.CREATE,
                        target_type=AuditTargetType.REVISION,
                        target_id=str(result.revision_id),
                        actor_type=resolved_actor_type,
                        actor_id=created_by,
                        job_id=job_id,
                        after={
                            "vendor": result.api_vendor,
                            "name": result.api_name,
                            "version": result.api_version,
                            "state": result.state,
                        },
                        origin=None,
                    )
                except Exception as exc:
                    logger.exception("import_source_failed", source_index=idx, job_id=job_id)
                    failures.append(f"source[{idx}]: {_readable_source_error(exc)}")

            logger.info(
                "import_handler_complete",
                job_id=job_id,
                total=len(sources),
                succeeded=len(revisions),
                failed=len(failures),
            )

            # Flow-3 resolve path: a successful (re-)import adopts the upstream spec, so
            # any outstanding ``catalog.update_available`` prompt for that API is now
            # resolved — settle it best-effort so the action-inbox item clears. Keyed on
            # the event payload's ``api_id``; a manual import that was never catalog-
            # tracked simply has no matching event. Never fails the import.
            for rev in revisions:
                await self._settle_update_available(job_id, created_by, rev["api"], session)

            # A4b worker step: an authorized catalog re-import that supersedes a live
            # confirmed overlay. The re-ingest archived the overlay's revision (the
            # supersede_active stage archived *all* active revisions), so the served spec
            # is now the fresh upstream. Auto-deprecate the overlay so its lifecycle
            # reflects reality. Two cases reach a correct end state:
            #   - clean first run: the fresh revision was created this attempt; deprecate.
            #   - duplicate-on-retry: a prior attempt already committed the fresh revision
            #     and made it current, then crashed *before* the (separate-transaction)
            #     deprecate. The retry re-ingests identical content → DuplicateRevisionError
            #     → no new revision, a failure. Without recovery the deprecate is skipped
            #     and the job dead-letters, leaving the overlay stuck CONFIRMED over an
            #     archived revision. So treat "served spec is already the fresh upstream"
            #     as success and (idempotently, CAS on CONFIRMED) deprecate + settle.
            recovered_supersede = False
            if supersede_overlay_id:
                if revisions and not failures:
                    await self._deprecate_superseded_overlay(job_id, str(supersede_overlay_id))
                elif not revisions and len(sources) == 1:
                    recovered_api_id = await self._recover_supersede(
                        job_id, str(supersede_overlay_id), sources[0]
                    )
                    recovered_supersede = recovered_api_id is not None
                    if recovered_api_id is not None:
                        # The served spec is already the fresh upstream; settle the Flow-3
                        # prompts the (now-durable) adoption resolved, mirroring the clean
                        # path. Keyed on the resolved api_id — the URL source carries no
                        # version triple to re-resolve from.
                        await self._settle_events_for_api_id(
                            job_id, created_by, recovered_api_id, session
                        )

            # Overlay materialization: link the confirmed overlay to the revision the
            # re-ingest just produced, so the served spec and the overlay agree on which
            # revision embodies the applied change. ``overlay_id`` is set only by
            # OverlayService (a materialize job), which always carries exactly one
            # overlay-origin source — so its presence, not the revision count, is the
            # signal. Guard the single-source invariant and skip if it was violated or
            # the source failed, rather than linking the wrong revision.
            if overlay_id:
                if len(revisions) == 1 and not failures:
                    await self._link_overlay_revision(
                        job_id,
                        str(overlay_id),
                        revisions[0]["revision_id"],
                        revisions[0].get("superseded_revision_id"),
                    )
                elif not revisions and len(sources) == 1:
                    # Recovery: a prior attempt of this exact confirm already produced
                    # the overlaid revision, so the re-ingest failed only because the
                    # identical content already exists (DuplicateRevisionError). The
                    # existing active revision *is* the materialization we want linked —
                    # resolve it by digest and back-link, so H2 recovery completes even
                    # when the revision (not just the link) already landed.
                    recovered_overlay_link = await self._recover_overlay_link(
                        job_id, str(overlay_id), sources[0]
                    )
                else:
                    logger.warning(
                        "overlay_materialize_unexpected_result",
                        job_id=job_id,
                        overlay_id=overlay_id,
                        succeeded=len(revisions),
                        failed=len(failures),
                    )

            # If every source failed, surface it as a failed job rather than
            # masking it behind a "completed" status with an empty result.
            # Each source runs in its own transaction (see Ingestor.ingest), so
            # when nothing succeeded there is no committed work to preserve by
            # returning a "completed" result.
            #
            # Exception: an overlay recovery job whose only failure was "identical
            # content already exists" *did* achieve its goal (the revision exists and
            # we linked it above) — treat it as success, not a failed job. Same for a
            # supersede re-import recovered on duplicate-retry (fresh upstream already
            # served, overlay deprecated) — see _recover_supersede.
            if (
                failures
                and not revisions
                and not recovered_overlay_link
                and not recovered_supersede
            ):
                raise IngestJobError(
                    f"all {len(sources)} import source(s) failed: " + "; ".join(failures)
                )

            return JobResultPayload(
                body={"revisions": revisions},
                content_type=None,
            )
        finally:
            unbind_contextvars("job_id")

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

    async def _deprecate_superseded_overlay(self, job_id: str, overlay_id: str) -> None:
        """Auto-deprecate an overlay superseded by an authorized catalog re-import (A4b).

        The re-ingest already archived the overlay's materialized revision and made the
        fresh upstream the served spec; this only flips the overlay's own lifecycle to
        DEPRECATED (CAS on it still being CONFIRMED) so status reflects reality. Runs in
        its own registry_db transaction — a failure here does not undo the durable
        re-ingest, so it is logged rather than raised (the served spec is already correct;
        only the overlay's status label lags and can be repaired).
        """
        try:
            async with self._ctx.registry_db.transaction() as session:
                demoted = await OverlayRepository.set_status(
                    session,
                    overlay_id,
                    OverlayStatus.DEPRECATED,
                    deprecated_at=datetime.now(UTC),
                    expected_status=OverlayStatus.CONFIRMED,
                )
            if demoted == 0:
                logger.warning(
                    "overlay_supersede_not_deprecated",
                    job_id=job_id,
                    overlay_id=overlay_id,
                    reason="overlay_not_confirmed_or_missing",
                )
        except Exception:
            logger.exception(
                "overlay_supersede_deprecate_failed",
                job_id=job_id,
                overlay_id=overlay_id,
            )

    async def _recover_supersede(
        self, job_id: str, overlay_id: str, source: Any
    ) -> uuid.UUID | None:
        """Recover an interrupted A4b supersede after a duplicate-on-retry re-ingest.

        Mirrors ``_recover_overlay_link`` for the supersede path. A prior attempt already
        committed the fresh upstream revision and made it current, then crashed before the
        separate-transaction deprecate ran; the retry re-ingests identical content and
        fails with ``DuplicateRevisionError`` (no new revision). Returns the resolved
        ``api_id`` — so the caller completes the job (instead of dead-lettering) and settles
        events — when the served revision is now a *non-overlay* (upstream) revision,
        meaning the supersede's durable effect already landed. In that case it also
        (idempotently, CAS on CONFIRMED) deprecates the overlay to finish the interrupted
        step. Returns ``None`` for a genuine failure (served revision is still the overlay,
        or identity can't be resolved).

        Identity is resolved by the source's upstream ``url`` (the catalog ``spec_url``),
        *not* the (vendor, name, version) triple: the production supersede source is a
        ``type:"url"`` payload built by ``CatalogService._to_import_source`` that has no
        ``version`` key (the version isn't known until the spec is fetched). Keying on the
        served revision's ``source_url`` — the same provenance the enqueue-time gate uses
        (``current_revision_for_source_url``) — is what lets this fire on the real path.
        """
        src = source if isinstance(source, dict) else {}
        url = src.get("url")
        if not isinstance(url, str):
            return None
        try:
            async with self._ctx.registry_db.session() as session:
                current = await ApiRevisionRepository.current_revision_for_source_url(session, url)
                if current is None:
                    return None
                api_id, current_revision_id = current
                origin = await ApiRevisionRepository.origin_of(session, current_revision_id)
                if origin is None:
                    return None
                # The supersede's durable effect is "served revision is the fresh upstream"
                # (non-overlay). If the current revision is still the overlay, the prior
                # attempt did not actually supersede — this is a real duplicate failure.
                if origin == ORIGIN_OVERLAY:
                    return None
            logger.info(
                "overlay_supersede_recovered_existing_revision",
                job_id=job_id,
                overlay_id=overlay_id,
                revision_id=str(current_revision_id),
            )
        except Exception:
            logger.exception(
                "overlay_supersede_recovery_failed", job_id=job_id, overlay_id=overlay_id
            )
            return None
        # Finish the interrupted deprecate (idempotent: CAS on CONFIRMED — a no-op if a
        # prior attempt already demoted it).
        await self._deprecate_superseded_overlay(job_id, overlay_id)
        return api_id

    async def _settle_update_available(
        self, job_id: str, actor_id: str, api: dict[str, Any], session: Any
    ) -> None:
        """Clear outstanding Flow-3 update prompts for the (re-)imported API.

        A successful import adopts the upstream spec, so both the routine
        ``catalog.update_available`` prompt **and** any ``catalog.update_conflicts_overlay``
        prompt (A4c — the operator adopted upstream over an overlay) are resolved for that
        API. Best-effort: resolve the local ``api_id`` from the spec triple, then
        acknowledge matching actionable events of either class (matched on the event
        payload's ``api_id``). Never fails the import — the served spec is already correct;
        a missed settle only leaves a stale inbox item that the next re-import clears.

        ``session`` is the handler's own jobs/admin write session (events live in the admin
        DB). We deliberately reuse it rather than opening a second admin transaction: the
        worker already runs the handler inside an admin ``BEGIN IMMEDIATE`` (see
        ``JobWorker._execute_handler``), and on SQLite's single writer a nested admin
        transaction would deadlock against that outer one — no retry can win because the
        blocker is our own call stack. Reusing the session also makes the ack atomic with
        the import. Each settle runs in a SAVEPOINT so a failure rolls back only itself,
        leaving the surrounding import (and its completion event) intact.
        """
        vendor, name, version = api.get("vendor"), api.get("name"), api.get("version")
        if not (isinstance(vendor, str) and isinstance(name, str) and isinstance(version, str)):
            return
        try:
            async with self._ctx.registry_db.session() as read_session:
                resolved = await ApiRepository.get_by_identifier(
                    read_session, vendor, name, version
                )
            if resolved is None:
                return
        except Exception:
            logger.exception("catalog_update_available_settle_failed", job_id=job_id)
            return
        await self._settle_events_for_api_id(job_id, actor_id, resolved.id, session)

    async def _settle_events_for_api_id(
        self, job_id: str, actor_id: str, api_id: uuid.UUID, session: Any
    ) -> None:
        """Ack both Flow-3 update event classes for a resolved ``api_id``.

        Split from ``_settle_update_available`` so callers that already hold the resolved
        ``api_id`` (e.g. the supersede-recovery path, whose URL source carries no version
        triple to re-resolve) can settle directly. Per-type isolation: settle each event
        class in its own SAVEPOINT + try, so a failure settling one class (e.g. the plain
        update) does not skip the other — for the A4c adopt-over-overlay case the conflict
        event is the one that most matters, and it must be acked even if the plain-update
        settle errors first.
        """
        for event_type in (
            EventType.CATALOG_UPDATE_AVAILABLE,
            EventType.CATALOG_UPDATE_CONFLICTS_OVERLAY,
        ):
            try:
                async with session.begin_nested():
                    settled = await settle_actionable_events(
                        session,
                        event_type=event_type,
                        acknowledged_by=actor_id,
                        acknowledgement_note="Resolved by re-import of the upstream spec",
                        data_match={"api_id": str(api_id)},
                    )
                if settled:
                    logger.info(
                        "catalog_update_event_settled",
                        job_id=job_id,
                        api_id=str(api_id),
                        event_type=event_type,
                        settled=settled,
                    )
            except Exception:
                logger.exception(
                    "catalog_update_event_settle_failed",
                    job_id=job_id,
                    event_type=event_type,
                )

    async def _recover_overlay_link(self, job_id: str, overlay_id: str, source: Any) -> bool:
        """Link an overlay to its already-materialized revision after a duplicate re-ingest.

        Returns ``True`` if the re-ingest failed *only* because the overlaid content
        already exists and the current revision is the overlay revision we linked
        (so the job is a successful no-op recovery), else ``False`` (a genuine failure
        that should still surface). Best-effort like ``_link_overlay_revision``.
        """
        src = source if isinstance(source, dict) else {}
        vendor = src.get("vendor")
        name = src.get("api_name")
        version = src.get("version")
        if not (isinstance(vendor, str) and isinstance(name, str) and isinstance(version, str)):
            return False
        try:
            async with self._ctx.registry_db.transaction() as session:
                api = await ApiRepository.get_by_identifier_with_current_revision(
                    session, vendor, name, version
                )
                if api is None or api.current_revision is None:
                    return False
                # Only claim recovery when the live revision is the overlaid one; a
                # non-overlay current revision means the duplicate was something else.
                if api.current_revision.origin != ORIGIN_OVERLAY:
                    return False
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
            return updated > 0
        except Exception:
            logger.exception(
                "overlay_materialize_recovery_failed", job_id=job_id, overlay_id=overlay_id
            )
            return False
