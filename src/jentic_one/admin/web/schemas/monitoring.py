"""Monitoring response schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from jentic_one.shared.web import openapi_examples as ex

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

    date: str = Field(
        description="The day the bucket covers, as `YYYY-MM-DD`.", examples=["2026-01-15"]
    )
    total: int = Field(description="Number of executions started that day.", examples=[128])
    success: int = Field(description="Number of those executions that completed.", examples=[120])
    failed: int = Field(description="Number of those executions that failed.", examples=[8])


class TopOperation(BaseModel):
    """Aggregated execution counts for a single operation."""

    api_vendor: str = Field(
        description="Vendor of the API the operation belongs to.", examples=[ex.API_VENDOR]
    )
    api_name: str = Field(
        description="Name of the API the operation belongs to.", examples=[ex.API_NAME]
    )
    operation_id: str = Field(
        description="Identifier of the operation.", examples=[ex.OPERATION_ID]
    )
    total: int = Field(
        description="Number of executions of the operation in the window.", examples=[342]
    )
    failed: int = Field(description="Number of those executions that failed.", examples=[12])


class ExecutionStatsResponse(BaseModel):
    """Aggregated execution statistics for the dashboard."""

    total_executions: int = Field(
        description="Number of executions started in the window.", examples=[1280]
    )
    success_rate_percent: float = Field(
        description=(
            "Share of executions in the window that completed, as a percentage rounded to "
            "two decimals; 0 when there were none."
        ),
        examples=[93.75],
    )
    daily_buckets: list[DailyExecutionBucket] = Field(
        description="Per-day execution counts, oldest first; days with no executions are omitted."
    )
    top_operations: list[TopOperation] = Field(
        description="The most-executed operations in the window, most executions first (up to 10)."
    )


class UsageStatsBlock(BaseModel):
    """Overall usage statistics for a time window."""

    total: int = Field(description="Number of executions started in the window.", examples=[1280])
    success: int = Field(description="Number of those executions that completed.", examples=[1200])
    failed: int = Field(description="Number of those executions that failed.", examples=[80])
    pending: int = Field(
        description=(
            "Number of asynchronous executions queued in the window that have not finished yet "
            "(still queued or running). They are not part of `total`, which counts finished "
            "executions only. A synchronous execution is counted once it finishes, never here. "
            "The `toolkit_id`, `api_id` and `agent_id` filters apply; a `status` filter makes "
            "this 0, since no unfinished execution has a final status."
        ),
        examples=[3],
    )
    avg_ms: float = Field(
        description="Mean execution duration in milliseconds; 0 when there were no executions.",
        examples=[245.6],
    )
    p50_ms: float | None = Field(
        description="Median execution duration in milliseconds; null when not available.",
        examples=[180.0],
    )
    p95_ms: float | None = Field(
        description="95th-percentile execution duration in milliseconds; null when not available.",
        examples=[720.0],
    )
    active_now: int = Field(
        description=(
            "Number of asynchronous executions a worker is running right now, regardless of the "
            "window. Queued executions not yet picked up by a worker are not counted. The same "
            "filters apply as for `pending`."
        ),
        examples=[2],
    )


class UsageBucket(BaseModel):
    """Execution counts for a single time bucket."""

    ts: int = Field(
        description="Start of the bucket, in Unix epoch seconds.", examples=[ex.EPOCH_SINCE]
    )
    total: int = Field(description="Number of executions started in the bucket.", examples=[42])
    success: int = Field(description="Number of those executions that completed.", examples=[40])
    failed: int = Field(description="Number of those executions that failed.", examples=[2])
    avg_ms: float = Field(
        description="Mean execution duration in the bucket, in milliseconds.", examples=[231.4]
    )


class UsageTopRow(BaseModel):
    """Top entity row with sparkline trend."""

    key: str = Field(
        description=(
            "Grouping key for the row: `vendor/name` for `api`, the toolkit or credential ID, "
            "or `actor_type/actor_id` for `agent`; `unknown` when the value is missing."
        ),
        examples=[ex.API_VENDOR_NAME],
    )
    label: str = Field(description="Display label for the row.", examples=["Stripe"])
    total: int = Field(
        description="Number of executions for this entity in the window.", examples=[342]
    )
    success: int = Field(description="Number of those executions that completed.", examples=[330])
    failed: int = Field(description="Number of those executions that failed.", examples=[12])
    avg_ms: float = Field(
        description="Mean execution duration for this entity, in milliseconds.", examples=[212.8]
    )
    trend: list[int] = Field(
        description=(
            "Execution counts for this entity across equal segments of the window, oldest first."
        ),
        examples=[[40, 52, 47, 61, 58, 84]],
    )


class UsageResponse(BaseModel):
    """Full usage statistics response."""

    since: int = Field(
        description="Start of the window, in Unix epoch seconds (inclusive).",
        examples=[ex.EPOCH_SINCE],
    )
    until: int = Field(
        description="End of the window, in Unix epoch seconds (exclusive).",
        examples=[ex.EPOCH_UNTIL],
    )
    bucket_seconds: int = Field(
        description="Width of each entry in `buckets`, in seconds; chosen from the window length.",
        examples=[86400],
    )
    group_by: str = Field(description="Dimension the `top` rows are grouped by.", examples=["api"])
    stats: UsageStatsBlock
    buckets: list[UsageBucket] = Field(
        description="Execution counts per time bucket, oldest first; empty buckets are omitted."
    )
    top: list[UsageTopRow] = Field(
        description="The entities with the most executions in the window, most first."
    )
