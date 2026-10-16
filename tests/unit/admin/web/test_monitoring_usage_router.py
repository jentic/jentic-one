"""Unit tests for the monitoring usage router response schemas and validation."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from jentic_one.admin.services.monitoring_service import (
    MonitoringService,
    UsageFilters,
    UsageStats,
)
from jentic_one.admin.web.deps import get_monitoring_service
from jentic_one.admin.web.routers.monitoring import router
from jentic_one.admin.web.schemas.monitoring import (
    GroupBy,
    UsageBucket,
    UsageResponse,
    UsageStatsBlock,
    UsageTopRow,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web.deps import resolve_identity
from jentic_one.shared.web.errors import request_validation_error_handler


async def _fake_identity() -> Identity:
    return Identity(sub="usr_test", permissions=["org:admin"])


def _empty_stats() -> UsageStats:
    return UsageStats(
        since=0,
        until=86400,
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


@pytest.fixture()
def usage_service() -> AsyncMock:
    return AsyncMock(spec=MonitoringService)


@pytest.fixture()
def usage_client(usage_service: AsyncMock) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)  # type: ignore[arg-type]
    app.dependency_overrides[resolve_identity] = _fake_identity
    app.dependency_overrides[get_monitoring_service] = lambda: usage_service
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("group_by", ["toolkit", "operation"])
def test_usage_rejects_unknown_group_by_with_422(
    usage_client: TestClient, usage_service: AsyncMock, group_by: str
) -> None:
    """A ``group_by`` outside the enum — including the removed ``toolkit`` axis — is a 422."""
    resp = usage_client.get("/monitoring/usage", params={"group_by": group_by})
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["query", "group_by"]
    usage_service.get_usage_stats.assert_not_called()


def test_usage_ignores_toolkit_id_param(usage_client: TestClient, usage_service: AsyncMock) -> None:
    """Pins #1745: ``toolkit_id`` is not a filter on ``GET /monitoring/usage``.

    The request gets a 200, no filter reaches the service, and one
    ``removed_query_param_ignored`` INFO names the param and the caller but
    never the value.
    """
    usage_service.get_usage_stats.return_value = _empty_stats()
    with structlog.testing.capture_logs() as logs:
        resp = usage_client.get("/monitoring/usage", params={"toolkit_id": "tk_abc"})
    assert resp.status_code == 200
    assert usage_service.get_usage_stats.call_args.kwargs["filters"] == UsageFilters()
    ignored = [log for log in logs if log["event"] == "removed_query_param_ignored"]
    assert ignored == [
        {
            "event": "removed_query_param_ignored",
            "log_level": "info",
            "param": "toolkit_id",
            "route": "/monitoring/usage",
            "actor_id": "usr_test",
        }
    ]


def test_usage_without_ignored_params_logs_nothing(
    usage_client: TestClient, usage_service: AsyncMock
) -> None:
    """A request that only sends supported filters emits no ``removed_query_param_ignored``."""
    usage_service.get_usage_stats.return_value = _empty_stats()
    with structlog.testing.capture_logs() as logs:
        resp = usage_client.get("/monitoring/usage", params={"api_id": "stripe/payments"})
    assert resp.status_code == 200
    assert not [log for log in logs if log["event"] == "removed_query_param_ignored"]


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
        group_by="credential",
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
    assert {e.value for e in GroupBy} == {"api", "credential", "agent"}


def test_group_by_enum_rejects_invalid() -> None:
    valid_values = {e.value for e in GroupBy}
    assert "invalid" not in valid_values
    assert "operation" not in valid_values
    # Toolkits were removed (#1498); the axis is not a grouping dimension.
    assert "toolkit" not in valid_values


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
