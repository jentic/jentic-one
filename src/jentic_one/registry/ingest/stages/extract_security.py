"""Security scheme extraction stage."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.registry.repos import SecurityRepository


class ExtractSecuritySchemesStage(BasePipelineStage):
    """Extracts security schemes from the spec and persists them."""

    name: ClassVar[str] = "ExtractSecuritySchemesStage"
    _requires: ClassVar[dict[str, type]] = {"revision_id": uuid.UUID}
    _produces: ClassVar[dict[str, type]] = {"security_scheme_ids": set}

    async def pre_run(self, ctx: PipelineContext) -> None:
        revision_id = ctx.require("revision_id", uuid.UUID)
        await SecurityRepository.delete_for_revision(ctx.session, revision_id)

    async def _run(self, ctx: PipelineContext) -> None:
        revision_id = ctx.require("revision_id", uuid.UUID)
        content: dict[str, Any] = ctx.specification.content or {}

        schemes: dict[str, dict[str, Any]] = content.get("components", {}).get(
            "securitySchemes", {}
        )
        if schemes:
            ids = await SecurityRepository.store_security_schemes(
                ctx.session,
                revision_id=revision_id,
                schemes=schemes,
                created_by=ctx.created_by,
            )
            ctx.produce("security_scheme_ids", set(ids), set)
        else:
            ctx.produce("security_scheme_ids", set(), set)
