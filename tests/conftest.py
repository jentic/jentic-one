"""Suite-wide fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from jentic_one.shared.dns_cache import clear_resolution_cache
from jentic_one.shared.metrics import reset_metrics
from jentic_one.shared.tracing import reset_tracing


@pytest.fixture(autouse=True)
def _isolated_resolution_cache() -> Iterator[None]:
    """Start and end every test with an empty process-wide DNS answer cache.

    Tests swap ``socket.getaddrinfo`` for canned answers; a cached answer from
    one test must never satisfy a lookup in the next.
    """
    clear_resolution_cache()
    yield
    clear_resolution_cache()


@pytest.fixture()
def otel_metric_reader() -> Iterator[InMemoryMetricReader]:
    """Install a fresh in-memory global ``MeterProvider`` for one test.

    Proxy instruments created at import time (``get_meter(...).create_*``)
    rebind to the provider installed here, so the returned reader sees every
    measurement the test makes. The global is reset on teardown so no other
    test inherits this reader.
    """
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    reset_metrics()
    metrics.set_meter_provider(provider)
    yield reader
    reset_metrics()
    provider.shutdown()


@pytest.fixture()
def otel_span_exporter() -> Iterator[InMemorySpanExporter]:
    """Install a fresh in-memory global ``TracerProvider`` for one test.

    A module-level ``trace.get_tracer(...)`` proxy binds to the first provider
    it sees and keeps it; tests asserting on spans from such a tracer must also
    clear that binding (see ``tests/unit/registry/ingest/test_observability.py``).
    The global is reset on teardown so no other test inherits this exporter.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    reset_tracing()
    trace.set_tracer_provider(provider)
    yield exporter
    reset_tracing()
    provider.shutdown()
