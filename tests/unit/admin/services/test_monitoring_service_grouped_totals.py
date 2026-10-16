"""Unit tests for MonitoringService.get_grouped_totals (#836)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.admin.repos.monitoring_repo import GroupedTopRow, UsageQueryFilters
from jentic_one.admin.services.monitoring_service import (
    _CACHE_TTL_SECONDS,
    _GROUPED_TOTALS_KEY_TAG,
    _USAGE_CACHE_MAX_ENTRIES,
    MonitoringService,
    UsageFilters,
    UsageTopKey,
    _GroupedTotalsCacheEntry,
)
from jentic_one.admin.web.schemas.monitoring import GroupBy

_REPO = "jentic_one.admin.services.monitoring_service.MonitoringRepository"


def _top_rows() -> list[GroupedTopRow]:
    return [
        GroupedTopRow(
            key="agent/agt_a", label="agent/agt_a", total=7, success=5, failed=2, avg_ms=120.0
        ),
        GroupedTopRow(
            key="agent/agt_b", label="agent/agt_b", total=3, success=3, failed=0, avg_ms=80.0
        ),
    ]


@pytest.fixture()
def repo() -> Iterator[dict[str, AsyncMock]]:
    """Patch every MonitoringRepository usage query so call counts can be asserted."""
    with (
        patch(f"{_REPO}.overall_stats", new_callable=AsyncMock) as overall_stats,
        patch(f"{_REPO}.time_buckets", new_callable=AsyncMock, return_value=[]) as time_buckets,
        patch(
            f"{_REPO}.grouped_top", new_callable=AsyncMock, return_value=_top_rows()
        ) as grouped_top,
        patch(f"{_REPO}.grouped_trend", new_callable=AsyncMock, return_value={}) as grouped_trend,
    ):
        yield {
            "overall_stats": overall_stats,
            "time_buckets": time_buckets,
            "grouped_top": grouped_top,
            "grouped_trend": grouped_trend,
        }


def _svc() -> MonitoringService:
    ctx = MagicMock()
    ctx.admin_db = MagicMock()
    return MonitoringService(ctx)


@pytest.mark.asyncio
async def test_grouped_totals_issues_only_the_grouped_query(repo: dict[str, AsyncMock]) -> None:
    """Only `grouped_top` runs: no overall stats, time buckets or per-key trend IN-list."""
    svc = _svc()
    filters = UsageFilters(api_id="stripe/payments", agent_id="agt_a", status="failed")

    result = await svc.get_grouped_totals(
        since=1719792000, until=1719878400, group_by=GroupBy.AGENT, limit=500, filters=filters
    )

    assert result == [
        UsageTopKey(
            key="agent/agt_a", label="agent/agt_a", total=7, success=5, failed=2, avg_ms=120.0
        ),
        UsageTopKey(
            key="agent/agt_b", label="agent/agt_b", total=3, success=3, failed=0, avg_ms=80.0
        ),
    ]
    repo["grouped_top"].assert_awaited_once()
    repo["overall_stats"].assert_not_called()
    repo["time_buckets"].assert_not_called()
    repo["grouped_trend"].assert_not_called()

    args, kwargs = repo["grouped_top"].call_args
    _session, cutoff, until_dt, group_by, limit = args
    assert cutoff == datetime.fromtimestamp(1719792000, tz=UTC)
    assert until_dt == datetime.fromtimestamp(1719878400, tz=UTC)
    assert group_by == "agent"
    assert limit == 500
    # Same filter translation as get_usage_stats (api_id split into vendor/name).
    assert kwargs["filters"] == UsageQueryFilters(
        api_vendor="stripe", api_name="payments", actor_id="agt_a", status="failed"
    )


@pytest.mark.asyncio
async def test_grouped_totals_empty_filters_pass_none(repo: dict[str, AsyncMock]) -> None:
    await _svc().get_grouped_totals(
        since=0, until=86400, group_by=GroupBy.API, limit=10, filters=UsageFilters()
    )
    assert repo["grouped_top"].call_args[1]["filters"] is None


@pytest.mark.asyncio
async def test_grouped_totals_cache_is_separate_from_usage_cache(
    repo: dict[str, AsyncMock],
) -> None:
    """The two methods never serve each other's entries for the same parameters."""
    svc = _svc()
    stats_row = MagicMock(total=10, success=8, failed=2, avg_ms=100.0, p50_ms=None, p95_ms=None)
    repo["overall_stats"].return_value = stats_row

    await svc.get_usage_stats(since=0, until=86400, group_by=GroupBy.AGENT, top_limit=10)
    assert repo["grouped_top"].await_count == 1

    # Same window/group_by/limit/filters: must miss, not reuse the usage entry.
    totals = await svc.get_grouped_totals(since=0, until=86400, group_by=GroupBy.AGENT, limit=10)
    assert repo["grouped_top"].await_count == 2
    assert all(isinstance(t, UsageTopKey) for t in totals)

    # Each now hits its own cache.
    await svc.get_usage_stats(since=0, until=86400, group_by=GroupBy.AGENT, top_limit=10)
    await svc.get_grouped_totals(since=0, until=86400, group_by=GroupBy.AGENT, limit=10)
    assert repo["grouped_top"].await_count == 2

    usage_key = (0, 86400, GroupBy.AGENT, 10, UsageFilters())
    totals_key = (_GROUPED_TOTALS_KEY_TAG, 0, 86400, GroupBy.AGENT, 10, UsageFilters())
    assert set(svc._usage_cache) == {usage_key}
    assert set(svc._grouped_totals_cache) == {totals_key}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changed", "expected_queries"),
    [
        ({}, 1),
        ({"since": 1}, 2),
        ({"until": 86401}, 2),
        ({"group_by": GroupBy.API}, 2),
        ({"limit": 11}, 2),
        ({"filters": UsageFilters(agent_id="agt_a")}, 2),
    ],
)
async def test_grouped_totals_cache_key_covers_every_parameter(
    repo: dict[str, AsyncMock], changed: dict[str, object], expected_queries: int
) -> None:
    svc = _svc()
    base: dict[str, object] = {
        "since": 0,
        "until": 86400,
        "group_by": GroupBy.AGENT,
        "limit": 10,
        "filters": None,
    }
    await svc.get_grouped_totals(**base)  # type: ignore[arg-type]
    await svc.get_grouped_totals(**{**base, **changed})  # type: ignore[arg-type]
    assert repo["grouped_top"].await_count == expected_queries


