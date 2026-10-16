"""Unit tests for the metrics facade."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry import metrics
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import HistogramDataPoint, InMemoryMetricReader

from jentic_one.shared.config import MetricsConfig
from jentic_one.shared.metrics import (
    LOW_LATENCY_BUCKETS_MS,
    LOW_LATENCY_BUCKETS_S,
    configure_metrics,
    make_metrics_asgi_app,
    metric_views,
    reset_metrics,
)


@pytest.fixture(autouse=True)
def _reset_metrics_state():
    """Reset global metrics state between tests."""
    reset_metrics()
    yield
    reset_metrics()


def test_none_exporter_is_noop():
    config = MetricsConfig(exporter="none")
    result = configure_metrics("test-svc", config)
    assert result is None
    assert not isinstance(metrics.get_meter_provider(), MeterProvider)


def test_prometheus_exporter_creates_provider():
    config = MetricsConfig(exporter="prometheus")
    result = configure_metrics("test-svc", config)
    assert isinstance(result, MeterProvider)
    assert metrics.get_meter_provider() is result


def test_configure_metrics_idempotent():
    config = MetricsConfig(exporter="prometheus")
    first = configure_metrics("test-svc", config)
    second = configure_metrics("test-svc", config)
    assert first is second


async def test_prometheus_exporter_serves_metrics():
    config = MetricsConfig(exporter="prometheus")
    configure_metrics("test-svc", config)

    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    metrics_app = make_metrics_asgi_app()
    assert metrics_app is not None
    app.mount("/metrics", metrics_app)

    FastAPIInstrumentor.instrument_app(app)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/ping")
        resp = await client.get("/metrics/")
        assert resp.status_code == 200
        body = resp.text
        assert "http" in body.lower() or "target_info" in body.lower()

    FastAPIInstrumentor.uninstrument_app(app)


async def test_route_template_cardinality():
    config = MetricsConfig(exporter="prometheus")
    configure_metrics("test-svc", config)

    app = FastAPI()

    @app.get("/items/{item_id}")
    async def get_item(item_id: str):
        return {"id": item_id}

    metrics_app = make_metrics_asgi_app()
    assert metrics_app is not None
    app.mount("/metrics", metrics_app)

    FastAPIInstrumentor.instrument_app(app)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/items/abc")
        await client.get("/items/xyz")
        await client.get("/items/123")
        resp = await client.get("/metrics/")
        body = resp.text
        # Metrics should use the route template, not the raw path
        assert "/items/{item_id}" in body or "/items/" in body
        # Raw paths should NOT appear as separate metric labels
        assert body.count("/items/abc") == 0
        assert body.count("/items/xyz") == 0

    FastAPIInstrumentor.uninstrument_app(app)


def _bucket_boundaries(reader: InMemoryMetricReader, name: str) -> tuple[float, ...]:
    data = reader.get_metrics_data()
    assert data is not None
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name == name:
                    point = next(iter(metric.data.data_points))
                    assert isinstance(point, HistogramDataPoint)
                    return tuple(point.explicit_bounds)
    raise AssertionError(f"metric {name!r} not exported")


@pytest.mark.parametrize(
    ("name", "unit", "expected"),
    [
        ("http.server.duration", "ms", LOW_LATENCY_BUCKETS_MS),
        ("broker.execution.duration_ms", "ms", LOW_LATENCY_BUCKETS_MS),
        ("http.server.request.duration", "s", LOW_LATENCY_BUCKETS_S),
    ],
)
def test_latency_histograms_use_low_latency_buckets(
    name: str, unit: str, expected: tuple[float, ...]
) -> None:
    """The Views give each latency histogram explicit sub-25 ms buckets in its unit."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader], views=metric_views())
    provider.get_meter("test").create_histogram(name, unit=unit).record(12)

    assert _bucket_boundaries(reader, name) == expected
    provider.shutdown()


def test_unlisted_histogram_keeps_default_buckets() -> None:
    """Instruments outside the View list keep the SDK default aggregation."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader], views=metric_views())
    provider.get_meter("test").create_histogram("ingest.duration_ms", unit="ms").record(12)

    assert _bucket_boundaries(reader, "ingest.duration_ms") != LOW_LATENCY_BUCKETS_MS
    provider.shutdown()


def test_low_latency_buckets_resolve_sub_25ms_and_reach_upstream_timeout() -> None:
    assert {1, 2, 5, 10, 15, 20, 25} <= set(LOW_LATENCY_BUCKETS_MS)
    assert max(LOW_LATENCY_BUCKETS_MS) == 30000
    assert list(LOW_LATENCY_BUCKETS_MS) == sorted(LOW_LATENCY_BUCKETS_MS)
    assert LOW_LATENCY_BUCKETS_S[0] == 0.001
    assert LOW_LATENCY_BUCKETS_S[-1] == 30.0


async def test_inbound_http_duration_uses_low_latency_buckets() -> None:
    """The instrumented FastAPI app's server-duration histogram gets the explicit buckets."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader], views=metric_views())

    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    FastAPIInstrumentor.instrument_app(app, meter_provider=provider)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/ping")
    FastAPIInstrumentor.uninstrument_app(app)

    assert _bucket_boundaries(reader, "http.server.duration") == LOW_LATENCY_BUCKETS_MS
    provider.shutdown()


async def test_configure_metrics_applies_views_to_exported_histograms() -> None:
    """The global provider built by configure_metrics exports the explicit buckets."""
    configure_metrics("test-svc", MetricsConfig(exporter="prometheus"))
    metrics.get_meter("broker").create_histogram("broker.execution.duration_ms", unit="ms").record(
        12
    )

    app = FastAPI()
    metrics_app = make_metrics_asgi_app()
    assert metrics_app is not None
    app.mount("/metrics", metrics_app)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/metrics/")).text

    buckets = {
        line.split('le="', 1)[1].split('"', 1)[0]: line.rsplit(" ", 1)[1]
        for line in body.splitlines()
        if line.startswith("broker_execution_duration_ms_milliseconds_bucket")
    }
    assert "15.0" in buckets
    assert "30000.0" in buckets
    # The 12 ms sample lands in the (10, 15] bucket, not the default (10, 25].
    assert buckets["10.0"] == "0.0"
    assert buckets["15.0"] == "1.0"
