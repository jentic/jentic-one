"""Catalog-source sync telemetry.

Instruments go through the ``shared/metrics.py`` facade and are created lazily on
first use, so importing this module has no side effects. ``source`` is always a
configured slug, so label cardinality is bounded by configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jentic_one.shared.metrics import get_meter

if TYPE_CHECKING:
    from opentelemetry.metrics import Counter, Histogram

_METER_NAME = "jentic_one.catalog_sources"

_counters: dict[str, Counter] = {}
_histograms: dict[str, Histogram] = {}
_gauges: dict[str, Any] = {}


def _counter(name: str, description: str) -> Counter:
    counter = _counters.get(name)
    if counter is None:
        counter = get_meter(_METER_NAME).create_counter(name, description=description)
        _counters[name] = counter
    return counter


def _gauge(name: str, description: str) -> Any:
    gauge = _gauges.get(name)
    if gauge is None:
        gauge = get_meter(_METER_NAME).create_gauge(name, description=description)
        _gauges[name] = gauge
    return gauge


def record_sync(source: str, status: str, duration_seconds: float) -> None:
    """One sync run finished with ``status``."""
    _counter("catalog_source_sync_total", "Catalog source sync runs by outcome.").add(
        1, {"source": source, "status": status}
    )
    histogram = _histograms.get("duration")
    if histogram is None:
        histogram = get_meter(_METER_NAME).create_histogram(
            "catalog_source_sync_duration_seconds",
            unit="s",
            description="Wall-clock duration of one catalog source sync.",
        )
        _histograms["duration"] = histogram
    histogram.record(duration_seconds, {"source": source})


def record_entries(source: str, live_entries: int) -> None:
    """The live entry count after an apply."""
    _gauge("catalog_source_entries", "Live entries per catalog source.").set(
        live_entries, {"source": source}
    )


def record_stale_seconds(source: str, seconds: float) -> None:
    """Seconds since the source last synced successfully."""
    _gauge("catalog_source_stale_seconds", "Seconds since the last successful sync.").set(
        seconds, {"source": source}
    )


def record_namespaced(source: str) -> None:
    """The resolver namespaced a hint that claimed a curated vendor."""
    _counter(
        "catalog_source_id_namespaced_total",
        "Hints namespaced because they claimed a curated vendor.",
    ).add(1, {"source": source})


def record_mapping_unmatched(source: str, count: int) -> None:
    """How many ``catalog_api_id_mappings`` rows of ``source`` match no curated id."""
    _gauge(
        "catalog_mapping_unmatched_rows",
        "catalog_api_id mapping rows that match no curated id (ignored).",
    ).set(count, {"source": source})


def record_ownership_conflicts(source: str, count: int) -> None:
    """How many imported APIs of ``source`` hold a catalog_api_id a curated source owns.

    A gauge set on every apply and re-resolve of ``source``, so re-counting the
    same APIs never inflates it and resolved conflicts drop back to zero.
    """
    _gauge(
        "catalog_ownership_conflicts",
        "Imported APIs whose catalog_api_id is now owned by a curated source.",
    ).add(count, {"source": source})


#: ``catalog_source_fetch_total`` outcomes.
FETCH_BODY = "body"
FETCH_NOT_MODIFIED = "not_modified"
FETCH_TRANSIENT = "transient"
FETCH_GONE = "gone"


def record_fetch(source: str, outcome: str) -> None:
    """One request to a source ended with ``outcome`` (body, not_modified, transient, gone)."""
    _counter("catalog_source_fetch_total", "Catalog source requests by outcome.").add(
        1, {"source": source, "outcome": outcome}
    )


def record_bucket_wait(source: str, lane: str, seconds: float) -> None:
    """Time one request waited for a concurrency slot and a rate token."""
    histogram = _histograms.get("bucket_wait")
    if histogram is None:
        histogram = get_meter(_METER_NAME).create_histogram(
            "catalog_source_bucket_wait_seconds",
            unit="s",
            description="Time a catalog source request waited for its fetch limits.",
        )
        _histograms["bucket_wait"] = histogram
    histogram.record(max(seconds, 0.0), {"source": source, "class": lane})
