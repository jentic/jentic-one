"""Unit tests for the metrics facade."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry import metrics
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import HistogramDataPoint, InMemoryMetricReader

from jentic_one.shared.config import DEFAULT_LATENCY_BUCKETS_MS, MetricsConfig
from jentic_one.shared.metrics import (
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


_DEFAULT_MS = DEFAULT_LATENCY_BUCKETS_MS
_DEFAULT_S = tuple(b / 1000 for b in DEFAULT_LATENCY_BUCKETS_MS)


def _provider(config: MetricsConfig) -> tuple[MeterProvider, InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    return MeterProvider(metric_readers=[reader], views=metric_views(config)), reader


@pytest.mark.parametrize(
    ("name", "unit", "expected"),
    [
        ("http.server.duration", "ms", _DEFAULT_MS),
        ("broker.execution.duration_ms", "ms", _DEFAULT_MS),
        ("http.server.request.duration", "s", _DEFAULT_S),
    ],
)
def test_latency_histograms_use_default_buckets(
    name: str, unit: str, expected: tuple[float, ...]
) -> None:
    """With no config, each latency histogram gets the default sub-25 ms buckets in its unit."""
    provider, reader = _provider(MetricsConfig())
    provider.get_meter("test").create_histogram(name, unit=unit).record(12)

    assert _bucket_boundaries(reader, name) == expected
    provider.shutdown()


@pytest.mark.parametrize(
    ("name", "unit", "expected"),
    [
        ("http.server.duration", "ms", (3.0, 7.0, 40.0)),
        ("broker.execution.duration_ms", "ms", (3.0, 7.0, 40.0)),
        ("http.server.request.duration", "s", (0.003, 0.007, 0.04)),
    ],
)
def test_configured_buckets_drive_all_latency_histograms(
    name: str, unit: str, expected: tuple[float, ...]
) -> None:
    """One setting drives all three; the seconds view is derived by dividing by 1000."""
    provider, reader = _provider(MetricsConfig(latency_buckets_ms=[3, 7, 40]))
    provider.get_meter("test").create_histogram(name, unit=unit).record(5)

    assert _bucket_boundaries(reader, name) == pytest.approx(expected)
    provider.shutdown()


def test_unlisted_histogram_keeps_default_buckets() -> None:
    """Instruments outside the View list keep the SDK default aggregation."""
    provider, reader = _provider(MetricsConfig())
    provider.get_meter("test").create_histogram("ingest.duration_ms", unit="ms").record(12)

    assert _bucket_boundaries(reader, "ingest.duration_ms") != _DEFAULT_MS
    provider.shutdown()


def test_default_buckets_resolve_sub_25ms_and_reach_upstream_timeout() -> None:
    assert MetricsConfig().latency_buckets_ms == list(DEFAULT_LATENCY_BUCKETS_MS)
    assert DEFAULT_LATENCY_BUCKETS_MS == (
        1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 25.0, 50.0, 100.0, 250.0, 500.0,
        1000.0, 2500.0, 5000.0, 10000.0, 30000.0,
    )  # fmt: skip


async def test_inbound_http_duration_uses_low_latency_buckets() -> None:
    """The instrumented FastAPI app's server-duration histogram gets the explicit buckets."""
    provider, reader = _provider(MetricsConfig())

    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    FastAPIInstrumentor.instrument_app(app, meter_provider=provider)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/ping")
    FastAPIInstrumentor.uninstrument_app(app)

    assert _bucket_boundaries(reader, "http.server.duration") == _DEFAULT_MS
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


async def test_configure_metrics_exports_configured_buckets() -> None:
    """configure_metrics threads latency_buckets_ms from config into the global provider."""
    configure_metrics("test-svc", MetricsConfig(exporter="prometheus", latency_buckets_ms=[4, 8]))
    metrics.get_meter("broker").create_histogram("broker.execution.duration_ms", unit="ms").record(
        6
    )

    app = FastAPI()
    metrics_app = make_metrics_asgi_app()
    assert metrics_app is not None
    app.mount("/metrics", metrics_app)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/metrics/")).text

    les = {
        line.split('le="', 1)[1].split('"', 1)[0]
        for line in body.splitlines()
        if line.startswith("broker_execution_duration_ms_milliseconds_bucket")
    }
    # The prometheus_client registry is process-global, so an earlier test's
    # reader may also be scraped here; 4 and 8 are not default boundaries.
    assert {"4.0", "8.0"} <= les
