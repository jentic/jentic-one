"""In-memory capture of a module-level OTel histogram, for asserting recorded points.

Instruments are created at import time against the global ``MeterProvider``;
:func:`capture_histogram` swaps one for an equivalent instrument on a private
provider for the duration of a ``with`` block.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType
from unittest.mock import patch

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import HistogramDataPoint, InMemoryMetricReader


@contextmanager
def capture_histogram(
    module: ModuleType, attribute: str, name: str, unit: str = "ms"
) -> Iterator[InMemoryMetricReader]:
    """Patch ``module.attribute`` with histogram ``name`` on an in-memory provider."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    histogram = provider.get_meter("test").create_histogram(name, unit=unit)
    try:
        with patch.object(module, attribute, histogram):
            yield reader
    finally:
        provider.shutdown()


def histogram_points(reader: InMemoryMetricReader, name: str) -> list[HistogramDataPoint]:
    """All histogram data points recorded for ``name`` so far."""
    data = reader.get_metrics_data()
    if data is None:
        return []
    return [
        point
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == name
        for point in metric.data.data_points
        if isinstance(point, HistogramDataPoint)
    ]
