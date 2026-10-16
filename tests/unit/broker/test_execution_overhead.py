"""Unit tests for the broker-overhead histogram (``broker.execution.overhead_ms``)."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
import structlog
from opentelemetry.sdk.metrics.export import HistogramDataPoint, InMemoryMetricReader

from jentic_one.broker.core.server_timing import ServerTimingRecorder, ServerTimingStage
from jentic_one.broker.services.execution import overhead
from jentic_one.broker.services.execution.overhead import (
    record_overhead,
    upstream_exclusive_elapsed_ms,
)
from tests.harness.metrics import capture_histogram, histogram_points

_OVERHEAD = "broker.execution.overhead_ms"


@pytest.fixture
def overhead_reader() -> Iterator[InMemoryMetricReader]:
    with capture_histogram(overhead, "_execution_overhead", _OVERHEAD) as reader:
        yield reader


def _points(reader: InMemoryMetricReader) -> list[HistogramDataPoint]:
    return histogram_points(reader, _OVERHEAD)


def test_upstream_exclusive_elapsed_subtracts_the_upstream_window() -> None:
    recorder = ServerTimingRecorder()
    recorder.started_at = time.perf_counter() - 0.05  # 50 ms since the request arrived
    recorder.add(ServerTimingStage.UPSTREAM, 30.0)
    recorder.add(ServerTimingStage.PERSIST, 5.0)  # broker time: not subtracted

    value = upstream_exclusive_elapsed_ms(recorder)
    assert 20.0 <= value <= recorder.elapsed_ms() - 30.0


def test_record_overhead_keeps_float_ms_and_bounded_labels(
    overhead_reader: InMemoryMetricReader,
) -> None:
    record_overhead(1.25, "getThing", "buffered")
    record_overhead(0.5, None, "streaming")

    by_mode = {dict(p.attributes or {})["mode"]: p for p in _points(overhead_reader)}
    assert by_mode["buffered"].sum == pytest.approx(1.25)
    assert dict(by_mode["buffered"].attributes or {}) == {
        "operation": "getThing",
        "mode": "buffered",
    }
    assert dict(by_mode["streaming"].attributes or {}) == {"operation": "", "mode": "streaming"}


def test_negative_overhead_is_logged_and_dropped_not_clamped(
    overhead_reader: InMemoryMetricReader,
) -> None:
    """A negative value means the upstream window was mis-wired; hiding it as 0
    would make the fault invisible, so it is surfaced in the logs instead."""
    with structlog.testing.capture_logs() as logs:
        record_overhead(-3.0, "getThing", "buffered")

    assert _points(overhead_reader) == []
    (event,) = [e for e in logs if e["event"] == "broker_overhead_negative"]
    assert event["log_level"] == "warning"
    assert event["overhead_ms"] == -3.0
    assert event["mode"] == "buffered"
