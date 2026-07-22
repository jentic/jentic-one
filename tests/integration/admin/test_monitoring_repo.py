"""Integration tests for MonitoringRepository aggregations on both backends.

These run under ``make test-integration`` (PostgreSQL) and
``make test-integration-sqlite`` (SQLite). The ``daily_buckets`` query used to
hard-code the Postgres-only ``date_trunc`` function, which 500'd the
``GET /monitoring/executions`` endpoint on SQLite (issue #623); these tests
guard the portable, dialect-aware day-bucket expression.

The usage aggregations (``overall_stats``, ``time_buckets``, ``grouped_top``,
``grouped_trend``) likewise once hard-coded Postgres-only SQL —
``percentile_cont … WITHIN GROUP``, ``extract('epoch', …)`` and ``concat()`` —
which 500'd ``GET /monitoring/usage`` and the enterprise usage overview on
SQLite. The tests below guard their dialect-aware forms (percentiles are simply
``None`` on SQLite, which has no ordered-set aggregate).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.repos import ExecutionRecordRepository
from jentic_one.admin.repos.monitoring_repo import MonitoringRepository
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_execution_records(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.session() as session:
        await session.execute(delete(ExecutionRecord))
        await session.commit()
    yield
    async with admin_db.session() as session:
        await session.execute(delete(ExecutionRecord))
        await session.commit()


async def _seed(
    admin_db: DatabaseSession, started_at: datetime, status: str, **kwargs: object
) -> None:
    async with admin_db.session() as session:
        await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_test000000000000000000",
            trace_id="abcdef1234567890abcdef12",
            started_at=started_at,
            status=status,
            created_by="usr_test",
            actor_id="usr_test",
            actor_type="user",
            **kwargs,  # type: ignore[arg-type]
        )
        await session.commit()


async def test_daily_buckets_groups_by_day(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    now = datetime.now(UTC)
    # Two executions today, one yesterday — must collapse into two day buckets.
    await _seed(admin_db, now, "completed")
    await _seed(admin_db, now - timedelta(minutes=30), "failed")
    await _seed(admin_db, now - timedelta(days=1), "completed")

    cutoff = now - timedelta(days=7)
    async with admin_db.session() as session:
        buckets = await MonitoringRepository.daily_buckets(session, cutoff)

    assert len(buckets) == 2
    # Each bucket date is a YYYY-MM-DD string on both backends.
    for bucket in buckets:
        assert len(bucket.date) == 10
        assert bucket.date[4] == "-" and bucket.date[7] == "-"
    by_date = {b.date: b for b in buckets}
    today = by_date[now.strftime("%Y-%m-%d")]
    assert today.total == 2
    assert today.success == 1
    assert today.failed == 1


async def test_daily_buckets_respects_cutoff(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    now = datetime.now(UTC)
    await _seed(admin_db, now, "completed")
    await _seed(admin_db, now - timedelta(days=10), "completed")  # before cutoff

    async with admin_db.session() as session:
        buckets = await MonitoringRepository.daily_buckets(session, now - timedelta(days=7))

    assert len(buckets) == 1
    assert buckets[0].total == 1


async def test_daily_buckets_empty(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    async with admin_db.session() as session:
        buckets = await MonitoringRepository.daily_buckets(
            session, datetime.now(UTC) - timedelta(days=7)
        )
    assert buckets == []


async def test_top_operations_ranks_by_total(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    now = datetime.now(UTC)
    for _ in range(3):
        await _seed(
            admin_db,
            now,
            "completed",
            operation_id="listUsers",
            api_vendor="github",
            api_name="rest",
        )
    await _seed(
        admin_db,
        now,
        "failed",
        operation_id="getRepo",
        api_vendor="github",
        api_name="rest",
    )

    async with admin_db.session() as session:
        ops = await MonitoringRepository.top_operations(session, now - timedelta(days=7))

    assert ops[0].operation_id == "listUsers"
    assert ops[0].total == 3
    failed_op = next(o for o in ops if o.operation_id == "getRepo")
    assert failed_op.failed == 1


async def test_overall_stats_counts_and_percentiles(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    now = datetime.now(UTC)
    await _seed(admin_db, now, "completed", duration_ms=100)
    await _seed(admin_db, now - timedelta(minutes=1), "completed", duration_ms=200)
    await _seed(admin_db, now - timedelta(minutes=2), "failed", duration_ms=300)

    async with admin_db.session() as session:
        is_postgres = session.bind is not None and session.bind.dialect.name == "postgresql"
        stats = await MonitoringRepository.overall_stats(
            session, now - timedelta(days=7), now + timedelta(minutes=1)
        )

    # Counts + avg are portable across both backends.
    assert stats.total == 3
    assert stats.success == 2
    assert stats.failed == 1
    assert stats.avg_ms == pytest.approx(200.0)
    # Percentiles are Postgres-only (SQLite has no ordered-set aggregate) — the
    # SQLite path returns None rather than 500'ing the whole statement.
    if is_postgres:
        assert stats.p50_ms is not None and stats.p95_ms is not None
    else:
        assert stats.p50_ms is None and stats.p95_ms is None


async def test_time_buckets_aggregates_by_window(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    now = datetime.now(UTC)
    await _seed(admin_db, now, "completed", duration_ms=100)
    await _seed(admin_db, now - timedelta(seconds=10), "failed", duration_ms=100)

    async with admin_db.session() as session:
        buckets = await MonitoringRepository.time_buckets(
            session, now - timedelta(hours=1), now + timedelta(minutes=1), 3600
        )

    # Both executions fall in the same hour bucket (exercises the dialect-aware
    # epoch-seconds expression that replaced Postgres-only extract('epoch', …)).
    assert sum(b.total for b in buckets) == 2
    assert sum(b.success for b in buckets) == 1
    assert sum(b.failed for b in buckets) == 1


async def test_grouped_top_and_trend_by_toolkit(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    now = datetime.now(UTC)
    for _ in range(3):
        await _seed(admin_db, now, "completed")

    async with admin_db.session() as session:
        top = await MonitoringRepository.grouped_top(
            session, now - timedelta(days=7), now + timedelta(minutes=1), "toolkit", 10
        )
        keys = [r.key for r in top]
        trends = await MonitoringRepository.grouped_trend(
            session, now - timedelta(days=7), now + timedelta(minutes=1), "toolkit", keys
        )

    assert top[0].total == 3
    assert top[0].key == "tk_test000000000000000000"
    # grouped_trend distributes the 3 executions across its segments (exercises
    # the dialect-aware epoch expression + `||` string concat on SQLite).
    assert sum(trends["tk_test000000000000000000"]) == 3


async def test_grouped_top_by_agent_concat_key(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    now = datetime.now(UTC)
    await _seed(admin_db, now, "completed")

    async with admin_db.session() as session:
        top = await MonitoringRepository.grouped_top(
            session, now - timedelta(days=7), now + timedelta(minutes=1), "agent", 10
        )

    # The agent key is `actor_type/actor_id` — built with `||` concat so it works
    # on SQLite (Postgres-only `concat()` used to break this on SQLite).
    assert top[0].key == "user/usr_test"
