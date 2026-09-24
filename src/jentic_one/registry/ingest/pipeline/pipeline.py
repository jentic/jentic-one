"""Pipeline execution engine and factory."""

from __future__ import annotations

import structlog
from opentelemetry.trace.status import Status, StatusCode

from jentic_one.registry.ingest.exc import IngestPipelineError, IngestStageError
from jentic_one.registry.ingest.models import IngestSpecification, SpecType
from jentic_one.registry.ingest.observability import tracer
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.pipeline.stage_registry import registered_pipeline_stages
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.registry.ingest.stages.extract_api import CreateDraftRevisionStage, ResolveApiStage
from jentic_one.registry.ingest.stages.extract_operations import ExtractOperationsStage
from jentic_one.registry.ingest.stages.extract_security import ExtractSecuritySchemesStage
from jentic_one.registry.ingest.stages.extract_servers import ExtractServersStage
from jentic_one.registry.ingest.stages.persist import FinalizeStage, StoreSpecFileStage
from jentic_one.registry.ingest.stages.search_text import BuildSearchTextForOperationsStage
from jentic_one.registry.ingest.stages.url_index import BuildURLIndexStage
from jentic_one.registry.ingest.stages.validation import ValidateOpenAPISpec

logger = structlog.get_logger(__name__)


class BasePipeline:
    """Base pipeline that holds and executes an ordered list of stages."""

    def __init__(self, stages: list[BasePipelineStage]) -> None:
        self.stages = stages


class Pipeline(BasePipeline):
    """Executes stages sequentially, propagating errors."""

    async def run(self, ctx: PipelineContext) -> None:
        """Run all stages in order. Fail-fast on first error."""
        log = logger.bind(stage_count=len(self.stages))
        log.info("pipeline_start")
        with tracer.start_as_current_span("ingest.pipeline") as span:
            span.set_attribute("ingest.stage_count", len(self.stages))
            try:
                for stage in self.stages:
                    await stage.run(ctx)
            except IngestStageError as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR))
                log.error("pipeline_failed", stage=exc.message)
                raise IngestPipelineError(exc.message) from exc
            else:
                log.info("pipeline_complete")


class PipelineFactory:
    """Creates pipelines based on specification type."""

    @staticmethod
    def from_specification(
        spec: IngestSpecification, *, include_search_text: bool = False
    ) -> Pipeline:
        """Build the appropriate pipeline for the given spec type."""
        if spec.spec_type == SpecType.OPENAPI:
            stages: list[BasePipelineStage] = [
                ValidateOpenAPISpec(),
                ResolveApiStage(),
                CreateDraftRevisionStage(),
                ExtractOperationsStage(),
                ExtractServersStage(),
                BuildURLIndexStage(),
                ExtractSecuritySchemesStage(),
                StoreSpecFileStage(),
                FinalizeStage(),
            ]
            if include_search_text:
                stages.append(BuildSearchTextForOperationsStage())
            # Extension stages (register_pipeline_stage) always run last, in
            # registration order, so they can consume the built-ins' products
            # (operation_ids, revision_id, ...) within the same transaction.
            stages.extend(registered_pipeline_stages(spec.spec_type))
            return Pipeline(stages)
        msg = f"Unsupported spec type: {spec.spec_type}"
        raise ValueError(msg)
