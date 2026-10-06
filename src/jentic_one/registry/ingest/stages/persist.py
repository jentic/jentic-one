"""Persistence and finalization stages."""

from __future__ import annotations

import uuid
from typing import ClassVar

from jentic_one.registry.ingest.exc import HostOwnedByOtherVendorIngestError
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.registry.repos import (
    ApiRepository,
    ApiRevisionRepository,
    SpecFileRepository,
    UrlIndexRepository,
)
from jentic_one.registry.repos.url_index_repo import describe_live_host_owners


class StoreSpecFileStage(BasePipelineStage):
    """Persists the raw spec file content."""

    name: ClassVar[str] = "StoreSpecFileStage"
    _requires: ClassVar[dict[str, type]] = {"revision_id": uuid.UUID}
    _produces: ClassVar[dict[str, type]] = {"spec_file_id": uuid.UUID}

    async def _run(self, ctx: PipelineContext) -> None:
        revision_id = ctx.require("revision_id", uuid.UUID)
        spec_file = await SpecFileRepository.create_or_update(
            ctx.session,
            revision_id=revision_id,
            filename=ctx.specification.api_identifier.filename,
            content=ctx.specification.content or {},
            sha=ctx.specification.sha,
            created_by=ctx.created_by,
        )
        ctx.produce("spec_file_id", spec_file.id, uuid.UUID)


class FinalizeStage(BasePipelineStage):
    """Updates aggregate counts on the Api and ApiRevision."""

    name: ClassVar[str] = "FinalizeStage"
    _requires: ClassVar[dict[str, type]] = {
        "api_id": uuid.UUID,
        "revision_id": uuid.UUID,
        "operation_ids": set,
    }
    _produces: ClassVar[dict[str, type]] = {}

    async def _run(self, ctx: PipelineContext) -> None:
        api_id = ctx.require("api_id", uuid.UUID)
        revision_id = ctx.require("revision_id", uuid.UUID)
        operation_ids: set[str] = ctx.require("operation_ids", set)
        operation_count = len(operation_ids)

        await ApiRevisionRepository.set_operation_count(ctx.session, revision_id, operation_count)
        await ApiRepository.apply_counts(
            ctx.session,
            api_id,
            revision_count_delta=1,
            operation_count=operation_count,
        )

        # A revision held by the server-host change guard stays a draft: the API
        # keeps serving its current revision until an operator promotes it (the
        # promote runs the ownership check below).
        if ctx.specification.origin is not None and ctx.get("held_host_change") is None:
            # An origin-bearing import goes live right here, so it is held to the
            # same one-vendor-per-host rule as promotion (RevisionService.promote).
            owners = await UrlIndexRepository.find_live_hosts_of_other_vendors(
                ctx.session,
                revision_id=revision_id,
                vendor=ctx.specification.api_identifier.vendor,
            )
            if owners:
                raise HostOwnedByOtherVendorIngestError(describe_live_host_owners(owners))
            await ApiRepository.set_current_revision(ctx.session, api_id, revision_id)
