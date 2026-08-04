"""Pipeline infrastructure for the ingest module."""

from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.pipeline.pipeline import BasePipeline, Pipeline, PipelineFactory
from jentic_one.registry.ingest.pipeline.stage_registry import (
    PipelineStageSpec,
    register_pipeline_stage,
    registered_pipeline_stages,
)

__all__ = [
    "BasePipeline",
    "Pipeline",
    "PipelineContext",
    "PipelineFactory",
    "PipelineStageSpec",
    "register_pipeline_stage",
    "registered_pipeline_stages",
]
