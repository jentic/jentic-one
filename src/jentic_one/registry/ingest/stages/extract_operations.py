"""Operation extraction stage."""

from __future__ import annotations

import uuid
from typing import ClassVar

from jentic_one.registry.ingest.parsers.openapi import OpenAPIOperationParser
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.registry.repos import OperationInput, OperationRepository


class ExtractOperationsStage(BasePipelineStage):
    """Extracts operations from the OpenAPI spec and persists them."""

    name: ClassVar[str] = "ExtractOperationsStage"
    _requires: ClassVar[dict[str, type]] = {"revision_id": uuid.UUID}
    _produces: ClassVar[dict[str, type]] = {"operation_ids": set, "secured_operation_count": int}

    async def pre_run(self, ctx: PipelineContext) -> None:
        revision_id = ctx.require("revision_id", uuid.UUID)
        await OperationRepository.delete_for_revision(ctx.session, revision_id)

    async def _run(self, ctx: PipelineContext) -> None:
        revision_id = ctx.require("revision_id", uuid.UUID)
        parser = OpenAPIOperationParser()
        raw_ops = parser.extract_operations(ctx.specification.content or {})

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
        # Feeds the unresolved-security warning in ExtractSecuritySchemesStage
        # (issue #772): how many operations resolved an effective requirement.
        secured = sum(1 for op in raw_ops if op.get("security"))
        ctx.produce("secured_operation_count", secured, int)
