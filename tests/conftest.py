"""Suite-wide fixtures."""

from __future__ import annotations

import inspect
from collections.abc import Iterator

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from jentic_one.shared import metrics as shared_metrics
from jentic_one.shared.dns_cache import clear_resolution_cache
from jentic_one.shared.metrics import reset_metrics
from jentic_one.shared.tracing import reset_tracing

# ``ProxyTracer`` internals ``otel_span_exporter`` relies on: the instance
# fields its ``_tracer`` property reads to build the real tracer, plus the
# ``_real_tracer`` cache that property fills. Verified against
# opentelemetry-api 1.45.1; the checks in ``otel_span_exporter`` fail by name
# if the layout changes.
_PROXY_TRACER_FIELDS = (
    "_instrumenting_module_name",
    "_instrumenting_library_version",
    "_schema_url",
    "_attributes",
    "_real_tracer",
)

pytest_plugins = ("tests.harness.logging_isolation",)


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
def isolated_meter_provider() -> Iterator[None]:
    """Give one test an unset global ``MeterProvider`` and restore the previous one after.

    This is the suite's OTel metrics isolation contract: the test starts from
    ``reset_metrics()`` (no provider, ``configure_metrics()`` armed), and on
    teardown the previous global provider and the ``configure_metrics()`` /
    Prometheus guard flags are put back, so a provider installed by a wider
    scope survives the test.
    """
    previous_provider = metrics._internal._METER_PROVIDER
    previous_configured = shared_metrics._configured
    previous_prometheus = shared_metrics._prometheus_active
    reset_metrics()
    yield
    reset_metrics()
    if previous_provider is not None:
        metrics.set_meter_provider(previous_provider)
    shared_metrics._configured = previous_configured
    shared_metrics._prometheus_active = previous_prometheus


@pytest.fixture()
def isolated_tracer_provider() -> Iterator[None]:
    """Give one test an unset global ``TracerProvider`` and restore the previous one after.

    The tracing half of the suite's OTel isolation contract; see
    ``isolated_meter_provider``.
    """
    previous_provider = trace._TRACER_PROVIDER
    reset_tracing()
    yield
    reset_tracing()
    if previous_provider is not None:
        trace.set_tracer_provider(previous_provider)


@pytest.fixture()
def otel_metric_reader(isolated_meter_provider: None) -> Iterator[InMemoryMetricReader]:
    """Install a fresh in-memory global ``MeterProvider`` for one test.

    Proxy instruments rebind to the provider installed here, so the returned
    reader sees every measurement the test makes. That holds only for
    instruments whose meter was created while no concrete ``MeterProvider`` was
    installed: a meter obtained while one is set is bound to it for good. So no
    test module may install a provider at import time (enforced by
    ``tests/arch/test_no_import_time_otel_providers.py``). Teardown follows
    ``isolated_meter_provider``.
    """
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    yield reader
    provider.shutdown()


@pytest.fixture()
def otel_span_exporter(
    isolated_tracer_provider: None, monkeypatch: pytest.MonkeyPatch
) -> Iterator[InMemorySpanExporter]:
    """Install a fresh in-memory global ``TracerProvider`` for one test.

    Spans from module-level ``trace.get_tracer(...)`` tracers land here too. A
    ``ProxyTracer`` caches the first real tracer it resolves and never
    re-resolves it, so for this test every ``ProxyTracer`` resolves against
    this provider on each use, ignoring and leaving its cache untouched. A
    tracer obtained while a concrete provider was installed is not a proxy and
    stays bound to that provider. Teardown follows ``isolated_tracer_provider``.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Patching ``ProxyTracer._tracer`` depends on OTel internals; check their
    # shape first so an SDK upgrade that changes them fails here, by name.
    probe_fields = vars(trace.ProxyTracer("tests.otel_probe"))
    missing = [field for field in _PROXY_TRACER_FIELDS if field not in probe_fields]
    assert not missing and isinstance(
        inspect.getattr_static(trace.ProxyTracer, "_tracer", None), property
    ), (
        "opentelemetry.trace.ProxyTracer internals changed (missing fields "
        f"{missing} or `_tracer` is no longer a property); update "
        "otel_span_exporter in tests/conftest.py for the installed OTel version"
    )

    def _resolve_against_test_provider(self: trace.ProxyTracer) -> trace.Tracer:
        return provider.get_tracer(
            self._instrumenting_module_name,
            self._instrumenting_library_version,
            self._schema_url,
            self._attributes,
        )

    monkeypatch.setattr(trace.ProxyTracer, "_tracer", property(_resolve_against_test_provider))

    # A proxy already bound to another provider must still reach this exporter.
    stale_provider = TracerProvider()
    probe = trace.ProxyTracer("tests.otel_probe")
    probe._real_tracer = stale_provider.get_tracer("tests.otel_probe")
    with probe.start_as_current_span("tests.otel_probe.current"):
        pass
    probe.start_span("tests.otel_probe.manual").end()
    stale_provider.shutdown()
    probed = [span.name for span in exporter.get_finished_spans()]
    assert probed == ["tests.otel_probe.current", "tests.otel_probe.manual"], (
        "a ProxyTracer no longer routes spans through its `_tracer` property; "
        "update otel_span_exporter in tests/conftest.py for the installed OTel version"
    )
    exporter.clear()

    yield exporter
    provider.shutdown()
