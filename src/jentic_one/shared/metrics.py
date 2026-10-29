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
_provider: MeterProvider | None = None

#: Histogram instruments that get the configured latency buckets
#: (``observability.metrics.latency_buckets_ms``), by recorded unit.
#: ``http.server.duration`` (ms) is what the ASGI/FastAPI instrumentation emits
#: under the default semconv mode; ``http.server.request.duration`` (s) is its
#: stable-semconv replacement (``OTEL_SEMCONV_STABILITY_OPT_IN=http``).
_LATENCY_HISTOGRAMS_MS: tuple[str, ...] = (
    "http.server.duration",
    "broker.execution.duration_ms",
)
_LATENCY_HISTOGRAMS_S: tuple[str, ...] = ("http.server.request.duration",)


def metric_views(config: MetricsConfig) -> list[View]:
    """Return the Views applied to the MeterProvider for ``config``.

    Each latency histogram gets ``config.latency_buckets_ms`` as explicit
    bucket boundaries in its own recorded unit (divided by 1000 for the
    seconds-unit instrument); every other instrument keeps the SDK default
    aggregation. Each View matches on name *and* unit, so an instrument that
    reuses one of these names with a different unit keeps the SDK default
    buckets rather than getting boundaries that are 1000x off. Boundaries are
    floats so the Prometheus ``le`` label renders like the SDK defaults
    (``le="15.0"``).
    """
    buckets_ms = tuple(float(b) for b in config.latency_buckets_ms)
    ms = ExplicitBucketHistogramAggregation(boundaries=buckets_ms)
    s = ExplicitBucketHistogramAggregation(boundaries=tuple(b / 1000 for b in buckets_ms))
    return [
        *(
            View(
                instrument_type=Histogram,
                instrument_name=name,
                instrument_unit="ms",
                aggregation=ms,
            )
            for name in _LATENCY_HISTOGRAMS_MS
        ),
        *(
            View(
                instrument_type=Histogram,
                instrument_name=name,
                instrument_unit="s",
                aggregation=s,
            )
            for name in _LATENCY_HISTOGRAMS_S
        ),
    ]


def configure_metrics(service_name: str, config: MetricsConfig) -> MeterProvider | None:
    """Configure the global MeterProvider based on the chosen exporter.

    Returns the MeterProvider if one was created, or None for the "none" exporter.
    Idempotent — subsequent calls return immediately.
    """
    global _configured, _prometheus_active, _provider
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

    provider = MeterProvider(resource=resource, metric_readers=[reader], views=metric_views(config))

    metrics.set_meter_provider(provider)
    _provider = provider
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

    Shuts down the MeterProvider that `configure_metrics()` created (so its
    ``PrometheusMetricReader`` unregisters from the process-global
    ``prometheus_client`` registry instead of being scraped again by the next
    test; providers a test installs itself are left to that test), then clears
    this module's guard flags and OpenTelemetry's set-once global so a fresh
    `configure_metrics()` call in the next test takes effect. Touches OTel
    internals on purpose; if a future SDK release breaks the attribute names,
    fix it here in one place.
    """
    global _configured, _prometheus_active, _provider
    if _provider is not None:
        # Bounded: an OTLP reader flushes on shutdown, and tests have no collector.
        _provider.shutdown(timeout_millis=1_000)
        _provider = None
    _configured = False
    _prometheus_active = False
    metrics._internal._METER_PROVIDER = None
    metrics._internal._METER_PROVIDER_SET_ONCE._done = False
