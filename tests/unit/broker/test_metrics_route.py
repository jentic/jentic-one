"""The standalone broker serves /metrics/ ahead of its forward-proxy catch-all.

The broker's ``/{upstream_url:path}`` route matches every path, so the
Prometheus mount must be registered before it or every scrape is answered by
the auth-gated proxy (401) instead of the exposition.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry import metrics
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from jentic_one.__main__ import _expand_allowed_dbs
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.shared.config import AppConfig, MetricsConfig
from jentic_one.shared.context import Context
from jentic_one.shared.metrics import configure_metrics, reset_metrics


@pytest.fixture()
def prometheus_broker_client(sample_config_dict: dict[str, Any]) -> Iterator[TestClient]:
    """A standalone broker app built with the Prometheus exporter enabled.

    Restores whatever global MeterProvider was installed beforehand so other
    modules that pin their own in-memory provider at import time keep it.
    """
    previous_provider = metrics._internal._METER_PROVIDER
    reset_metrics()
    configure_metrics("test-broker", MetricsConfig(exporter="prometheus"))
    app_config = AppConfig.model_validate(sample_config_dict)
    ctx = Context(app_config, allowed_dbs=_expand_allowed_dbs(["broker"], app_config))
    app = create_broker_app(ctx)
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
        reset_metrics()
        if previous_provider is not None:
            metrics.set_meter_provider(previous_provider)


def test_broker_metrics_returns_prometheus_exposition(
    prometheus_broker_client: TestClient,
) -> None:
    """GET /metrics/ answers 200 with Prometheus text, not the proxy's 401."""
    resp = prometheus_broker_client.get("/metrics/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "# TYPE" in resp.text


def test_broker_catch_all_still_handles_other_paths(
    prometheus_broker_client: TestClient,
) -> None:
    """Non-infra paths still reach the auth-gated forward proxy."""
    resp = prometheus_broker_client.get("/api.example.com/v1/things")
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")
