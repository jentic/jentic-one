"""Monitoring response schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

__all__ = [
    "DailyExecutionBucket",
    "ExecutionStatsResponse",
    "GroupBy",
    "TopOperation",
    "UsageBucket",
    "UsageResponse",
    "UsageStatsBlock",
    "UsageTopRow",
]


class GroupBy(StrEnum):
    """Grouping dimension for usage statistics.

    ``TOOLKIT`` is a legacy axis: it groups over the surviving
    ``execution_records.toolkit_id`` attribution column, which nothing writes
    since the toolkit path was deleted (theme-5 Phase 6b). It stays so
    historical dashboards keep working; ``CREDENTIAL`` is the live
    consumer axis (execution records carry ``credential_id`` since Phase 2).
    """

    API = "api"
    TOOLKIT = "toolkit"
    CREDENTIAL = "credential"
    AGENT = "agent"


class DailyExecutionBucket(BaseModel):
    """Execution counts for a single day."""

    date: str = Field(description="The day the bucket covers, as `YYYY-MM-DD`.")
    total: int = Field(description="Number of executions started that day.")
    success: int = Field(description="Number of those executions that completed.")
    failed: int = Field(description="Number of those executions that failed.")


class TopOperation(BaseModel):
    """Aggregated execution counts for a single operation."""

    api_vendor: str = Field(description="Vendor of the API the operation belongs to.")
    api_name: str = Field(description="Name of the API the operation belongs to.")
    operation_id: str = Field(description="Identifier of the operation.")
    total: int = Field(description="Number of executions of the operation in the window.")
    failed: int = Field(description="Number of those executions that failed.")


class ExecutionStatsResponse(BaseModel):
    """Aggregated execution statistics for the dashboard."""

    total_executions: int = Field(description="Number of executions started in the window.")
    success_rate_percent: float = Field(
        description=(
            "Share of executions in the window that completed, as a percentage rounded to "
            "two decimals; 0 when there were none."
        )
    )
    daily_buckets: list[DailyExecutionBucket] = Field(
        description="Per-day execution counts, oldest first; days with no executions are omitted."
    )
    top_operations: list[TopOperation] = Field(
        description="The most-executed operations in the window, most executions first (up to 10)."
    )


class UsageStatsBlock(BaseModel):
    """Overall usage statistics for a time window."""

    total: int = Field(description="Number of executions started in the window.")
    success: int = Field(description="Number of those executions that completed.")
    failed: int = Field(description="Number of those executions that failed.")
    pending: int = Field(description="Reserved count of pending executions; currently always 0.")
    avg_ms: float = Field(
        description="Mean execution duration in milliseconds; 0 when there were no executions."
    )
    p50_ms: float | None = Field(
        description="Median execution duration in milliseconds; null when not available."
    )
    p95_ms: float | None = Field(
        description="95th-percentile execution duration in milliseconds; null when not available."
    )
    active_now: int = Field(
        description="Reserved count of in-flight executions; currently always 0."
    )


class UsageBucket(BaseModel):
    """Execution counts for a single time bucket."""

    ts: int = Field(description="Start of the bucket, in Unix epoch seconds.")
    total: int = Field(description="Number of executions started in the bucket.")
    success: int = Field(description="Number of those executions that completed.")
    failed: int = Field(description="Number of those executions that failed.")
    avg_ms: float = Field(description="Mean execution duration in the bucket, in milliseconds.")


class UsageTopRow(BaseModel):
    """Top entity row with sparkline trend."""

    key: str = Field(
        description=(
            "Grouping key for the row: `vendor/name` for `api`, the toolkit or credential ID, "
            "or `actor_type/actor_id` for `agent`; `unknown` when the value is missing."
        )
    )
    label: str = Field(description="Display label for the row.")
    total: int = Field(description="Number of executions for this entity in the window.")
    success: int = Field(description="Number of those executions that completed.")
    failed: int = Field(description="Number of those executions that failed.")
    avg_ms: float = Field(description="Mean execution duration for this entity, in milliseconds.")
    trend: list[int] = Field(
        description=(
            "Execution counts for this entity across equal segments of the window, oldest first."
        )
    )


class UsageResponse(BaseModel):
    """Full usage statistics response."""

    since: int = Field(description="Start of the window, in Unix epoch seconds (inclusive).")
    until: int = Field(description="End of the window, in Unix epoch seconds (exclusive).")
    bucket_seconds: int = Field(
        description="Width of each entry in `buckets`, in seconds; chosen from the window length."
    )
    group_by: str = Field(description="Dimension the `top` rows are grouped by.")
    stats: UsageStatsBlock
    buckets: list[UsageBucket] = Field(
        description="Execution counts per time bucket, oldest first; empty buckets are omitted."
    )
    top: list[UsageTopRow] = Field(
        description="The entities with the most executions in the window, most first."
    )
