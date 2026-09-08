"""Integration tests for MonitoringService's usage window resolution (#913).

The volume chart / KPI aggregate must never trail the executions feed: an
execution recorded "now" has to be counted by a usage query that omits
``until``. The service used to floor the fallback bound DOWN to the minute
(for cache-key stability), and every aggregate filters ``started_at < until``
strictly — so current-minute executions were invisible to the aggregate for up
to 60 s while ``GET /executions`` (no upper bound) already listed them.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.repos import ExecutionRecordRepository
from jentic_one.admin.services.monitoring_service import MonitoringService
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_executions(integration_context: Context) -> AsyncGenerator[None, None]:
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(ExecutionRecord))
        await session.commit()
    yield
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(ExecutionRecord))
        await session.commit()


async def _seed(ctx: Context, started_at: datetime) -> None:
    async with ctx.admin_db.session() as session:
        await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_test000000000000000000",
            trace_id="ab" * 16,
            started_at=started_at,
            status="completed",
            created_by="usr_test",
            actor_id="agt_test",
            actor_type="agent",
        )
        await session.commit()


async def test_usage_without_until_counts_the_current_partial_minute(
    integration_context: Context, clean_executions: None
) -> None:
    """The #913 contract: volume must not trail the feed.

    A record started *now* — inside the current partial minute — must be
    included when the caller sends only ``since`` (the agent/toolkit detail
    pages' shape). The fallback ``until`` is ceiled to the NEXT minute
    boundary, so the strict ``started_at < until`` covers it immediately; no
    hard refresh or minute-boundary wait.
    """
    ctx = integration_context
    now = datetime.now(UTC)
    await _seed(ctx, now)

    svc = MonitoringService(ctx)
    result = await svc.get_usage_stats(since=int(now.timestamp()) - 7 * 86_400)

    assert result.total == 1
    assert result.success == 1
    # The resolved bound is minute-aligned (cache keys stay stable for the
    # whole minute) and strictly in the future relative to the seeded row.
    assert result.until % 60 == 0
    assert result.until > int(now.timestamp())


async def test_usage_with_explicit_until_still_bounds_strictly(
    integration_context: Context, clean_executions: None
) -> None:
    """A caller-supplied ``until`` stays authoritative (Monitor's explicit
    windows): a record at/after the bound is excluded, one before it counts."""
    ctx = integration_context
    now = datetime.now(UTC).replace(microsecond=0)
    await _seed(ctx, now - timedelta(minutes=5))
    await _seed(ctx, now + timedelta(minutes=5))  # outside the explicit window

    svc = MonitoringService(ctx)
    result = await svc.get_usage_stats(
        since=int(now.timestamp()) - 3_600, until=int(now.timestamp())
    )

    assert result.total == 1
