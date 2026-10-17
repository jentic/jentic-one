"""Unit tests for the metrics facade."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry import metrics
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider

from jentic_one.shared.config import MetricsConfig
from jentic_one.shared.metrics import configure_metrics, make_metrics_asgi_app

pytestmark = pytest.mark.usefixtures("isolated_meter_provider")


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
