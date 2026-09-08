"""Operation extraction stage."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import structlog

from jentic_one.registry.ingest.parsers.openapi import OpenAPIOperationParser
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.registry.repos import OperationInput, OperationRepository

logger = structlog.get_logger()


class ExtractOperationsStage(BasePipelineStage):
    """Extracts operations from the OpenAPI spec and persists them."""

    name: ClassVar[str] = "ExtractOperationsStage"
    _requires: ClassVar[dict[str, type]] = {"revision_id": uuid.UUID}
    _produces: ClassVar[dict[str, type]] = {"operation_ids": set}

    async def pre_run(self, ctx: PipelineContext) -> None:
        revision_id = ctx.require("revision_id", uuid.UUID)
        await OperationRepository.delete_for_revision(ctx.session, revision_id)

    async def _run(self, ctx: PipelineContext) -> None:
        revision_id = ctx.require("revision_id", uuid.UUID)
        content: dict[str, Any] = ctx.specification.content or {}
        parser = OpenAPIOperationParser()
        raw_ops = parser.extract_operations(content)

        inputs: list[OperationInput] = []
        for op in raw_ops:
            summary = op.get("summary")
            if summary and len(summary) > 500:
                summary = summary[:500]
            inputs.append(
                OperationInput(
                    path=op["path"],
                    method=op["method"],
                    operation_id=op.get("operation_id"),
                    summary=summary,
                    description=op.get("description"),
                    tags=op.get("tags"),
                    deprecated=op.get("deprecated", False),
                    raw_operation=op,
                )
            )

        ids = await OperationRepository.bulk_create(
            ctx.session, revision_id, inputs, created_by=ctx.created_by
        )
        ctx.produce("operation_ids", set(ids), set)
        self._warn_if_security_unresolved(revision_id, content, raw_ops)

    @staticmethod
    def _warn_if_security_unresolved(
        revision_id: uuid.UUID,
        content: dict[str, Any],
        raw_ops: list[dict[str, Any]],
    ) -> None:
        """Warn when the spec declares schemes but no operation resolved one.

        A spec that defines ``components.securitySchemes`` yet yields zero
        operations with an effective ``security`` requirement almost certainly
        relies on something the importer failed to carry over — exactly the
        silent state that made globally-secured APIs uncallable in issue #772.

        Kept here (rather than in the scheme-persistence stage) so it stays a
        self-contained diagnostic: it reads what it needs from the spec and the
        just-parsed operations without adding cross-stage contract coupling for
        a log-only signal.
        """
        components = content.get("components")
        schemes = components.get("securitySchemes") if isinstance(components, dict) else None
        if not isinstance(schemes, dict) or not schemes or not raw_ops:
            return
        if any(op.get("security") for op in raw_ops):
            return
        scheme_names = sorted(str(name) for name in schemes)
        logger.warning(
            "security_schemes_unused",
            revision_id=str(revision_id),
            scheme_names=scheme_names,
            operation_count=len(raw_ops),
        )
