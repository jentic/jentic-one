"""Monitoring response schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

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

    ``TOOLKIT`` is deprecated (theme-5 Phase 5b) and will be removed one
    release later, with the toolkit tables (Phase 6b): execution records
    carry a ``credential_id`` since Phase 2 and the direct-binding path
    writes no ``toolkit_id``, so ``CREDENTIAL`` is the replacement axis.
    """

    API = "api"
    TOOLKIT = "toolkit"
    CREDENTIAL = "credential"
    AGENT = "agent"


class DailyExecutionBucket(BaseModel):
    """Execution counts for a single day."""

    date: str
    total: int
    success: int
    failed: int


class TopOperation(BaseModel):
    """Aggregated execution counts for a single operation."""

    api_vendor: str
    api_name: str
    operation_id: str
    total: int
    failed: int


class ExecutionStatsResponse(BaseModel):
    """Aggregated execution statistics for the dashboard."""

    total_executions: int
    success_rate_percent: float
    daily_buckets: list[DailyExecutionBucket]
    top_operations: list[TopOperation]


class UsageStatsBlock(BaseModel):
    """Overall usage statistics for a time window."""

    total: int
    success: int
    failed: int
    pending: int
    avg_ms: float
    p50_ms: float | None
    p95_ms: float | None
    active_now: int


class UsageBucket(BaseModel):
    """Execution counts for a single time bucket."""

    ts: int
    total: int
    success: int
    failed: int
    avg_ms: float


class UsageTopRow(BaseModel):
    """Top entity row with sparkline trend."""

    key: str
    label: str
    total: int
    success: int
    failed: int
    avg_ms: float
    trend: list[int]


class UsageResponse(BaseModel):
    """Full usage statistics response."""

    since: int
    until: int
    bucket_seconds: int
    group_by: str
    stats: UsageStatsBlock
    buckets: list[UsageBucket]
    top: list[UsageTopRow]
