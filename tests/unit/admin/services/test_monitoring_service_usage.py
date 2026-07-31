"""Unit tests for MonitoringService.get_usage_stats method."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.admin.repos.monitoring_repo import (
    GroupedTopRow,
    OverallStatsRow,
    TimeBucketRow,
    UsageQueryFilters,
)
from jentic_one.admin.services.monitoring_service import (
    _CACHE_TTL_SECONDS,
    _USAGE_CACHE_MAX_ENTRIES,
    MonitoringService,
    UsageFilters,
    UsageStats,
    _UsageCacheEntry,
)
from jentic_one.admin.web.schemas.monitoring import GroupBy


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.admin_db = MagicMock()
    return ctx


def _sample_stats_row() -> OverallStatsRow:
    return OverallStatsRow(
        total=100, success=80, failed=20, avg_ms=250.0, p50_ms=200.0, p95_ms=500.0
    )


def _sample_bucket_rows() -> list[TimeBucketRow]:
    return [
        TimeBucketRow(ts=1719792000, total=50, success=40, failed=10, avg_ms=100.0),
        TimeBucketRow(ts=1719795600, total=30, success=28, failed=2, avg_ms=80.0),
    ]


def _sample_top_rows() -> list[GroupedTopRow]:
    return [
        GroupedTopRow(
            key="stripe/payments",
            label="stripe/payments",
            total=50,
            success=45,
            failed=5,
            avg_ms=200.0,
        ),
    ]


def _sample_trends() -> dict[str, list[int]]:
    return {"stripe/payments": [5, 3, 4, 6, 2, 3, 5, 4, 7, 3, 2, 6]}


@pytest.mark.asyncio
async def test_usage_cache_returns_cached_result_within_ttl() -> None:
    ctx = _make_ctx()
    svc = MonitoringService(ctx)
    cached = UsageStats(
        since=1000,
        until=87400,
        bucket_seconds=3600,
        group_by="api",
        total=100,
        success=80,
        failed=20,
        pending=0,
        avg_ms=250.0,
        p50_ms=200.0,
        p95_ms=500.0,
        active_now=0,
        buckets=[],
        top=[],
    )
    cache_key = (1000, 87400, GroupBy.API, 10, UsageFilters())
    svc._usage_cache[cache_key] = _UsageCacheEntry(result=cached, cached_at=time.monotonic())

    result = await svc.get_usage_stats(since=1000, until=87400, group_by=GroupBy.API, top_limit=10)
    assert result is cached


@pytest.mark.asyncio
async def test_default_since_until_resolution() -> None:
    ctx = _make_ctx()
    svc = MonitoringService(ctx)

    mock_now = datetime(2024, 7, 2, 0, 0, 15, tzinfo=UTC)
    with (
        patch("jentic_one.admin.services.monitoring_service.datetime") as mock_datetime,
        patch.object(svc, "_query_usage", new_callable=AsyncMock) as mock_query,
    ):
        mock_datetime.now.return_value = mock_now
        mock_query.return_value = UsageStats(
            since=0,
            until=0,
            bucket_seconds=3600,
            group_by="api",
            total=0,
            success=0,
            failed=0,
            pending=0,
            avg_ms=0.0,
            p50_ms=None,
            p95_ms=None,
            active_now=0,
            buckets=[],
            top=[],
        )
        await svc.get_usage_stats()
        call_args = mock_query.call_args[0]
        resolved_since, resolved_until = call_args[0], call_args[1]

    # The fallback bound is rounded UP to the next minute (#913): stable cache
    # keys within a minute, but the strict `< until` filter still covers the
    # current partial minute — executions must never be visible in the feed
    # yet missing from the volume aggregate.
    expected_until = (int(mock_now.timestamp()) // 60 + 1) * 60
    expected_since = expected_until - 86400
    assert resolved_until == expected_until
    assert resolved_since == expected_since
    assert resolved_until % 60 == 0
    assert resolved_until > int(mock_now.timestamp())
    assert resolved_until - resolved_since == 86400


@pytest.mark.asyncio
async def test_cached_entry_expires_after_ttl() -> None:
    """An entry older than the TTL is a miss — the aggregate is re-queried.

    The TTL is deliberately short (30s, #913): with the fallback window stable
    for a whole minute, a longer TTL would pin the minute's FIRST response and
    hide executions arriving mid-minute from the volume chart while the
    executions feed already shows them.
    """
    ctx = _make_ctx()
    svc = MonitoringService(ctx)
    cached = UsageStats(
        since=1000,
        until=87400,
        bucket_seconds=3600,
        group_by="api",
        total=1,
        success=1,
        failed=0,
        pending=0,
        avg_ms=1.0,
        p50_ms=None,
        p95_ms=None,
        active_now=0,
        buckets=[],
        top=[],
    )
    cache_key = (1000, 87400, GroupBy.API, 10, UsageFilters())
    svc._usage_cache[cache_key] = _UsageCacheEntry(
        result=cached, cached_at=time.monotonic() - (_CACHE_TTL_SECONDS + 1)
    )

    fresh = UsageStats(
        since=1000,
        until=87400,
        bucket_seconds=3600,
        group_by="api",
        total=4,
        success=4,
        failed=0,
        pending=0,
        avg_ms=1.0,
        p50_ms=None,
        p95_ms=None,
        active_now=0,
        buckets=[],
        top=[],
    )
    with patch.object(svc, "_query_usage", new_callable=AsyncMock, return_value=fresh):
        result = await svc.get_usage_stats(
            since=1000, until=87400, group_by=GroupBy.API, top_limit=10
        )
    assert result is fresh


@pytest.mark.asyncio
async def test_bucket_seconds_selection_for_various_windows() -> None:
    assert MonitoringService._compute_bucket_seconds(1800) == 60
    assert MonitoringService._compute_bucket_seconds(3600) == 60
    assert MonitoringService._compute_bucket_seconds(7200) == 3600
    assert MonitoringService._compute_bucket_seconds(86400) == 3600
    assert MonitoringService._compute_bucket_seconds(259200) == 21600
    assert MonitoringService._compute_bucket_seconds(604800) == 21600
    # A day-aligned 7d window across a fall-back DST change (7d + 1h) keeps
    # the 6h tier; only genuinely wider windows drop to daily buckets.
    assert MonitoringService._compute_bucket_seconds(608400) == 21600
    assert MonitoringService._compute_bucket_seconds(8 * 86400) == 21600
    assert MonitoringService._compute_bucket_seconds(8 * 86400 + 1) == 86400
    assert MonitoringService._compute_bucket_seconds(1209600) == 86400


def test_trend_points_follow_bucket_tier() -> None:
    # One trend segment per aggregate bucket: 24 hourly for a day, 28 six-hour
    # for a week, 30 daily for a month — so day-aligned windows produce
    # day-aligned segments (the volume chart's 7d axis shows exactly 7 days).
    assert MonitoringService._compute_trend_points(86400, 3600) == 24
    assert MonitoringService._compute_trend_points(604800, 21600) == 28
    assert MonitoringService._compute_trend_points(2592000, 86400) == 30
    # A DST-stretched 7-day window (7d + 1h) stays on the 6h tier and still
    # gets 28 segments — each slightly stretched (~6h2m), not an extra one —
    # so it remains re-bucketable into 7 day bars client-side.
    assert MonitoringService._compute_trend_points(608400, 21600) == 28
    # Capped at 60 (1h window / 60s buckets, or pathologically wide windows).
    assert MonitoringService._compute_trend_points(3600, 60) == 60
    assert MonitoringService._compute_trend_points(86400 * 365, 86400) == 60
    # Never below one segment.
    assert MonitoringService._compute_trend_points(30, 60) == 1


@pytest.mark.asyncio
async def test_pending_and_active_now_always_zero() -> None:
    ctx = _make_ctx()
    svc = MonitoringService(ctx)

    with patch.object(svc, "_query_usage", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = UsageStats(
            since=1000,
            until=87400,
            bucket_seconds=3600,
            group_by="api",
            total=100,
            success=80,
            failed=20,
            pending=0,
            avg_ms=250.0,
            p50_ms=200.0,
            p95_ms=500.0,
            active_now=0,
            buckets=[],
            top=[],
        )
        result = await svc.get_usage_stats(since=1000, until=87400)
        assert result.pending == 0
        assert result.active_now == 0


@pytest.mark.asyncio
async def test_filters_are_included_in_cache_key() -> None:
    ctx = _make_ctx()
    svc = MonitoringService(ctx)

    cached1 = UsageStats(
        since=1000,
        until=87400,
        bucket_seconds=3600,
        group_by="api",
        total=100,
        success=80,
        failed=20,
        pending=0,
        avg_ms=250.0,
        p50_ms=200.0,
        p95_ms=500.0,
        active_now=0,
        buckets=[],
        top=[],
    )
    cached2 = UsageStats(
        since=1000,
        until=87400,
        bucket_seconds=3600,
        group_by="api",
        total=50,
        success=40,
        failed=10,
        pending=0,
        avg_ms=150.0,
        p50_ms=100.0,
        p95_ms=300.0,
        active_now=0,
        buckets=[],
        top=[],
    )

    key1 = (1000, 87400, GroupBy.API, 10, UsageFilters())
    key2 = (1000, 87400, GroupBy.API, 10, UsageFilters(toolkit_id="tk_abc"))
    svc._usage_cache[key1] = _UsageCacheEntry(result=cached1, cached_at=time.monotonic())
    svc._usage_cache[key2] = _UsageCacheEntry(result=cached2, cached_at=time.monotonic())

    result1 = await svc.get_usage_stats(since=1000, until=87400)
    result2 = await svc.get_usage_stats(
        since=1000, until=87400, filters=UsageFilters(toolkit_id="tk_abc")
    )
    assert result1.total == 100
    assert result2.total == 50


@pytest.mark.asyncio
async def test_query_usage_assembles_response_correctly() -> None:
    ctx = _make_ctx()
    svc = MonitoringService(ctx)

    with (
        patch(
            "jentic_one.admin.services.monitoring_service.MonitoringRepository.overall_stats",
            new_callable=AsyncMock,
            return_value=_sample_stats_row(),
        ),
        patch(
            "jentic_one.admin.services.monitoring_service.MonitoringRepository.time_buckets",
            new_callable=AsyncMock,
            return_value=_sample_bucket_rows(),
        ),
        patch(
            "jentic_one.admin.services.monitoring_service.MonitoringRepository.grouped_top",
            new_callable=AsyncMock,
            return_value=_sample_top_rows(),
        ),
        patch(
            "jentic_one.admin.services.monitoring_service.MonitoringRepository.grouped_trend",
            new_callable=AsyncMock,
            return_value=_sample_trends(),
        ) as mock_trend,
    ):
        result = await svc.get_usage_stats(since=1719792000, until=1719878400)

    # 24h window / hourly buckets → 24 trend segments requested.
    assert mock_trend.call_args[1]["num_points"] == 24
    assert result.total == 100
    assert result.success == 80
    assert result.failed == 20
    assert result.pending == 0
    assert result.active_now == 0
    assert result.avg_ms == 250.0
    assert result.p50_ms == 200.0
    assert result.p95_ms == 500.0
    assert len(result.buckets) == 2
    assert len(result.top) == 1
    assert result.top[0]["trend"] == [5, 3, 4, 6, 2, 3, 5, 4, 7, 3, 2, 6]


@pytest.mark.asyncio
async def test_filters_are_passed_to_repository() -> None:
    ctx = _make_ctx()
    svc = MonitoringService(ctx)
    filters = UsageFilters(toolkit_id="tk_abc", api_id="stripe/payments", agent_id=None)

    with (
        patch(
            "jentic_one.admin.services.monitoring_service.MonitoringRepository.overall_stats",
            new_callable=AsyncMock,
            return_value=_sample_stats_row(),
        ) as mock_stats,
        patch(
            "jentic_one.admin.services.monitoring_service.MonitoringRepository.time_buckets",
            new_callable=AsyncMock,
            return_value=_sample_bucket_rows(),
        ) as mock_buckets,
        patch(
            "jentic_one.admin.services.monitoring_service.MonitoringRepository.grouped_top",
            new_callable=AsyncMock,
            return_value=_sample_top_rows(),
        ) as mock_top,
        patch(
            "jentic_one.admin.services.monitoring_service.MonitoringRepository.grouped_trend",
            new_callable=AsyncMock,
            return_value=_sample_trends(),
        ),
    ):
        await svc.get_usage_stats(since=1719792000, until=1719878400, filters=filters)

    expected_filters = UsageQueryFilters(
        toolkit_id="tk_abc", api_vendor="stripe", api_name="payments"
    )
    assert mock_stats.call_args[1]["filters"] == expected_filters
    assert mock_buckets.call_args[1]["filters"] == expected_filters
    assert mock_top.call_args[1]["filters"] == expected_filters


@pytest.mark.asyncio
async def test_empty_filters_pass_none_to_repository() -> None:
    ctx = _make_ctx()
    svc = MonitoringService(ctx)
    filters = UsageFilters()

    with (
        patch(
            "jentic_one.admin.services.monitoring_service.MonitoringRepository.overall_stats",
            new_callable=AsyncMock,
            return_value=_sample_stats_row(),
        ) as mock_stats,
        patch(
            "jentic_one.admin.services.monitoring_service.MonitoringRepository.time_buckets",
            new_callable=AsyncMock,
            return_value=_sample_bucket_rows(),
        ),
        patch(
            "jentic_one.admin.services.monitoring_service.MonitoringRepository.grouped_top",
            new_callable=AsyncMock,
            return_value=_sample_top_rows(),
        ),
        patch(
            "jentic_one.admin.services.monitoring_service.MonitoringRepository.grouped_trend",
            new_callable=AsyncMock,
            return_value=_sample_trends(),
        ),
    ):
        await svc.get_usage_stats(since=1719792000, until=1719878400, filters=filters)

    assert mock_stats.call_args[1]["filters"] is None


def test_cache_eviction_removes_stale_entries() -> None:
    ctx = _make_ctx()
    svc = MonitoringService(ctx)
    now = time.monotonic()
    stale_time = now - 200.0

    for i in range(5):
        key = (i, i + 100, GroupBy.API, 10, UsageFilters())
        svc._usage_cache[key] = _UsageCacheEntry(result=MagicMock(), cached_at=stale_time)

    fresh_key = (999, 1099, GroupBy.API, 10, UsageFilters())
    svc._usage_cache[fresh_key] = _UsageCacheEntry(result=MagicMock(), cached_at=now)

    svc._evict_stale_usage_entries(now)
    assert len(svc._usage_cache) == 1
    assert fresh_key in svc._usage_cache


def test_cache_eviction_caps_at_max_entries() -> None:
    ctx = _make_ctx()
    svc = MonitoringService(ctx)
    now = time.monotonic()

    for i in range(_USAGE_CACHE_MAX_ENTRIES + 10):
        key = (i, i + 100, GroupBy.API, 10, UsageFilters())
        svc._usage_cache[key] = _UsageCacheEntry(
            result=MagicMock(), cached_at=now - (_USAGE_CACHE_MAX_ENTRIES + 10 - i)
        )

    svc._evict_stale_usage_entries(now)
    assert len(svc._usage_cache) <= _USAGE_CACHE_MAX_ENTRIES
