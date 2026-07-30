"""Security scheme extraction stage."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import structlog

from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.registry.repos import SecurityRepository

logger = structlog.get_logger()


class ExtractSecuritySchemesStage(BasePipelineStage):
    """Extracts security schemes from the spec and persists them."""

    name: ClassVar[str] = "ExtractSecuritySchemesStage"
    _requires: ClassVar[dict[str, type]] = {
        "revision_id": uuid.UUID,
        "operation_ids": set,
        "secured_operation_count": int,
    }
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
            self._warn_if_unresolved(ctx, schemes)
        else:
            ctx.produce("security_scheme_ids", set(), set)

    @staticmethod
    def _warn_if_unresolved(ctx: PipelineContext, schemes: dict[str, dict[str, Any]]) -> None:
        """Warn when schemes exist but no operation resolved a requirement.

        A spec that declares ``securitySchemes`` yet yields zero operations
        with an effective ``security`` requirement almost certainly relies on
        something the importer failed to carry over — exactly the silent state
        that made globally-secured APIs uncallable in issue #772.
        """
        operation_ids = ctx.require("operation_ids", set)
        secured_count = ctx.require("secured_operation_count", int)
        if operation_ids and secured_count == 0:
            logger.warning(
                "security_schemes_unused",
                revision_id=str(ctx.require("revision_id", uuid.UUID)),
                scheme_names=sorted(schemes),
                operation_count=len(operation_ids),
                detail=(
                    "spec declares securitySchemes but no operation resolved an "
                    "effective security requirement; authenticated calls will not "
                    "receive credentials"
                ),
            )
