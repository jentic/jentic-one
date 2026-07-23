"""API resolution and draft revision creation stages."""

from __future__ import annotations

import uuid
from typing import ClassVar

from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.registry.repos import ApiRepository, ApiRevisionRepository
from jentic_one.shared.models import ApiRevisionSourceType


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
        )
        ctx.produce("api_id", api.id, uuid.UUID)


class CreateRevisionStage(BasePipelineStage):
    """Creates an ApiRevision (draft or imported) for this ingestion."""

    name: ClassVar[str] = "CreateRevisionStage"
    _requires: ClassVar[dict[str, type]] = {"api_id": uuid.UUID}
    _produces: ClassVar[dict[str, type]] = {"revision_id": uuid.UUID}

    async def _run(self, ctx: PipelineContext) -> None:
        api_id = ctx.require("api_id", uuid.UUID)
        spec = ctx.specification
        digest = spec.sha or ""
        if spec.origin is not None:
            # Re-importing an unchanged spec yields the same digest. Reuse the
            # existing revision instead of inserting a duplicate (which would
            # violate the (api_id, spec_digest) unique constraint and fail the
            # import job). Archive any *other* active imported revision, then
            # reactivate the matching one — so re-import is an idempotent no-op.
            existing = await ApiRevisionRepository.get_by_digest(ctx.session, api_id, digest)
            if existing is not None:
                await ApiRevisionRepository.archive_all_active_imported(ctx.session, api_id)
                revision = await ApiRevisionRepository.reactivate_imported(
                    ctx.session, existing, origin=spec.origin
                )
            else:
                await ApiRevisionRepository.archive_active_imported(
                    ctx.session, api_id, spec.origin
                )
                revision = await ApiRevisionRepository.create_imported(
                    ctx.session,
                    api_id=api_id,
                    origin=spec.origin,
                    spec_digest=digest,
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
                spec_digest=digest,
                source_type=spec.source_type or ApiRevisionSourceType.UNKNOWN,
                source_url=spec.source_url,
                source_filename=spec.source_filename,
                submitted_by=spec.submitted_by,
                created_by=ctx.created_by,
            )
        ctx.produce("revision_id", revision.id, uuid.UUID)


CreateDraftRevisionStage = CreateRevisionStage
