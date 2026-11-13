"""Import job handler — processes API import sources.

This handler is registered with the WorkerLoop for kind=import jobs.
It fetches/parses OpenAPI/Arazzo sources and creates draft ApiRevisions.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import structlog
from pydantic import TypeAdapter, ValidationError
from structlog.contextvars import bind_contextvars, unbind_contextvars

from jentic_one.registry.ingest.errors import (
    BaseIngestError,
    CatalogIdentityConflictError,
    DuplicateRevisionError,
    HostOwnedByOtherVendorIngestError,
    IngestJobError,
    NonRetryableIngestJobError,
    RelativeServerUrlError,
    RetryableFetchError,
    UnexpectedStageError,
)
from jentic_one.registry.ingest.fetch import IngestSource, load_specification
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.repos.revision_repo import (
    is_duplicate_digest_conflict,
    is_duplicate_revision_conflict,
)
from jentic_one.registry.services.errors import RegistryServiceError
from jentic_one.registry.services.import_overlay import (
    SupersedeSkipReason as SupersedeSkipReason,
)
from jentic_one.registry.services.import_overlay import (
    _OverlayReconciliationMixin,
    _Recovery,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseIntegrityError
from jentic_one.shared.db.protocols import TransactionalDatabase
from jentic_one.shared.discovery.errors import DiscoveryError
from jentic_one.shared.jobs.handlers import JobResultPayload
from jentic_one.shared.models import ActorType

logger = structlog.get_logger(__name__)

_source_adapter: TypeAdapter[IngestSource] = TypeAdapter(IngestSource)

_UNEXPECTED_SOURCE_ERROR = "import failed unexpectedly"

# The job-error wrapper an all-sources-failed import rides (``IngestJobError``):
# ``ALL_SOURCES_FAILED_PREFIX_TEMPLATE`` heads the whole message and
# ``SOURCE_FAILURE_PREFIX_TEMPLATE`` heads each per-source entry. Named (rather
# than inline f-strings) because the rendering is a cross-module contract: the
# /mcp mount's duplicate-content detection (``jentic_one.mcp.tools``) keys on
# the single-source rendering surviving the worker's 128-char ``job.error``
# truncation, and its tests build fixtures from these exact templates. Keep the
# rendering byte-identical when touching them — a wrapper that grows can push
# the duplicate fragment past the truncation point in production.
SOURCE_FAILURE_PREFIX_TEMPLATE = "source[{index}]: "
ALL_SOURCES_FAILED_PREFIX_TEMPLATE = "all {count} import source(s) failed: "


# The readable ``job.error`` entry for a lost race for the API's one active
# revision slot (``ix_api_revisions_one_active``). It deliberately does not
# carry the duplicate-content wording: the competing active revision can hold
# different content, so /mcp must not report the spec as already imported.
ACTIVE_REVISION_CONFLICT_ERROR = "another revision of this API version is already active"

# Bounds the exception-chain walk in _exception_chain.
_MAX_CHAIN_DEPTH = 8


def _exception_chain(exc: BaseException) -> list[BaseException]:
    """``exc`` and the exceptions it was raised from or while handling, outermost first.

    Follows ``__cause__``, then an unsuppressed ``__context__`` (an exception
    raised inside an ``except`` block without ``from``), as a rendered
    traceback does. Bounded and cycle-safe.
    """
    chain: list[BaseException] = []
    seen: set[int] = set()
    link: BaseException | None = exc
    while link is not None and id(link) not in seen and len(chain) < _MAX_CHAIN_DEPTH:
        seen.add(id(link))
        chain.append(link)
        if link.__cause__ is not None:
            link = link.__cause__
        elif not link.__suppress_context__:
            link = link.__context__
        else:
            link = None
    return chain


def _is_active_revision_conflict(exc: BaseException) -> bool:
    """True when the failure is a lost race for the one-active revision slot.

    The integrity error can arrive raw (at commit) or under the
    ``DuplicateRevisionError`` the revision stage raises for it.
    """
    return any(
        isinstance(link, DatabaseIntegrityError)
        and is_duplicate_revision_conflict(link)
        and not is_duplicate_digest_conflict(link)
        for link in _exception_chain(exc)
    )


def _readable_source_error(exc: Exception) -> str:
    """Map a source-level ingest failure to a message safe to show a user.

    The result lands in ``Job.error`` and event summaries, so only deliberate,
    operator-facing messages pass through: ingest/registry domain errors and
    source-payload validation errors. A lost race for the one-active revision
    slot reads as :data:`ACTIVE_REVISION_CONFLICT_ERROR`; an identical-content
    collision surfacing as a raw integrity error at commit reads as the
    duplicate message. Anything else — database/driver errors, unexpected
    internals — maps to a generic message so SQL or driver text never reaches
    the job record; the detail is logged by the caller (``import_source_failed``).
    """
    if _is_active_revision_conflict(exc):
        return ACTIVE_REVISION_CONFLICT_ERROR
    if isinstance(exc, DatabaseIntegrityError) and is_duplicate_digest_conflict(exc):
        return DuplicateRevisionError().message
    if isinstance(exc, BaseIngestError | RegistryServiceError | DiscoveryError | ValidationError):
        return str(exc)
    return _UNEXPECTED_SOURCE_ERROR


# Ingest refusals that keep their meaning however they were raised (some chain
# the repository error that detected them): the stored state or the document
# decides them, so a retry meets the same refusal.
_DETERMINISTIC_INGEST_ERRORS: tuple[type[BaseIngestError], ...] = (
    DuplicateRevisionError,
    CatalogIdentityConflictError,
    HostOwnedByOtherVendorIngestError,
    RelativeServerUrlError,
)
# Ingest errors that report a failure a later attempt may not repeat.
_RETRYABLE_INGEST_ERRORS: tuple[type[BaseIngestError], ...] = (
    RetryableFetchError,
    UnexpectedStageError,
)


class _SourcePhase(StrEnum):
    """Which step of importing one source was running when it failed."""

    VALIDATE = "validate"
    LOAD = "load"
    INGEST = "ingest"


def _is_deterministic_source_failure(exc: Exception, *, phase: _SourcePhase) -> bool:
    """True when a source failed for a reason no retry of the job can change.

    Deterministic:

    * the source payload fails its model (``ValidationError``);
    * loading the source raises an ingest error other than
      :class:`RetryableFetchError`: an unsafe URL, too many redirects, a
      non-transient 4xx, an oversized document, unparseable content, or an
      unresolvable API identity;
    * the ingest pipeline refuses the spec: identical content already stored
      (also the raw digest integrity error of a lost race), a catalog identity
      conflict, a host another vendor serves, an unusable server URL, or any
      ingest error a stage raises itself, with no underlying exception.

    Everything else stays retryable: database and driver errors, network
    errors and transient upstream statuses, a lost race for the one-active
    revision slot, and :class:`UnexpectedStageError` (the "Stage '…' failed
    unexpectedly" wrapper), as does an ingest error raised from, or while
    handling, a non-ingest exception.
    """
    if phase == _SourcePhase.VALIDATE:
        return isinstance(exc, ValidationError)
    if phase == _SourcePhase.LOAD:
        return isinstance(exc, BaseIngestError) and not isinstance(exc, RetryableFetchError)
    if _is_active_revision_conflict(exc):
        return False
    if isinstance(exc, DatabaseIntegrityError):
        return is_duplicate_digest_conflict(exc)
    for link in _exception_chain(exc):
        if isinstance(link, _RETRYABLE_INGEST_ERRORS):
            return False
        if isinstance(link, _DETERMINISTIC_INGEST_ERRORS):
            return True
        if not isinstance(link, BaseIngestError):
            return False
    # Every link is an ingest error; the innermost was raised on purpose.
    return True


class ImportHandler(_OverlayReconciliationMixin):
    """Handles kind=import jobs by processing API sources."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def execute(
        self,
        job_id: str,
        db: TransactionalDatabase | None,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        """Process import sources and create draft ApiRevisions.

        ``db`` is the worker's database, unused here: the import writes through
        the ``Context`` databases in its own transactions.
        """
        bind_contextvars(job_id=job_id)
        if created_by is None:
            raise NonRetryableIngestJobError(
                "import job is missing the triggering actor (created_by)"
            )
        try:
            payload = payload or {}
            sources = payload.get("sources", [])
            overlay_id = payload.get("overlay_id")
            supersede_overlay_id = payload.get("supersede_overlay_id")
            # Opaque string, not ``ActorType(...)``: a job enqueued on 0.40 by a
            # since-retired service account carries ``actor_type='service_account'``
            # and must still import (the audit row keeps the historical label).
            resolved_actor_type: str = actor_type or ActorType.USER.value
            revisions: list[dict[str, Any]] = []
            failures: list[str] = []
            # Whether every failure so far is one a retry cannot change.
            all_failures_deterministic = True
            recovered_overlay_link = False

            logger.info("import_handler_start", source_count=len(sources), job_id=job_id)

            for idx, src in enumerate(sources):
                phase = _SourcePhase.VALIDATE
                try:
                    source = _source_adapter.validate_python(src)
                    phase = _SourcePhase.LOAD
                    spec = await load_specification(source, config=self._ctx.config.ingest)
                    phase = _SourcePhase.INGEST
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
                    held = result.held_host_change
                    if held is not None:
                        # Server-host change guard: kept as a draft for an organisation
                        # admin to review.
                        revisions[-1]["held_for_review"] = True
                        revisions[-1]["host_change"] = held.model_dump()
                    if result.server_url_changes is not None:
                        revisions[-1]["server_url_changes"] = result.server_url_changes.model_dump()
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
                            **({"host_change": held.model_dump()} if held is not None else {}),
                        },
                        reason="server_host_change_held" if held is not None else None,
                        origin=None,
                    )
                except Exception as exc:
                    readable_error = _readable_source_error(exc)
                    if _is_deterministic_source_failure(exc, phase=phase):
                        # A refused spec or payload is the caller's to fix, not
                        # a system fault: no traceback.
                        logger.warning(
                            "import_source_failed",
                            source_index=idx,
                            job_id=job_id,
                            retryable=False,
                            error_type=type(exc).__name__,
                            error=readable_error,
                        )
                    elif isinstance(exc, RetryableFetchError) or _is_active_revision_conflict(exc):
                        # An expected transient condition: the job is retried.
                        all_failures_deterministic = False
                        logger.warning(
                            "import_source_failed",
                            source_index=idx,
                            job_id=job_id,
                            retryable=True,
                            error_type=type(exc).__name__,
                            error=readable_error,
                        )
                    else:
                        all_failures_deterministic = False
                        logger.exception(
                            "import_source_failed", source_index=idx, job_id=job_id, retryable=True
                        )
                    failures.append(
                        SOURCE_FAILURE_PREFIX_TEMPLATE.format(index=idx) + readable_error
                    )

            logger.info(
                "import_handler_complete",
                job_id=job_id,
                total=len(sources),
                succeeded=len(revisions),
                failed=len(failures),
            )

            # A4b worker step: an authorized catalog re-import that supersedes a live
            # confirmed overlay. When the re-ingest archived the overlay's revision (the
            # supersede_active stage archives *all* active revisions of the API row it
            # lands on), the served spec is the fresh upstream and the overlay is
            # auto-deprecated so its lifecycle reflects reality — unless the same-API
            # guard finds the re-import did not supersede it (#1078; see
            # _deprecate_superseded_overlay), in which case the overlay stays confirmed
            # and the job result records ``supersede_skipped``. Two cases reach a correct
            # end state:
            #   - clean first run: the fresh revision was created this attempt; deprecate.
            #   - duplicate-on-retry: a prior attempt already committed the fresh revision
            #     and made it current, then crashed *before* the (separate-transaction)
            #     deprecate. The retry re-ingests identical content → DuplicateRevisionError
            #     → no new revision, a failure. Without recovery the deprecate is skipped
            #     and the job dead-letters, leaving the overlay stuck CONFIRMED over an
            #     archived revision. So treat "served spec is already the fresh upstream"
            #     as success and (idempotently, CAS on CONFIRMED) deprecate + settle.
            recovered_supersede = False
            supersede_skipped: SupersedeSkipReason | None = None
            if supersede_overlay_id:
                if any(rev.get("held_for_review") for rev in revisions):
                    # The upstream revision was held for review, so nothing was archived
                    # and the overlay is still served: leave it confirmed.
                    logger.info(
                        "overlay_supersede_skipped_host_change_held",
                        job_id=job_id,
                        overlay_id=supersede_overlay_id,
                    )
                elif revisions and not failures:
                    skipped_reason = await self._deprecate_superseded_overlay(
                        job_id,
                        str(supersede_overlay_id),
                        superseding_revision=revisions[0],
                        actor_id=created_by,
                        actor_type=actor_type,
                    )
                    if skipped_reason is not None:
                        revisions[0]["supersede_skipped"] = skipped_reason.value
                elif not revisions and len(sources) == 1:
                    supersede_recovery = await self._recover_supersede(
                        job_id,
                        str(supersede_overlay_id),
                        sources[0],
                        actor_id=created_by,
                        actor_type=actor_type,
                    )
                    recovered_supersede = supersede_recovery.outcome == _Recovery.RECOVERED
                    if supersede_recovery.outcome == _Recovery.RECOVERED:
                        supersede_skipped = supersede_recovery.skipped
                    elif supersede_recovery.outcome == _Recovery.ERRORED:
                        all_failures_deterministic = False

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
                    link_recovery = await self._recover_overlay_link(
                        job_id, str(overlay_id), sources[0]
                    )
                    recovered_overlay_link = link_recovery == _Recovery.RECOVERED
                    if link_recovery == _Recovery.ERRORED:
                        all_failures_deterministic = False
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
            # served; the overlay is deprecated unless the same-API guard skips) — see
            # _recover_supersede. Both recoveries run above, on the attempt that hit the
            # duplicate, so a deterministic duplicate is only failed once recovery has had
            # its chance. A recovery that raised (``_Recovery.ERRORED``) keeps the job
            # retryable, so the retry runs the recovery again.
            #
            # When every failure is deterministic the job fails without a retry
            # (NonRetryableIngestJobError); one retryable failure keeps the
            # worker's backoff budget for the whole job. The message is the same
            # either way.
            if (
                failures
                and not revisions
                and not recovered_overlay_link
                and not recovered_supersede
            ):
                message = ALL_SOURCES_FAILED_PREFIX_TEMPLATE.format(count=len(sources)) + "; ".join(
                    failures
                )
                if all_failures_deterministic:
                    raise NonRetryableIngestJobError(message)
                raise IngestJobError(message)

            body: dict[str, Any] = {"revisions": revisions}
            if supersede_skipped is not None:
                # The recovery path has no revision entry to carry the skip.
                body["supersede_skipped"] = supersede_skipped.value
            return JobResultPayload(
                body=body,
                content_type=None,
            )
        finally:
            unbind_contextvars("job_id")