@pytest.mark.asyncio
async def test_grouped_totals_cache_hit_returns_a_fresh_list(repo: dict[str, AsyncMock]) -> None:
    """Mutating a returned list can't corrupt the cached entry."""
    svc = _svc()
    first = await svc.get_grouped_totals(since=0, until=86400, group_by=GroupBy.AGENT, limit=10)
    first.clear()
    second = await svc.get_grouped_totals(since=0, until=86400, group_by=GroupBy.AGENT, limit=10)
    assert repo["grouped_top"].await_count == 1
    assert len(second) == 2


@pytest.mark.asyncio
async def test_grouped_totals_entry_expires_after_ttl(repo: dict[str, AsyncMock]) -> None:
    svc = _svc()
    key = (_GROUPED_TOTALS_KEY_TAG, 0, 86400, GroupBy.AGENT, 10, UsageFilters())
    svc._grouped_totals_cache[key] = _GroupedTotalsCacheEntry(
        result=(), cached_at=time.monotonic() - (_CACHE_TTL_SECONDS + 1)
    )
    result = await svc.get_grouped_totals(since=0, until=86400, group_by=GroupBy.AGENT, limit=10)
    assert repo["grouped_top"].await_count == 1
    assert len(result) == 2


@pytest.mark.asyncio
async def test_grouped_totals_default_window_matches_usage_stats(
    repo: dict[str, AsyncMock],
) -> None:
    """With no bounds, both methods resolve the same minute-ceiled 24h window."""
    svc = _svc()
    mock_now = datetime(2024, 7, 2, 0, 0, 15, tzinfo=UTC)
    with patch("jentic_one.admin.services.monitoring_service.datetime") as mock_datetime:
        mock_datetime.now.return_value = mock_now
        mock_datetime.fromtimestamp = datetime.fromtimestamp
        await svc.get_grouped_totals(since=None, until=None, group_by=GroupBy.API, limit=10)

    expected_until = (int(mock_now.timestamp()) // 60 + 1) * 60
    (key,) = svc._grouped_totals_cache
    assert key[1:3] == (expected_until - 86400, expected_until)


@pytest.mark.asyncio
async def test_grouped_totals_rejects_non_positive_limit(repo: dict[str, AsyncMock]) -> None:
    with pytest.raises(ValueError, match="limit"):
        await _svc().get_grouped_totals(since=0, until=86400, group_by=GroupBy.API, limit=0)
    repo["grouped_top"].assert_not_called()


@pytest.mark.asyncio
async def test_grouped_totals_cache_is_bounded(repo: dict[str, AsyncMock]) -> None:
    svc = _svc()
    now = time.monotonic()
    for i in range(_USAGE_CACHE_MAX_ENTRIES + 10):
        key = (_GROUPED_TOTALS_KEY_TAG, i, i + 100, GroupBy.API, 10, UsageFilters())
        svc._grouped_totals_cache[key] = _GroupedTotalsCacheEntry(
            result=(), cached_at=now - i * 0.001
        )

    await svc.get_grouped_totals(since=0, until=86400, group_by=GroupBy.API, limit=10)
    assert len(svc._grouped_totals_cache) <= _USAGE_CACHE_MAX_ENTRIES
