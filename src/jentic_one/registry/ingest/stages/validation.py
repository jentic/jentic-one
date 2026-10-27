"""Validation stage — verifies spec structure before processing."""

from __future__ import annotations

from typing import ClassVar

from jentic_one.registry.ingest.errors import IngestStageError
from jentic_one.registry.ingest.models import SpecType
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.base import BasePipelineStage


class ValidateOpenAPISpec(BasePipelineStage):
    """Validates that the specification is a well-formed OpenAPI document."""

    name: ClassVar[str] = "ValidateOpenAPISpec"
    _requires: ClassVar[dict[str, type]] = {}
    _produces: ClassVar[dict[str, type]] = {}

    async def _run(self, ctx: PipelineContext) -> None:
        if ctx.specification.spec_type != SpecType.OPENAPI:
            raise IngestStageError(
                f"Expected OpenAPI spec type, got '{ctx.specification.spec_type}'"
            )

        content = ctx.specification.content
        if not content:
            raise IngestStageError("Specification content is empty")

        if "arazzo" in content:
            raise IngestStageError("Arazzo specifications are not supported")

        if "openapi" not in content:
            raise IngestStageError("Missing 'openapi' key in specification content")
