"""Import job handler — processes API import sources.

This handler is registered with the WorkerLoop for kind=import jobs.
It fetches/parses OpenAPI/Arazzo sources and creates draft ApiRevisions.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from pydantic import TypeAdapter
from structlog.contextvars import bind_contextvars, unbind_contextvars

from jentic_one.registry.ingest.exc import DuplicateRevisionError, IngestJobError
from jentic_one.registry.ingest.fetch import IngestSource, load_specification
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.overlay_repo import OverlayRepository
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseIntegrityError
from jentic_one.shared.events import settle_actionable_events
from jentic_one.shared.jobs.handlers import JobResultPayload
from jentic_one.shared.models import ORIGIN_OVERLAY, ActorType
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
            # we linked it above) — treat it as success, not a failed job.
            if failures and not revisions and not recovered_overlay_link:
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

    async def _settle_update_available(
        self, job_id: str, actor_id: str, api: dict[str, Any], session: Any
    ) -> None:
        """Clear any outstanding ``catalog.update_available`` for the (re-)imported API.

        A successful import adopts the upstream spec, so the Flow-3 update prompt for that
        API is resolved. Best-effort: resolve the local ``api_id`` from the spec triple,
        then acknowledge matching actionable events (matched on the event payload's
        ``api_id``). Never fails the import — the served spec is already correct; a missed
        settle only leaves a stale inbox item that the next re-import clears.

        ``session`` is the handler's own jobs/admin write session (events live in the admin
        DB). We deliberately reuse it rather than opening a second admin transaction: the
        worker already runs the handler inside an admin ``BEGIN IMMEDIATE`` (see
        ``JobWorker._execute_handler``), and on SQLite's single writer a nested admin
        transaction would deadlock against that outer one — no retry can win because the
        blocker is our own call stack. Reusing the session also makes the ack atomic with
        the import. The settle runs in a SAVEPOINT so a failure rolls back only itself,
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
            async with session.begin_nested():
                settled = await settle_actionable_events(
                    session,
                    event_type=EventType.CATALOG_UPDATE_AVAILABLE,
                    acknowledged_by=actor_id,
                    acknowledgement_note="Resolved by re-import of the upstream spec",
                    data_match={"api_id": str(resolved.id)},
                )
            if settled:
                logger.info(
                    "catalog_update_available_settled",
                    job_id=job_id,
                    api_id=str(resolved.id),
                    settled=settled,
                )
        except Exception:
            logger.exception("catalog_update_available_settle_failed", job_id=job_id)

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
                updated = await OverlayRepository.set_confirmed_revision(
                    session, overlay_id, api.current_revision_id
                )
            logger.info(
                "overlay_materialize_recovered_existing_revision",
                job_id=job_id,
                overlay_id=overlay_id,
                revision_id=str(api.current_revision_id),
                linked=updated > 0,
            )
            return updated > 0
        except Exception:
            logger.exception(
                "overlay_materialize_recovery_failed", job_id=job_id, overlay_id=overlay_id
            )
            return False
