"""Logging, metrics, and tracing configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LoggingConfig(BaseModel):
    """File logging sink (in addition to stdout)."""

    file_enabled: bool = Field(
        default=False,
        description=(
            "Also write logs to a size-rotated file. The file is always JSON lines, "
            "whatever ``runtime.debug`` selects for stdout."
        ),
    )
    file_dir: str = Field(
        default=".jentic/logs",
        description=(
            "Directory for the log file (relative paths resolve against the working "
            "directory); created at startup if missing."
        ),
    )
    file_name: str = Field(default="app.log", description="Log file name inside ``file_dir``.")
    file_max_bytes: int = Field(
        default=10 * 1024 * 1024,
        description=(
            "Size in bytes at which the log file is rotated (default 10 MiB); 0 never rotates."
        ),
    )
    file_backup_count: int = Field(
        default=5,
        description=(
            "Number of rotated log files kept beside the active one; older ones are deleted."
        ),
    )
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


class MetricsConfig(BaseModel):
    """Metrics exporter configuration."""

    exporter: Literal["otlp", "prometheus", "none"] = Field(
        default="otlp",
        description=(
            "Where metrics go: ``otlp`` pushes to the OTLP endpoint named by the "
            "standard ``OTEL_EXPORTER_OTLP_*`` environment variables, "
            "``prometheus`` serves a scrape endpoint at ``/metrics``, and ``none`` "
            "records nothing."
        ),
    )
    export_interval_seconds: int = Field(
        default=15,
        description="Seconds between OTLP metric pushes. Ignored unless ``exporter`` is ``otlp``.",
    )


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
