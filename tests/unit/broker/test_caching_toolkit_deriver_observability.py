"""Unit tests asserting cache hit/miss counters on CachingToolkitDeriver."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import jentic_one.broker.repos.caching_toolkit_deriver as _deriver_mod
from jentic_one.broker.repos.caching_toolkit_deriver import CachingToolkitDeriver
from jentic_one.shared.broker.protocols import ToolkitDerivation


class _CountingDeriver:
    """A ToolkitDeriverProtocol stub that counts calls."""

    def __init__(self, result: list[str] | None = None) -> None:
        self.result = result if result is not None else ["tk_1"]
        self.calls = 0

    async def derive_toolkits(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> ToolkitDerivation:
        self.calls += 1
        tk = tuple(self.result)
        return ToolkitDerivation(
            toolkits=tk,
            agent_bound_any=bool(tk),
            api_served_toolkits=tk,
            identity_mismatch=None,
        )


def _get_counter_value(reader: InMemoryMetricReader, name: str) -> int:
    """Extract the cumulative value for a counter from the in-memory reader."""
    data = reader.get_metrics_data()
    if data is None:
        return 0
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                if metric.name == name:
                    total = 0
                    for point in metric.data.data_points:
                        total += int(getattr(point, "value", 0))
                    return total
    return 0


@pytest.fixture
def metric_reader() -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test")

    orig_hits = _deriver_mod._cache_hits
    orig_misses = _deriver_mod._cache_misses
    _deriver_mod._cache_hits = meter.create_counter("broker.toolkit_cache.hits")
    _deriver_mod._cache_misses = meter.create_counter("broker.toolkit_cache.misses")
    yield reader
    _deriver_mod._cache_hits = orig_hits
    _deriver_mod._cache_misses = orig_misses
    provider.shutdown()


@pytest.mark.asyncio
async def test_cache_miss_increments_counter(metric_reader: InMemoryMetricReader) -> None:
    """A cold lookup increments cache_misses."""
    inner = _CountingDeriver(["tk_1"])
    cache = CachingToolkitDeriver(inner, cache_ttl_seconds=300.0)

    await cache.derive_toolkits(agent_id="a1", vendor="acme", name="api", version="1")

    assert _get_counter_value(metric_reader, "broker.toolkit_cache.misses") == 1
    assert _get_counter_value(metric_reader, "broker.toolkit_cache.hits") == 0


@pytest.mark.asyncio
async def test_cache_hit_increments_counter(metric_reader: InMemoryMetricReader) -> None:
    """A warm lookup increments cache_hits."""
    inner = _CountingDeriver(["tk_1"])
    cache = CachingToolkitDeriver(inner, cache_ttl_seconds=300.0)

    await cache.derive_toolkits(agent_id="a1", vendor="acme", name="api", version="1")
    await cache.derive_toolkits(agent_id="a1", vendor="acme", name="api", version="1")

    assert _get_counter_value(metric_reader, "broker.toolkit_cache.hits") == 1
    assert _get_counter_value(metric_reader, "broker.toolkit_cache.misses") == 1


@pytest.mark.asyncio
async def test_distinct_keys_each_miss(metric_reader: InMemoryMetricReader) -> None:
    """Different keys each produce a cache miss."""
    inner = _CountingDeriver(["tk_1"])
    cache = CachingToolkitDeriver(inner, cache_ttl_seconds=300.0)

    await cache.derive_toolkits(agent_id="a1", vendor="acme", name="api", version="1")
    await cache.derive_toolkits(agent_id="a2", vendor="acme", name="api", version="1")

    assert _get_counter_value(metric_reader, "broker.toolkit_cache.misses") == 2
    assert _get_counter_value(metric_reader, "broker.toolkit_cache.hits") == 0
