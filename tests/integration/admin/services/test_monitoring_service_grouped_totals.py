"""Integration tests for MonitoringService.get_grouped_totals (#836) on both backends.

Grouped totals are the top-N half of ``get_usage_stats`` without the buckets,
overall stats and per-key trend: for the same window, ``group_by``, limit and
filters they must return exactly the keys, labels and counts that
``get_usage_stats().top`` reports.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.repos import ExecutionRecordRepository
from jentic_one.admin.services.monitoring_service import (
    MonitoringService,
    UsageFilters,
    UsageTopKey,
)
from jentic_one.admin.web.schemas.monitoring import GroupBy
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration

# (actor_id, api_vendor/api_name, credential_id, completed, failed). Totals are
# distinct per key on every dimension so the volume ordering is deterministic.
_SEED: list[tuple[str, str, str | None, int, int]] = [
    ("agt_a", "stripe/payments", "cred_1", 4, 1),
    ("agt_a", "github/repos", "cred_2", 2, 0),
    ("agt_b", "stripe/payments", None, 1, 2),
    ("agt_c", "slack/chat", "cred_1", 0, 1),
]


@pytest.fixture()
async def clean_executions(integration_context: Context) -> AsyncGenerator[None, None]:
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(ExecutionRecord))
        await session.commit()
    yield
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(ExecutionRecord))
        await session.commit()


@pytest.fixture()
async def window(integration_context: Context, clean_executions: None) -> tuple[int, int]:
    """Seed ``_SEED`` inside an explicit window, plus one record just outside it."""
    now = datetime.now(UTC).replace(microsecond=0)
    since = now - timedelta(hours=6)
    async with integration_context.admin_db.session() as session:
        for actor_id, api, credential_id, completed, failed in _SEED:
            vendor, name = api.split("/")
            for i, status in enumerate(["completed"] * completed + ["failed"] * failed):
                await ExecutionRecordRepository.create(
                    session,
                    toolkit_id="tk_test000000000000000000",
                    trace_id="ab" * 16,
                    started_at=since + timedelta(minutes=10 + i),
                    status=status,
                    duration_ms=100 * (i + 1),
                    api_vendor=vendor,
                    api_name=name,
                    created_by="usr_test",
                    actor_id=actor_id,
                    actor_type="agent",
                    credential_id=credential_id,
                )
        # Before the window: must not be counted by either method.
        await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_test000000000000000000",
            trace_id="cd" * 16,
            started_at=since - timedelta(minutes=1),
            status="completed",
            api_vendor="stripe",
            api_name="payments",
            created_by="usr_test",
            actor_id="agt_a",
            actor_type="agent",
        )
        await session.commit()
    return int(since.timestamp()), int(now.timestamp())


def _as_top_rows(totals: list[UsageTopKey]) -> list[dict[str, object]]:
    return [
        {
            "key": t.key,
            "label": t.label,
            "total": t.total,
            "success": t.success,
            "failed": t.failed,
            "avg_ms": t.avg_ms,
        }
        for t in totals
    ]


@pytest.mark.parametrize("group_by", list(GroupBy))
@pytest.mark.parametrize(
    "filters",
    [
        None,
        UsageFilters(api_id="stripe/payments"),
        UsageFilters(agent_id="agt_a"),
        UsageFilters(status="failed"),
    ],
)
@pytest.mark.parametrize("limit", [1, 2, 50])
async def test_grouped_totals_match_usage_stats_top(
    integration_context: Context,
    window: tuple[int, int],
    group_by: GroupBy,
    filters: UsageFilters | None,
    limit: int,
) -> None:
    since, until = window
    svc = MonitoringService(integration_context)

    totals = await svc.get_grouped_totals(
        since=since, until=until, group_by=group_by, limit=limit, filters=filters
    )
    usage = await svc.get_usage_stats(
        since=since, until=until, group_by=group_by, top_limit=limit, filters=filters
    )

    expected = [{k: v for k, v in row.items() if k != "trend"} for row in usage.top]
    assert _as_top_rows(totals) == expected
    assert len(totals) <= limit


async def test_grouped_totals_by_agent_counts_and_order(
    integration_context: Context, window: tuple[int, int]
) -> None:
    since, until = window
    totals = await MonitoringService(integration_context).get_grouped_totals(
        since=since, until=until, group_by=GroupBy.AGENT, limit=100
    )

    # `{actor_type}/{actor_id}` keys, busiest first; the out-of-window record is excluded.
    assert [(t.key, t.total, t.success, t.failed) for t in totals] == [
        ("agent/agt_a", 7, 6, 1),
        ("agent/agt_b", 3, 1, 2),
        ("agent/agt_c", 1, 0, 1),
    ]


async def test_grouped_totals_honour_filters_and_limit(
    integration_context: Context, window: tuple[int, int]
) -> None:
    since, until = window
    svc = MonitoringService(integration_context)

    by_api_for_agent_a = await svc.get_grouped_totals(
        since=since,
        until=until,
        group_by=GroupBy.API,
        limit=100,
        filters=UsageFilters(agent_id="agt_a"),
    )
    assert [(t.key, t.total) for t in by_api_for_agent_a] == [
        ("stripe/payments", 5),
        ("github/repos", 2),
    ]

    failed_by_agent = await svc.get_grouped_totals(
        since=since,
        until=until,
        group_by=GroupBy.AGENT,
        limit=100,
        filters=UsageFilters(status="failed"),
    )
    # agt_a and agt_c tie on one failure each, so only the leader's position is fixed.
    assert failed_by_agent[0].key == "agent/agt_b"
    assert sorted((t.key, t.total, t.failed, t.success) for t in failed_by_agent) == [
        ("agent/agt_a", 1, 1, 0),
        ("agent/agt_b", 2, 2, 0),
        ("agent/agt_c", 1, 1, 0),
    ]

    top_one = await svc.get_grouped_totals(
        since=since, until=until, group_by=GroupBy.CREDENTIAL, limit=1
    )
    # cred_1: agt_a/stripe (5) + agt_c/slack (1).
    assert [(t.key, t.total) for t in top_one] == [("cred_1", 6)]
