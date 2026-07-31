"""API resolution and draft revision creation stages."""

from __future__ import annotations

import uuid
from typing import ClassVar

from jentic_one.registry.ingest.exc import DuplicateRevisionError
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.registry.repos import ApiRepository, ApiRevisionRepository
from jentic_one.shared.models import ORIGIN_OVERLAY, ApiRevisionSourceType


class ResolveApiStage(BasePipelineStage):
    """Resolves or creates the Api entity for this specification."""

    name: ClassVar[str] = "ResolveApiStage"
    _requires: ClassVar[dict[str, type]] = {}
    _produces: ClassVar[dict[str, type]] = {"api_id": uuid.UUID}

    async def _run(self, ctx: PipelineContext) -> None:
        api = await ApiRepository.upsert(
            ctx.session,
            vendor=ctx.specification.api_identifier.vendor,
            name=ctx.specification.api_identifier.name,
            version=ctx.specification.api_identifier.version,
            created_by=ctx.created_by,
            catalog_api_id=ctx.specification.catalog_api_id,
        )
        ctx.produce("api_id", api.id, uuid.UUID)


class CreateRevisionStage(BasePipelineStage):
    """Creates an ApiRevision (draft or imported) for this ingestion."""

    name: ClassVar[str] = "CreateRevisionStage"
    _requires: ClassVar[dict[str, type]] = {"api_id": uuid.UUID}
    # Only ``revision_id`` is a *mandatory* output. This stage also conditionally
    # produces ``superseded_revision_id`` (overlay materialize that replaced a current
    # revision) — deliberately NOT declared here, since ``_produces`` keys are asserted
    # present for every run; the ingestor reads that one via the non-raising ctx.get().
    _produces: ClassVar[dict[str, type]] = {"revision_id": uuid.UUID}

    async def _run(self, ctx: PipelineContext) -> None:
        api_id = ctx.require("api_id", uuid.UUID)
        spec = ctx.specification
        spec_digest = spec.sha or ""
        # A prior import of identical content may have committed a revision that
        # was later abandoned (e.g. a sibling source failed, or a subsequent
        # stage crashed). Re-importing the same (api_id, spec_digest) would then
        # collide with uq_api_revisions_api_id_spec_digest. Replace a leftover
        # replaceable revision (draft/archived) so retries are idempotent.
        await ApiRevisionRepository.delete_replaceable_by_digest(ctx.session, api_id, spec_digest)
        # Anything still sharing the digest is an active (published/imported)
        # revision — a genuine conflict. Surface it as a readable error before we
        # attempt the insert, so callers see a clear message instead of a raw
        # unique-constraint IntegrityError.
        existing = await ApiRevisionRepository.get_by_digest(ctx.session, api_id, spec_digest)
        if existing is not None:
            raise DuplicateRevisionError()
        if spec.origin is not None:
            if spec.origin == ORIGIN_OVERLAY:
                # A materialized overlay must supersede whatever revision is currently
                # served — which may be an IMPORTED revision of a *different* origin
                # (e.g. "catalog") or a manually-promoted PUBLISHED revision. The
                # origin-scoped archive would leave those active and the new imported
                # revision would then violate ix_api_revisions_one_active (one active
                # revision per api). Archive every active revision (published+imported).
                #
                # Capture the revision being superseded (the API's current revision
                # *before* the archive) so the overlay can record it for a later
                # deterministic rollback (A5b). None if the API had no current revision
                # (a first-ever materialize) — the overlay then has no rollback target.
                api = await ApiRepository.get_by_id(ctx.session, api_id)
                if api is not None and api.current_revision_id is not None:
                    ctx.produce("superseded_revision_id", api.current_revision_id, uuid.UUID)
                await ApiRevisionRepository.archive_all_active(ctx.session, api_id)
            else:
                await ApiRevisionRepository.archive_active_imported(
                    ctx.session, api_id, spec.origin
                )
            revision = await ApiRevisionRepository.create_imported(
                ctx.session,
                api_id=api_id,
                origin=spec.origin,
                spec_digest=spec_digest,
                source_type=spec.source_type or ApiRevisionSourceType.UNKNOWN,
                source_url=spec.source_url,
                source_filename=spec.source_filename,
                submitted_by=spec.submitted_by,
                created_by=ctx.created_by,
            )
        else:
            revision = await ApiRevisionRepository.create_draft(
                ctx.session,
                api_id=api_id,
                spec_digest=spec_digest,
                source_type=spec.source_type or ApiRevisionSourceType.UNKNOWN,
                source_url=spec.source_url,
                source_filename=spec.source_filename,
                submitted_by=spec.submitted_by,
                created_by=ctx.created_by,
            )
        ctx.produce("revision_id", revision.id, uuid.UUID)


CreateDraftRevisionStage = CreateRevisionStage
