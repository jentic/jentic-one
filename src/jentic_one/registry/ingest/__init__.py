"""Registry ingest — core building blocks for API spec ingestion."""

from jentic_one.registry.ingest.fetch import InlineSource, UrlSource, load_specification
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.ingest.pipeline import (
    Pipeline,
    PipelineContext,
    PipelineFactory,
    PipelineStageSpec,
    register_pipeline_stage,
    registered_pipeline_stage_specs,
)
from jentic_one.registry.ingest.schemas import IngestResult

__all__ = [
    "ApiIdentifier",
    "IngestResult",
    "IngestSpecification",
    "Ingestor",
    "InlineSource",
    "Pipeline",
    "PipelineContext",
    "PipelineFactory",
    "PipelineStageSpec",
    "SpecType",
    "UrlSource",
    "load_specification",
    "register_pipeline_stage",
    "registered_pipeline_stage_specs",
]
