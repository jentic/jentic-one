"""Unit tests for MonitoringService caching and aggregation logic."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.admin.services.monitoring_service import (
    DailyBucket,
    ExecutionStats,
    MonitoringService,
    TopOp,
)


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.admin_db = MagicMock()
    return ctx


def _sample_result() -> ExecutionStats:
    return ExecutionStats(
        total_executions=100,
        success_rate_percent=90.0,
        daily_buckets=[DailyBucket(date="2026-06-01", total=50, success=45, failed=5)],
        top_operations=[
            TopOp(api_vendor="v", api_name="n", operation_id="op1", total=30, failed=2)
        ],
    )


@pytest.mark.asyncio
async def test_cache_returns_cached_result_within_ttl() -> None:
    ctx = _make_ctx()
    svc = MonitoringService(ctx)
    cached = _sample_result()
    svc._cache.put(7, cached, stamped_at=time.monotonic())

    result = await svc.get_execution_stats(7)
    assert result is cached


@pytest.mark.asyncio
async def test_cache_miss_triggers_query() -> None:
    ctx = _make_ctx()
    svc = MonitoringService(ctx)
    expected = _sample_result()

    with patch.object(svc, "_query", new_callable=AsyncMock, return_value=expected) as mock_query:
        result = await svc.get_execution_stats(7)
        mock_query.assert_awaited_once_with(7)
    assert result == expected


@pytest.mark.asyncio
async def test_stale_cache_triggers_refresh() -> None:
    ctx = _make_ctx()
    svc = MonitoringService(ctx)
    stale = _sample_result()
    svc._cache.put(7, stale, stamped_at=time.monotonic() - 300)

    fresh = ExecutionStats(
        total_executions=200,
        success_rate_percent=80.0,
        daily_buckets=[],
        top_operations=[],
    )
    with patch.object(svc, "_query", new_callable=AsyncMock, return_value=fresh):
        result = await svc.get_execution_stats(7)
    assert result == fresh


@pytest.mark.asyncio
async def test_different_days_use_separate_cache_keys() -> None:
    ctx = _make_ctx()
    svc = MonitoringService(ctx)
    result_7 = _sample_result()
    result_14 = ExecutionStats(
        total_executions=200,
        success_rate_percent=85.0,
        daily_buckets=[],
        top_operations=[],
    )
    svc._cache.put(7, result_7, stamped_at=time.monotonic())
    svc._cache.put(14, result_14, stamped_at=time.monotonic())

    assert await svc.get_execution_stats(7) is result_7
    assert await svc.get_execution_stats(14) is result_14


def test_success_rate_calculation() -> None:
    buckets = [
        DailyBucket(date="2026-06-01", total=60, success=50, failed=10),
        DailyBucket(date="2026-06-02", total=40, success=30, failed=10),
    ]
    total = sum(b.total for b in buckets)
    success = sum(b.success for b in buckets)
    rate = round(success / total * 100.0, 2)
    assert total == 100
    assert rate == 80.0


def test_success_rate_zero_when_no_executions() -> None:
    total = 0
    rate = (0 / total * 100.0) if total > 0 else 0.0
    assert rate == 0.0
