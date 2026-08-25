"""Monitoring service — bounded aggregation queries with in-process TTL cache."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from jentic_one.admin.repos import MonitoringRepository
from jentic_one.admin.repos.monitoring_repo import UsageQueryFilters
from jentic_one.admin.web.schemas.monitoring import GroupBy
from jentic_one.shared.context import Context
from jentic_one.shared.metrics import get_meter

logger = structlog.get_logger(__name__)
_meter = get_meter("admin")
_cache_hits = _meter.create_counter("monitoring.cache.hits", description="Monitoring cache hits")
_cache_misses = _meter.create_counter(
    "monitoring.cache.misses", description="Monitoring cache misses"
)

# Short enough that a repeated cache key (the fallback window is stable for a
# whole minute — see get_usage_stats) can't pin the first response of the
# minute much beyond the feed's freshness; long enough to absorb a dashboard
# fan-out's thundering herd (#913).
_CACHE_TTL_SECONDS = 30.0
_USAGE_CACHE_MAX_ENTRIES = 256


@dataclass(slots=True, frozen=True)
class DailyBucket:
    date: str
    total: int
    success: int
    failed: int


@dataclass(slots=True, frozen=True)
class TopOp:
    api_vendor: str
    api_name: str
    operation_id: str
    total: int
    failed: int


@dataclass(slots=True, frozen=True)
class ExecutionStats:
    total_executions: int
    success_rate_percent: float
    daily_buckets: list[DailyBucket]
    top_operations: list[TopOp]


@dataclass(slots=True, frozen=True)
class UsageStats:
    since: int
    until: int
    bucket_seconds: int
    group_by: str
    total: int
    success: int
    failed: int
    pending: int
    avg_ms: float
    p50_ms: float | None
    p95_ms: float | None
    active_now: int
    buckets: list[dict[str, Any]]
    top: list[dict[str, Any]]


@dataclass(slots=True)
class _CacheEntry:
    result: ExecutionStats
    cached_at: float


@dataclass(slots=True)
class _UsageCacheEntry:
    result: UsageStats
    cached_at: float


@dataclass(slots=True, frozen=True)
class UsageFilters:
    toolkit_id: str | None = field(default=None)
    api_id: str | None = field(default=None)
    agent_id: str | None = field(default=None)
    status: str | None = field(default=None)


class MonitoringService:
    """Computes execution dashboard statistics with TTL-cached, bounded queries."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._cache: dict[int, _CacheEntry] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._usage_cache: dict[tuple[Any, ...], _UsageCacheEntry] = {}
        self._usage_lock = asyncio.Lock()

    async def get_execution_stats(self, days: int) -> ExecutionStats:
        now = time.monotonic()
        entry = self._cache.get(days)
        if entry is not None and (now - entry.cached_at) < _CACHE_TTL_SECONDS:
            _cache_hits.add(1)
            return entry.result

        lock = self._locks.setdefault(days, asyncio.Lock())
        async with lock:
            entry = self._cache.get(days)
            if entry is not None and (now - entry.cached_at) < _CACHE_TTL_SECONDS:
                _cache_hits.add(1)
                return entry.result
            result = await self._query(days)
            self._cache[days] = _CacheEntry(result=result, cached_at=time.monotonic())
            _cache_misses.add(1)
            logger.debug("monitoring_cache_miss", days=days)
            return result

    async def _query(self, days: int) -> ExecutionStats:
        cutoff = datetime.now(tz=UTC) - timedelta(days=days)

        async with self._ctx.admin_db.session() as session:
            daily_rows = await MonitoringRepository.daily_buckets(session, cutoff)
            top_rows = await MonitoringRepository.top_operations(session, cutoff)

        total = sum(b.total for b in daily_rows)
        success = sum(b.success for b in daily_rows)
        rate = (success / total * 100.0) if total > 0 else 0.0

        return ExecutionStats(
            total_executions=total,
            success_rate_percent=round(rate, 2),
            daily_buckets=[
                DailyBucket(date=b.date, total=b.total, success=b.success, failed=b.failed)
                for b in daily_rows
            ],
            top_operations=[
                TopOp(
                    api_vendor=op.api_vendor,
                    api_name=op.api_name,
                    operation_id=op.operation_id,
                    total=op.total,
                    failed=op.failed,
                )
                for op in top_rows
            ],
        )

    async def get_usage_stats(
        self,
        *,
        since: int | None = None,
        until: int | None = None,
        group_by: GroupBy = GroupBy.API,
        top_limit: int = 10,
        filters: UsageFilters | None = None,
    ) -> UsageStats:
        current_ts = int(datetime.now(tz=UTC).timestamp())
        # Round the fallback UP to the next minute boundary: requests arriving
        # within the same 60s window still share an identical cache key, but —
        # because every aggregate filters `started_at < until` strictly — the
        # current partial minute is INCLUDED. Flooring here made executions
        # invisible to the volume chart / KPI for up to a minute while the
        # executions feed (no upper bound) already listed them (#913).
        stable_now_ts = (current_ts // 60 + 1) * 60
        resolved_until = until if until is not None else stable_now_ts
        resolved_since = since if since is not None else resolved_until - 86400

        f = filters or UsageFilters()
        cache_key = (resolved_since, resolved_until, group_by, top_limit, f)

        now = time.monotonic()
        entry = self._usage_cache.get(cache_key)
        if entry is not None and (now - entry.cached_at) < _CACHE_TTL_SECONDS:
            _cache_hits.add(1)
            return entry.result

        async with self._usage_lock:
            entry = self._usage_cache.get(cache_key)
            if entry is not None and (now - entry.cached_at) < _CACHE_TTL_SECONDS:
                _cache_hits.add(1)
                return entry.result
            result = await self._query_usage(resolved_since, resolved_until, group_by, top_limit, f)
            self._evict_stale_usage_entries(now)
            self._usage_cache[cache_key] = _UsageCacheEntry(
                result=result, cached_at=time.monotonic()
            )
            _cache_misses.add(1)
            logger.debug(
                "usage_cache_miss", group_by=group_by, since=resolved_since, until=resolved_until
            )
            return result

    def _evict_stale_usage_entries(self, now: float) -> None:
        expired = [
            k for k, v in self._usage_cache.items() if (now - v.cached_at) >= _CACHE_TTL_SECONDS
        ]
        for k in expired:
            del self._usage_cache[k]
        if len(self._usage_cache) >= _USAGE_CACHE_MAX_ENTRIES:
            oldest_keys = sorted(self._usage_cache, key=lambda k: self._usage_cache[k].cached_at)
            for k in oldest_keys[: len(oldest_keys) - _USAGE_CACHE_MAX_ENTRIES + 1]:
                del self._usage_cache[k]

    @staticmethod
    def _compute_bucket_seconds(window_seconds: int) -> int:
        if window_seconds <= 3600:
            return 60
        if window_seconds <= 86400:
            return 3600
        # Up to 8 days still gets the 6h tier: a day-aligned "7d" window
        # crossing a fall-back DST change is 7d + 1h, which must not drop to
        # daily buckets (that would leave the week view with ~7 coarse
        # segments misaligned from its 7 day bars).
        if window_seconds <= 8 * 86400:
            return 21600
        return 86400

    @staticmethod
    def _compute_trend_points(window_seconds: int, bucket_seconds: int) -> int:
        """Number of per-entity trend segments for a window.

        One segment per aggregate bucket tier (24h → 24 hourly, 7d → 28
        six-hour, 30d → 30 daily) instead of a fixed 12: equal twelfths of a
        multi-day window (e.g. 14h for 7d) can never line up with calendar
        days, which made the volume chart's day axis span more days than the
        window. When the window is an exact multiple of the bucket size,
        day-aligned bounds put segments exactly on day boundaries; a
        DST-stretched window drifts slightly but stays re-bucketable into
        day bars client-side. Capped so a pathologically wide window can't
        inflate the payload.
        """
        return max(1, min(60, round(window_seconds / bucket_seconds)))

    @staticmethod
    def _build_repo_filters(filters: UsageFilters | None) -> UsageQueryFilters | None:
        if filters is None:
            return None
        api_vendor: str | None = None
        api_name: str | None = None
        if filters.api_id and "/" in filters.api_id:
            api_vendor = filters.api_id.split("/")[0]
            api_name = filters.api_id.split("/", 1)[1]
        f = UsageQueryFilters(
            toolkit_id=filters.toolkit_id,
            api_vendor=api_vendor,
            api_name=api_name,
            actor_id=filters.agent_id,
            status=filters.status,
        )
        if f == UsageQueryFilters():
            return None
        return f

    async def _query_usage(
        self,
        since: int,
        until: int,
        group_by: GroupBy,
        top_limit: int,
        filters: UsageFilters | None = None,
    ) -> UsageStats:
        cutoff = datetime.fromtimestamp(since, tz=UTC)
        until_dt = datetime.fromtimestamp(until, tz=UTC)
        window_seconds = until - since
        bucket_seconds = self._compute_bucket_seconds(window_seconds)

        repo_filters = self._build_repo_filters(filters)

        async with self._ctx.admin_db.session() as session:
            stats_row = await MonitoringRepository.overall_stats(
                session, cutoff, until_dt, filters=repo_filters
            )
            bucket_rows = await MonitoringRepository.time_buckets(
                session, cutoff, until_dt, bucket_seconds, filters=repo_filters
            )
            top_rows = await MonitoringRepository.grouped_top(
                session, cutoff, until_dt, group_by.value, top_limit, filters=repo_filters
            )
            top_keys = [r.key for r in top_rows]
            trends = await MonitoringRepository.grouped_trend(
                session,
                cutoff,
                until_dt,
                group_by.value,
                top_keys,
                num_points=self._compute_trend_points(window_seconds, bucket_seconds),
                filters=repo_filters,
            )

        return UsageStats(
            since=since,
            until=until,
            bucket_seconds=bucket_seconds,
            group_by=group_by.value,
            total=stats_row.total,
            success=stats_row.success,
            failed=stats_row.failed,
            pending=0,
            avg_ms=stats_row.avg_ms,
            p50_ms=stats_row.p50_ms,
            p95_ms=stats_row.p95_ms,
            active_now=0,
            buckets=[
                {
                    "ts": b.ts,
                    "total": b.total,
                    "success": b.success,
                    "failed": b.failed,
                    "avg_ms": b.avg_ms,
                }
                for b in bucket_rows
            ],
            top=[
                {
                    "key": r.key,
                    "label": r.label,
                    "total": r.total,
                    "success": r.success,
                    "failed": r.failed,
                    "avg_ms": r.avg_ms,
                    "trend": trends.get(r.key, []),
                }
                for r in top_rows
            ],
        )
