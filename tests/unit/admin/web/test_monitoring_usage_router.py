"""Unit tests for the monitoring usage router response schemas and validation."""

from __future__ import annotations

from jentic_one.admin.web.schemas.monitoring import (
    GroupBy,
    UsageBucket,
    UsageResponse,
    UsageStatsBlock,
    UsageTopRow,
)


def test_usage_response_serializes_correctly() -> None:
    resp = UsageResponse(
        since=1719792000,
        until=1719878400,
        bucket_seconds=3600,
        group_by="api",
        stats=UsageStatsBlock(
            total=100,
            success=80,
            failed=20,
            pending=0,
            avg_ms=250.0,
            p50_ms=200.0,
            p95_ms=500.0,
            active_now=0,
        ),
        buckets=[
            UsageBucket(ts=1719792000, total=50, success=40, failed=10, avg_ms=100.0),
            UsageBucket(ts=1719795600, total=30, success=28, failed=2, avg_ms=80.0),
        ],
        top=[
            UsageTopRow(
                key="stripe/payments",
                label="stripe/payments",
                total=50,
                success=45,
                failed=5,
                avg_ms=200.0,
                trend=[5, 3, 4, 6, 2, 3, 5, 4, 7, 3, 2, 6],
            ),
        ],
    )
    data = resp.model_dump()
    assert data["since"] == 1719792000
    assert data["until"] == 1719878400
    assert data["bucket_seconds"] == 3600
    assert data["group_by"] == "api"
    assert data["stats"]["total"] == 100
    assert data["stats"]["pending"] == 0
    assert data["stats"]["active_now"] == 0
    assert data["stats"]["p50_ms"] == 200.0
    assert len(data["buckets"]) == 2
    assert data["buckets"][0]["ts"] == 1719792000
    assert len(data["top"]) == 1
    assert data["top"][0]["key"] == "stripe/payments"
    assert len(data["top"][0]["trend"]) == 12


def test_usage_response_with_null_percentiles() -> None:
    resp = UsageResponse(
        since=1719792000,
        until=1719878400,
        bucket_seconds=3600,
        group_by="toolkit",
        stats=UsageStatsBlock(
            total=0,
            success=0,
            failed=0,
            pending=0,
            avg_ms=0.0,
            p50_ms=None,
            p95_ms=None,
            active_now=0,
        ),
        buckets=[],
        top=[],
    )
    data = resp.model_dump()
    assert data["stats"]["p50_ms"] is None
    assert data["stats"]["p95_ms"] is None
    assert data["buckets"] == []
    assert data["top"] == []


def test_group_by_enum_values() -> None:
    assert GroupBy.API.value == "api"
    assert GroupBy.TOOLKIT.value == "toolkit"
    assert GroupBy.CREDENTIAL.value == "credential"
    assert GroupBy.AGENT.value == "agent"


def test_group_by_enum_rejects_invalid() -> None:
    valid_values = {e.value for e in GroupBy}
    assert "invalid" not in valid_values
    assert "operation" not in valid_values


def test_usage_top_row_with_empty_trend() -> None:
    row = UsageTopRow(
        key="test/api",
        label="test/api",
        total=10,
        success=9,
        failed=1,
        avg_ms=100.0,
        trend=[],
    )
    assert row.model_dump()["trend"] == []


def test_usage_stats_block_fields() -> None:
    block = UsageStatsBlock(
        total=500,
        success=450,
        failed=50,
        pending=0,
        avg_ms=120.5,
        p50_ms=100.0,
        p95_ms=400.0,
        active_now=0,
    )
    data = block.model_dump()
    assert data["total"] == 500
    assert data["success"] == 450
    assert data["failed"] == 50
    assert data["pending"] == 0
    assert data["avg_ms"] == 120.5
    assert data["active_now"] == 0
