"""Shared OTel test fixtures: per-test providers and their teardown contract.

Pins #1732: spans from a module-level ``ProxyTracer`` and measurements from a
module-level proxy instrument reach the provider each test installs, however
many earlier tests used the fixtures, and teardown restores the provider (and
metrics guard flags) that was installed before the test.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, NumberDataPoint
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from jentic_one.registry.ingest import observability
from jentic_one.shared import metrics as shared_metrics
from jentic_one.shared.metrics import reset_metrics
from jentic_one.shared.tracing import reset_tracing


@pytest.fixture()
def outer_tracer_provider() -> Iterator[TracerProvider]:
    """Stand in for a wider-scoped provider; teardown asserts it was restored.

    Requested before ``otel_span_exporter`` so it is set up first and torn down
    last, after the shared fixture has run its own teardown.
    """
    previous = trace._TRACER_PROVIDER
    outer = TracerProvider()
    reset_tracing()
    trace.set_tracer_provider(outer)
    yield outer
    restored = trace._TRACER_PROVIDER
    reset_tracing()
    if previous is not None:
        trace.set_tracer_provider(previous)
    outer.shutdown()
    assert restored is outer


@pytest.fixture()
def outer_meter_provider() -> Iterator[MeterProvider]:
    """Metrics counterpart of ``outer_tracer_provider``, with ``configure_metrics()`` done."""
    previous = metrics._internal._METER_PROVIDER
    previous_flags = (shared_metrics._configured, shared_metrics._prometheus_active)
    outer = MeterProvider()
    reset_metrics()
    metrics.set_meter_provider(outer)
    shared_metrics._configured = True
    shared_metrics._prometheus_active = True
    yield outer
    restored = metrics._internal._METER_PROVIDER
    restored_flags = (shared_metrics._configured, shared_metrics._prometheus_active)
    reset_metrics()
    if previous is not None:
        metrics.set_meter_provider(previous)
    shared_metrics._configured, shared_metrics._prometheus_active = previous_flags
    outer.shutdown()
    assert restored is outer
    assert restored_flags == (True, True)


def test_module_level_tracer_reaches_first_test_exporter(
    otel_span_exporter: InMemorySpanExporter,
) -> None:
    """The first test using the fixture captures spans from a module-level tracer."""
    assert isinstance(observability.tracer, trace.ProxyTracer)
    with observability.tracer.start_as_current_span("first"):
        pass
    assert [s.name for s in otel_span_exporter.get_finished_spans()] == ["first"]


def test_module_level_tracer_reaches_second_test_exporter(
    otel_span_exporter: InMemorySpanExporter,
) -> None:
    """A later test does too, rather than writing to the previous test's provider."""
    with observability.tracer.start_as_current_span("second"):
        pass
    assert [s.name for s in otel_span_exporter.get_finished_spans()] == ["second"]


def test_module_level_counter_reaches_test_reader(
    otel_metric_reader: InMemoryMetricReader,
) -> None:
    """A module-level proxy counter records into this test's reader."""
    observability.ingests_total.add(1, {"status": "error"})
    data = otel_metric_reader.get_metrics_data()
    assert data is not None
    points = [
        (dict(dp.attributes or {}), dp.value)
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == "ingest.total"
        for dp in metric.data.data_points
        if isinstance(dp, NumberDataPoint)
    ]
    assert points == [({"status": "error"}, 1)]


def test_span_exporter_teardown_restores_previous_provider(
    outer_tracer_provider: TracerProvider, otel_span_exporter: InMemorySpanExporter
) -> None:
    """During the test the fixture's provider is global; the outer one comes back after."""
    assert trace._TRACER_PROVIDER is not outer_tracer_provider


def test_metric_reader_teardown_restores_previous_provider(
    outer_meter_provider: MeterProvider, otel_metric_reader: InMemoryMetricReader
) -> None:
    """During the test the guard flags are reset; provider and flags come back after."""
    assert metrics._internal._METER_PROVIDER is not outer_meter_provider
    assert not shared_metrics._configured
    assert not shared_metrics._prometheus_active
