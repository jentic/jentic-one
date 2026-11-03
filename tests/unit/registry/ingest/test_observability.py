"""Tests for ingest pipeline observability instrumentation."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from structlog.contextvars import get_contextvars

from jentic_one.registry.ingest.errors import IngestPipelineError, IngestStageError
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.pipeline.pipeline import Pipeline, PipelineFactory
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.registry.services.import_service import ImportHandler


class PassingStage(BasePipelineStage):
    name = "pass_stage"

    async def _run(self, ctx: PipelineContext) -> None:
        ctx.produce("operation_ids", {"op1", "op2"}, set)
        ctx.produce("revision_id", uuid.uuid4(), uuid.UUID)


class FailingStage(BasePipelineStage):
    name = "fail_stage"

    async def _run(self, ctx: PipelineContext) -> None:
        raise ValueError("boom")


def _make_spec() -> IngestSpecification:
    return IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor="acme", name="pets", version="1.0.0"),
        content={"openapi": "3.1.0", "info": {"title": "Pets", "version": "1.0.0"}},
    )


def _make_ctx_mock() -> MagicMock:
    ctx_mock = MagicMock()
    ctx_mock.config.search.enabled = False
    session_mock = MagicMock()

    @asynccontextmanager
    async def fake_transaction(**kwargs: Any) -> AsyncGenerator[MagicMock, None]:
        yield session_mock

    ctx_mock.registry_db.transaction = fake_transaction
    return ctx_mock


def _get_metrics(reader: InMemoryMetricReader) -> dict[str, Any]:
    data = reader.get_metrics_data()
    result: dict[str, Any] = {}
    if data is None:
        return result
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                result[metric.name] = metric
    return result


def _counter_points(reader: InMemoryMetricReader, name: str) -> list[tuple[dict[str, Any], int]]:
    """Return ``(attributes, value)`` for every data point of counter ``name``."""
    counter = _get_metrics(reader)[name]
    return [(dict(dp.attributes), dp.value) for dp in counter.data.data_points]


async def test_happy_path_spans(
    otel_span_exporter: InMemorySpanExporter, otel_metric_reader: InMemoryMetricReader
) -> None:
    """Successful ingest produces the expected span tree and metrics."""
    ctx_mock = _make_ctx_mock()

    spec = _make_spec()
    pipeline = Pipeline([PassingStage()])

    with patch.object(PipelineFactory, "from_specification", return_value=pipeline):
        ingestor = Ingestor(ctx_mock)
        result = await ingestor.ingest(spec, created_by="usr_test")

    assert result.operation_count == 2

    spans = otel_span_exporter.get_finished_spans()
    span_names = [s.name for s in spans]
    assert "ingest.stage pass_stage" in span_names
    assert "ingest.pipeline" in span_names
    assert "ingest.ingest" in span_names

    ingest_span = next(s for s in spans if s.name == "ingest.ingest")
    assert ingest_span.attributes is not None
    assert ingest_span.attributes["ingest.vendor"] == "acme"
    assert ingest_span.attributes["ingest.name"] == "pets"
    assert ingest_span.attributes["ingest.version"] == "1.0.0"
    assert ingest_span.attributes["ingest.spec_type"] == "openapi"

    pipeline_span = next(s for s in spans if s.name == "ingest.pipeline")
    assert pipeline_span.attributes is not None
    assert pipeline_span.attributes["ingest.stage_count"] == 1

    stage_span = next(s for s in spans if s.name == "ingest.stage pass_stage")
    assert stage_span.attributes is not None
    assert stage_span.attributes["ingest.stage"] == "pass_stage"

    assert pipeline_span.context.trace_id == ingest_span.context.trace_id
    assert stage_span.context.trace_id == ingest_span.context.trace_id
    assert pipeline_span.parent.span_id == ingest_span.context.span_id  # type: ignore[union-attr]
    assert stage_span.parent.span_id == pipeline_span.context.span_id  # type: ignore[union-attr]

    metrics_data = _get_metrics(otel_metric_reader)
    assert "ingest.total" in metrics_data
    assert "ingest.duration_ms" in metrics_data
    assert "ingest.operation_count" in metrics_data
    assert "ingest.stage.total" in metrics_data
    assert "ingest.stage.duration_ms" in metrics_data


async def test_stage_failure_spans(
    otel_span_exporter: InMemorySpanExporter, otel_metric_reader: InMemoryMetricReader
) -> None:
    """A failing stage produces error spans and error metrics."""
    spec = _make_spec()
    ctx = PipelineContext(session=None, specification=spec, created_by="usr_test")
    stage = FailingStage()

    with pytest.raises(IngestStageError):
        await stage.run(ctx)

    spans = otel_span_exporter.get_finished_spans()
    span_names = [s.name for s in spans]
    assert "ingest.stage fail_stage" in span_names

    stage_span = next(s for s in spans if s.name == "ingest.stage fail_stage")
    assert stage_span.status.status_code == StatusCode.ERROR
    assert len(stage_span.events) > 0

    stage_points = _counter_points(otel_metric_reader, "ingest.stage.total")
    assert stage_points == [({"stage": "fail_stage", "status": "error"}, 1)]


async def test_ingest_failure_spans(
    otel_span_exporter: InMemorySpanExporter, otel_metric_reader: InMemoryMetricReader
) -> None:
    """A failing ingest produces error spans at all levels."""
    ctx_mock = _make_ctx_mock()

    spec = _make_spec()
    pipeline = Pipeline([FailingStage()])

    with patch.object(PipelineFactory, "from_specification", return_value=pipeline):
        ingestor = Ingestor(ctx_mock)
        with pytest.raises(IngestPipelineError):
            await ingestor.ingest(spec, created_by="usr_test")

    spans = otel_span_exporter.get_finished_spans()
    span_names = [s.name for s in spans]

    assert "ingest.stage fail_stage" in span_names
    assert "ingest.pipeline" in span_names
    assert "ingest.ingest" in span_names

    pipeline_span = next(s for s in spans if s.name == "ingest.pipeline")
    assert pipeline_span.status.status_code == StatusCode.ERROR
    assert len(pipeline_span.events) > 0

    ingest_span = next(s for s in spans if s.name == "ingest.ingest")
    assert ingest_span.status.status_code == StatusCode.ERROR

    assert _counter_points(otel_metric_reader, "ingest.total") == [({"status": "error"}, 1)]
    stage_points = _counter_points(otel_metric_reader, "ingest.stage.total")
    assert stage_points == [({"stage": "fail_stage", "status": "error"}, 1)]


async def test_pipeline_stage_metrics_attributes(otel_metric_reader: InMemoryMetricReader) -> None:
    """Stage metrics record the correct stage name and status attributes."""
    spec = _make_spec()
    ctx = PipelineContext(session=None, specification=spec, created_by="usr_test")
    stage = PassingStage()
    await stage.run(ctx)

    metrics_data = _get_metrics(otel_metric_reader)
    stage_total = metrics_data["ingest.stage.total"]
    data_points = list(stage_total.data.data_points)
    assert any(
        dp.attributes.get("stage") == "pass_stage" and dp.attributes.get("status") == "ok"
        for dp in data_points
    )


async def test_job_correlation_logging() -> None:
    """ImportHandler.execute binds job_id into structlog contextvars."""
    ctx_mock = AsyncMock()
    ctx_mock.config.ingest = AsyncMock()

    handler = ImportHandler(ctx_mock)
    result = await handler.execute(
        "test-job-123", None, payload={"sources": []}, created_by="usr_test"
    )

    assert result.body == {"revisions": []}
    assert "job_id" not in get_contextvars()
