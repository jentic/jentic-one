"""Logging, metrics, and tracing configuration."""

from __future__ import annotations

import itertools
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator

from jentic_one.shared.config._common import _csv_to_list


class LoggingConfig(BaseModel):
    """File logging sink (in addition to stdout)."""

    file_enabled: bool = False
    file_dir: str = ".jentic/logs"
    file_name: str = "app.log"
    file_max_bytes: int = 10 * 1024 * 1024  # 10 MB
    file_backup_count: int = 5
    http_wire_trace: bool = Field(
        default=False,
        description=(
            "Let the outbound wire-trace DEBUG loggers (httpcore, hpack) through when "
            "the log level is DEBUG. Off by default: those lines can quote outbound "
            "header values and request paths with their query strings, including "
            "injected credentials, unredacted. Not safe for production; enable only "
            "for short-lived local debugging."
        ),
    )


#: Default ``observability.metrics.latency_buckets_ms``: dense below 25 ms (the
#: OTel defaults 0, 5, 10, 25, … can't resolve broker overhead) with a tail up
#: to the default 30 s upstream timeout.
DEFAULT_LATENCY_BUCKETS_MS: tuple[float, ...] = (
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

#: Upper bound on ``latency_buckets_ms`` length — each boundary is one exported
#: series per histogram label set, so an unbounded list is a cardinality risk.
MAX_LATENCY_BUCKETS = 64


class MetricsConfig(BaseModel):
    """Metrics exporter configuration."""

    exporter: Literal["otlp", "prometheus", "none"] = "otlp"
    export_interval_seconds: int = 15
    latency_buckets_ms: Annotated[
        list[Annotated[float, Field(gt=0, allow_inf_nan=False)]],
        BeforeValidator(_csv_to_list),
    ] = Field(
        default=list(DEFAULT_LATENCY_BUCKETS_MS),
        min_length=1,
        max_length=MAX_LATENCY_BUCKETS,
        description=(
            "Explicit histogram bucket boundaries (milliseconds, strictly increasing, "
            "all > 0) for the request-latency histograms listed in "
            "docs/operations/monitoring.md; seconds-unit histograms get the list "
            "divided by 1000. Must be identical on every process whose series are "
            "aggregated together. As an env var, a comma-separated list."
        ),
    )

    @field_validator("latency_buckets_ms", mode="after")
    @classmethod
    def _strictly_increasing(cls, value: list[float]) -> list[float]:
        if any(later <= earlier for earlier, later in itertools.pairwise(value)):
            raise ValueError("latency_buckets_ms must be strictly increasing")
        return value


class TracingConfig(BaseModel):
    """Tracing exporter configuration."""

    exporter: Literal["otlp", "none"] = Field(
        default="otlp",
        description=(
            "Where spans go. ``none`` records no spans at all (every span is "
            "non-recording), while trace ids are still generated and propagated: "
            "an inbound ``traceparent`` is honoured, outbound requests carry "
            "``traceparent``/``tracestate``, and execution records and logs keep "
            "their ``trace_id``. With ``none`` the outbound ``traceparent`` has "
            "the sampled flag unset (``-00``) even when the caller sent ``-01``, "
            "since this process records nothing."
        ),
    )
    sample_ratio: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of traces recorded and exported when ``exporter`` is "
            "``otlp`` (1.0 records every trace). It caps every trace entering "
            "the process, including requests that arrive with a sampled "
            "``traceparent`` (the CLI and MCP send one on every call), so a "
            "caller cannot force recording. A caller's unsampled ``traceparent`` "
            "stays unsampled. Ignored with ``exporter: none``, and overridden by "
            "the standard ``OTEL_TRACES_SAMPLER`` environment variable when that "
            "is set (a warning is logged if this field is not 1.0)."
        ),
    )


class ObservabilityConfig(BaseModel):
    """Observability settings (metrics, tracing knobs)."""

    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    tracing: TracingConfig = Field(default_factory=TracingConfig)
