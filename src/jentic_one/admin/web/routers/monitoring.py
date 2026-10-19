"""Monitoring router — execution dashboard statistics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from jentic_one.admin.services.monitoring_service import MonitoringService, UsageFilters
from jentic_one.admin.web.deps import get_monitoring_service
from jentic_one.admin.web.removed_params import log_ignored_query_params
from jentic_one.admin.web.schemas.monitoring import (
    DailyExecutionBucket,
    ExecutionStatsResponse,
    GroupBy,
    TopOperation,
    UsageBucket,
    UsageResponse,
    UsageStatsBlock,
    UsageTopRow,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web import openapi_examples as ex

router = APIRouter()


@router.get("/monitoring/executions")
async def get_execution_stats(
    identity: Identity = get_current_identity(required_permissions=["org:admin"]),
    svc: MonitoringService = Depends(get_monitoring_service),
    days: int = Query(
        default=7,
        ge=1,
        le=30,
        description="Number of days back from now to aggregate over (1-30, default 7).",
        openapi_examples=ex.example(7),
    ),
) -> ExecutionStatsResponse:
    """Return aggregated execution statistics for the dashboard."""
    stats = await svc.get_execution_stats(days)
    return ExecutionStatsResponse(
        total_executions=stats.total_executions,
        success_rate_percent=stats.success_rate_percent,
        daily_buckets=[
            DailyExecutionBucket(
                date=b.date,
                total=b.total,
                success=b.success,
                failed=b.failed,
            )
            for b in stats.daily_buckets
        ],
        top_operations=[
            TopOperation(
                api_vendor=op.api_vendor,
                api_name=op.api_name,
                operation_id=op.operation_id,
                total=op.total,
                failed=op.failed,
            )
            for op in stats.top_operations
        ],
    )


@router.get("/monitoring/usage")
async def get_usage_stats(
    request: Request,
    identity: Identity = get_current_identity(required_permissions=["org:admin"]),
    svc: MonitoringService = Depends(get_monitoring_service),
    since: int | None = Query(
        default=None,
        description=(
            "Start of the window in Unix epoch seconds (inclusive); defaults to 24 hours before "
            "`until`."
        ),
        openapi_examples=ex.example(ex.EPOCH_SINCE),
    ),
    until: int | None = Query(
        default=None,
        description=(
            "End of the window in Unix epoch seconds (exclusive); defaults to the next minute "
            "boundary after now."
        ),
        openapi_examples=ex.example(ex.EPOCH_UNTIL),
    ),
    group_by: GroupBy | None = Query(
        default=None,
        description="Dimension to group the `top` rows by; defaults to `api`.",
        openapi_examples=ex.example("api"),
    ),
    top_limit: int = Query(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of `top` rows to return (1-50, default 10).",
        openapi_examples=ex.example(10),
    ),
    api_id: str | None = Query(
        default=None,
        description="Only count executions of this API, given as `vendor/name`.",
        openapi_examples=ex.example(ex.API_VENDOR_NAME),
    ),
    agent_id: str | None = Query(
        default=None,
        description="Only count executions run by this actor ID.",
        openapi_examples=ex.example(ex.AGENT_ID),
    ),
    status: str | None = Query(
        default=None,
        description="Only count executions with this status, e.g. `completed` or `failed`.",
        openapi_examples=ex.example("completed"),
    ),
) -> UsageResponse:
    """Return detailed usage statistics for the monitoring overview."""
    log_ignored_query_params(request, identity)
    resolved_group_by = group_by if group_by is not None else GroupBy.API
    filters = UsageFilters(api_id=api_id, agent_id=agent_id, status=status)
    result = await svc.get_usage_stats(
        since=since, until=until, group_by=resolved_group_by, top_limit=top_limit, filters=filters
    )
    return UsageResponse(
        since=result.since,
        until=result.until,
        bucket_seconds=result.bucket_seconds,
        group_by=result.group_by,
        stats=UsageStatsBlock(
            total=result.total,
            success=result.success,
            failed=result.failed,
            pending=result.pending,
            avg_ms=result.avg_ms,
            p50_ms=result.p50_ms,
            p95_ms=result.p95_ms,
            active_now=result.active_now,
        ),
        buckets=[
            UsageBucket(
                ts=b["ts"],
                total=b["total"],
                success=b["success"],
                failed=b["failed"],
                avg_ms=b["avg_ms"],
            )
            for b in result.buckets
        ],
        top=[
            UsageTopRow(
                key=t["key"],
                label=t["label"],
                total=t["total"],
                success=t["success"],
                failed=t["failed"],
                avg_ms=t["avg_ms"],
                trend=t["trend"],
            )
            for t in result.top
        ],
    )
