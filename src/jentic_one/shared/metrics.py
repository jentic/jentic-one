"""Metrics facade — the single sanctioned entry point for application metrics.

All application code under `src/` should use `get_meter()` from this module
to create instruments. No source module outside this file may import
`prometheus_client` or any `opentelemetry.exporter.*` package directly —
this is enforced by `tests/arch/test_metrics_facade.py`. Tests under
`tests/` are exempt so they can poke at internals when needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import Histogram, MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from prometheus_client import make_asgi_app

from jentic_one.shared.config import MetricsConfig

if TYPE_CHECKING:
    from opentelemetry.metrics import Meter

_configured = False
_prometheus_active = False

#: Explicit histogram bucket boundaries, in milliseconds, for request-latency
#: instruments. The OTel default buckets (0, 5, 10, 25, 50, 75, 100, …) are too
#: coarse below 25 ms to resolve broker overhead; the dense low end gives
#: meaningful p50/p99 for in-process cost, and the tail reaches the 30 s default
#: upstream timeout for slow upstream calls. Floats so the Prometheus ``le``
#: label renders like the SDK defaults (``le="15.0"``).
LOW_LATENCY_BUCKETS_MS: tuple[float, ...] = (
    1.0,
    2.0,
    5.0,
    10.0,
    15.0,
    20.0,
    25.0,
    50.0,
    100.0,
    250.0,
    500.0,
    1000.0,
    2500.0,
    5000.0,
    10000.0,
    30000.0,
)

#: The same boundaries in seconds, for instruments recorded in ``s``.
LOW_LATENCY_BUCKETS_S: tuple[float, ...] = tuple(b / 1000 for b in LOW_LATENCY_BUCKETS_MS)

#: Histogram instruments that get the low-latency buckets, by recorded unit.
#: ``http.server.duration`` (ms) is what the ASGI/FastAPI instrumentation emits
#: under the default semconv mode; ``http.server.request.duration`` (s) is its
#: stable-semconv replacement (``OTEL_SEMCONV_STABILITY_OPT_IN=http``).
_LOW_LATENCY_HISTOGRAMS_MS: tuple[str, ...] = (
    "http.server.duration",
    "broker.execution.duration_ms",
)
_LOW_LATENCY_HISTOGRAMS_S: tuple[str, ...] = ("http.server.request.duration",)


def metric_views() -> list[View]:
    """Return the Views applied to the global MeterProvider.

    Each latency histogram gets explicit bucket boundaries in its own recorded
    unit; every other instrument keeps the SDK default aggregation.
    """
    ms = ExplicitBucketHistogramAggregation(boundaries=LOW_LATENCY_BUCKETS_MS)
    s = ExplicitBucketHistogramAggregation(boundaries=LOW_LATENCY_BUCKETS_S)
    return [
        *(
            View(instrument_type=Histogram, instrument_name=name, aggregation=ms)
            for name in _LOW_LATENCY_HISTOGRAMS_MS
        ),
        *(
            View(instrument_type=Histogram, instrument_name=name, aggregation=s)
            for name in _LOW_LATENCY_HISTOGRAMS_S
        ),
    ]


def configure_metrics(service_name: str, config: MetricsConfig) -> MeterProvider | None:
    """Configure the global MeterProvider based on the chosen exporter.

    Returns the MeterProvider if one was created, or None for the "none" exporter.
    Idempotent — subsequent calls return immediately.
    """
    global _configured, _prometheus_active
    if _configured:
        current = metrics.get_meter_provider()
        if isinstance(current, MeterProvider):
            return current
        return None

    _configured = True

    if config.exporter == "none":
        return None

    resource = Resource.create({"service.name": service_name})

    reader: MetricReader
    if config.exporter == "prometheus":
        reader = PrometheusMetricReader()
        _prometheus_active = True
    else:
        otlp_exporter = OTLPMetricExporter()
        reader = PeriodicExportingMetricReader(
            otlp_exporter, export_interval_millis=config.export_interval_seconds * 1000
        )

    provider = MeterProvider(resource=resource, metric_readers=[reader], views=metric_views())

    metrics.set_meter_provider(provider)
    return provider


def get_meter(name: str) -> Meter:
    """Return a Meter from the global MeterProvider."""
    return metrics.get_meter(name)


def make_metrics_asgi_app() -> Any | None:
    """Return a Prometheus ASGI app if the prometheus reader is active, else None."""
    if not _prometheus_active:
        return None
    return make_asgi_app()


def reset_metrics() -> None:
    """Reset metrics state — for testing only.

    Clears both this module's guard flags and OpenTelemetry's set-once
    global so a fresh `configure_metrics()` call in the next test takes
    effect. Touches OTel internals on purpose; if a future SDK release
    breaks the attribute names, fix it here in one place.
    """
    global _configured, _prometheus_active
    _configured = False
    _prometheus_active = False
    metrics._internal._METER_PROVIDER = None
    metrics._internal._METER_PROVIDER_SET_ONCE._done = False
