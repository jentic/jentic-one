"""Unit tests for the new monitoring repository usage query methods."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from jentic_one.admin.repos.monitoring_repo import (
    GroupedTopRow,
    MonitoringRepository,
    OverallStatsRow,
    TimeBucketRow,
)


def _mock_session_with_result(rows: list[MagicMock] | MagicMock) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    if isinstance(rows, list):
        result.all.return_value = rows
    else:
        result.one.return_value = rows
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_overall_stats_returns_correct_aggregates() -> None:
    row = MagicMock()
    row.total = 100
    row.success = 80
    row.failed = 20
    row.avg_ms = 250.5
    row.p50_ms = 200.0
    row.p95_ms = 500.0

    session = _mock_session_with_result(row)
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    until = datetime(2026, 6, 2, tzinfo=UTC)

    result = await MonitoringRepository.overall_stats(session, cutoff, until)
    assert isinstance(result, OverallStatsRow)
    assert result.total == 100
    assert result.success == 80
    assert result.failed == 20
    assert result.avg_ms == 250.5
    assert result.p50_ms == 200.0
    assert result.p95_ms == 500.0


@pytest.mark.asyncio
async def test_overall_stats_handles_null_percentiles() -> None:
    row = MagicMock()
    row.total = 0
    row.success = 0
    row.failed = 0
    row.avg_ms = 0
    row.p50_ms = None
    row.p95_ms = None

    session = _mock_session_with_result(row)
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    until = datetime(2026, 6, 2, tzinfo=UTC)

    result = await MonitoringRepository.overall_stats(session, cutoff, until)
    assert result.p50_ms is None
    assert result.p95_ms is None


@pytest.mark.asyncio
async def test_time_buckets_produces_correct_rows() -> None:
    bucket1 = MagicMock()
    bucket1.bucket_ts_text = "1719792000.0"
    bucket1.total = 50
    bucket1.success = 40
    bucket1.failed = 10
    bucket1.avg_ms = 100.0

    bucket2 = MagicMock()
    bucket2.bucket_ts_text = "1719795600.0"
    bucket2.total = 30
    bucket2.success = 28
    bucket2.failed = 2
    bucket2.avg_ms = 80.0

    session = _mock_session_with_result([bucket1, bucket2])
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    until = datetime(2026, 6, 2, tzinfo=UTC)

    result = await MonitoringRepository.time_buckets(session, cutoff, until, 3600)
    assert len(result) == 2
    assert isinstance(result[0], TimeBucketRow)
    assert result[0].ts == 1719792000
    assert result[0].total == 50
    assert result[1].ts == 1719795600
    assert result[1].avg_ms == 80.0


@pytest.mark.asyncio
async def test_grouped_top_returns_rows_for_api_grouping() -> None:
    row1 = MagicMock()
    row1.key = "stripe/payments"
    row1.label = "stripe/payments"
    row1.total = 50
    row1.success = 45
    row1.failed = 5
    row1.avg_ms = 200.0

    row2 = MagicMock()
    row2.key = "twilio/sms"
    row2.label = "twilio/sms"
    row2.total = 30
    row2.success = 29
    row2.failed = 1
    row2.avg_ms = 150.0

    session = _mock_session_with_result([row1, row2])
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    until = datetime(2026, 6, 2, tzinfo=UTC)

    result = await MonitoringRepository.grouped_top(session, cutoff, until, "api", 10)
    assert len(result) == 2
    assert isinstance(result[0], GroupedTopRow)
    assert result[0].key == "stripe/payments"
    assert result[0].total == 50
    assert result[1].key == "twilio/sms"


@pytest.mark.asyncio
async def test_grouped_top_returns_rows_for_toolkit_grouping() -> None:
    row = MagicMock()
    row.key = "tk_abc123"
    row.label = "tk_abc123"
    row.total = 20
    row.success = 18
    row.failed = 2
    row.avg_ms = 300.0

    session = _mock_session_with_result([row])
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    until = datetime(2026, 6, 2, tzinfo=UTC)

    result = await MonitoringRepository.grouped_top(session, cutoff, until, "toolkit", 5)
    assert len(result) == 1
    assert result[0].key == "tk_abc123"


@pytest.mark.asyncio
async def test_grouped_top_returns_rows_for_agent_grouping() -> None:
    row = MagicMock()
    row.key = "service_account/sa_001"
    row.label = "service_account/sa_001"
    row.total = 15
    row.success = 14
    row.failed = 1
    row.avg_ms = 120.0

    session = _mock_session_with_result([row])
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    until = datetime(2026, 6, 2, tzinfo=UTC)

    result = await MonitoringRepository.grouped_top(session, cutoff, until, "agent", 5)
    assert len(result) == 1
    assert result[0].key == "service_account/sa_001"


@pytest.mark.asyncio
async def test_grouped_trend_returns_correct_length_and_counts() -> None:
    row1 = MagicMock()
    row1.key = "stripe/payments"
    row1.seg = "0"
    row1.cnt = 5

    row2 = MagicMock()
    row2.key = "stripe/payments"
    row2.seg = "3"
    row2.cnt = 10

    row3 = MagicMock()
    row3.key = "stripe/payments"
    row3.seg = "11"
    row3.cnt = 2

    session = _mock_session_with_result([row1, row2, row3])
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    until = datetime(2026, 6, 2, tzinfo=UTC)

    result = await MonitoringRepository.grouped_trend(
        session, cutoff, until, "api", ["stripe/payments"], num_points=12
    )
    assert "stripe/payments" in result
    trend = result["stripe/payments"]
    assert len(trend) == 12
    assert trend[0] == 5
    assert trend[3] == 10
    assert trend[11] == 2
    assert trend[6] == 0


@pytest.mark.asyncio
async def test_grouped_trend_returns_empty_dict_for_no_keys() -> None:
    session = AsyncMock()
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    until = datetime(2026, 6, 2, tzinfo=UTC)

    result = await MonitoringRepository.grouped_trend(session, cutoff, until, "api", [])
    assert result == {}
