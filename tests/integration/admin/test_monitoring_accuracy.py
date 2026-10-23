"""Integration tests for the monitoring aggregates' accuracy.

Two properties the dashboard's numbers depend on, and which no other test in
this directory pins:

* **The window is bounded at both ends.** ``/monitoring/executions`` sums its
  total and success rate over the day buckets, so a row stamped in the future
  inflates both for as long as it sits in the table.
* **One response describes one instant.** A route answering from several
  statements reads a fresh snapshot per statement at ``READ COMMITTED``, so a
  commit landing between two of them leaves a total disagreeing with the sum of
  its own buckets. The body is cached and re-served for the whole TTL, so one
  interleaved write stays visible well after the race.

The snapshot tests are Postgres-only: the guarantee comes from
``REPEATABLE READ``, and SQLite reaches it by a different route (a deferred
read transaction under WAL), whose interaction with a concurrent writer is not
what these tests are pinning.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.repos import ExecutionRecordRepository
from jentic_one.admin.repos.monitoring_repo import MonitoringRepository
from jentic_one.admin.services.monitoring_service import MonitoringService
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_COUNT = select(func.count()).select_from(ExecutionRecord)


@pytest.fixture()
async def clean_execution_records(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.session() as session:
        await session.execute(delete(ExecutionRecord))
        await session.commit()
    yield
    async with admin_db.session() as session:
        await session.execute(delete(ExecutionRecord))
        await session.commit()


@pytest.fixture()
async def postgres_only(admin_db: DatabaseSession) -> None:
    async with admin_db.session() as session:
        dialect = session.bind.dialect.name if session.bind else ""
    if dialect != "postgresql":
        pytest.skip(f"snapshot isolation is pinned on Postgres; backend is {dialect!r}")


async def _seed(
    admin_db: DatabaseSession,
    started_at: datetime,
    status: str = "completed",
    **kwargs: object,
) -> None:
    async with admin_db.session() as session:
        await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_test000000000000000000",
            trace_id="ab" * 12,
            started_at=started_at,
            status=status,
            created_by="usr_test",
            actor_id="usr_test",
            actor_type="user",
            **kwargs,  # type: ignore[arg-type]
        )
        await session.commit()


# ── The window is bounded at both ends ───────────────────────────────────────


async def test_daily_buckets_exclude_a_row_stamped_in_the_future(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    """A row past ``until`` is outside the window, however far ahead it is."""
    now = datetime.now(UTC)
    await _seed(admin_db, now - timedelta(hours=1))
    await _seed(admin_db, now + timedelta(days=2), "failed")

    until = now + timedelta(minutes=1)
    async with admin_db.session() as session:
        buckets = await MonitoringRepository.daily_buckets(session, now - timedelta(days=7), until)
        ops = await MonitoringRepository.top_operations(session, now - timedelta(days=7), until)
        stored = (await session.execute(_COUNT)).scalar_one()

    # The future row is really there: the aggregates exclude it rather than the
    # fixture failing to write it, which is what makes the counts below mean
    # something.
    assert stored == 2
    assert sum(b.total for b in buckets) == 1
    assert sum(b.failed for b in buckets) == 0
    assert sum(o.total for o in ops) == 0  # the seeds carry no operation_id


async def test_execution_stats_exclude_a_row_stamped_in_the_future(
    integration_context: Context, admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    """The route's total and success rate are sums over the buckets, so the bound reaches them.

    An unbounded window counted a future ``failed`` row into ``total`` while
    the rate's numerator ignored it, so the rate fell below 100% with no failure
    inside the window to explain it.
    """
    now = datetime.now(UTC)
    await _seed(admin_db, now - timedelta(hours=1))
    await _seed(admin_db, now + timedelta(days=2), "failed")

    stats = await MonitoringService(integration_context).get_execution_stats(days=7)

    assert stats.total_executions == 1
    assert stats.success_rate_percent == 100.0
    assert sum(b.failed for b in stats.daily_buckets) == 0


# ── One response describes one instant ───────────────────────────────────────


async def test_a_read_session_sees_an_interleaved_commit(
    admin_db: DatabaseSession, clean_execution_records: None, postgres_only: None
) -> None:
    """The control for the snapshot test below.

    Without it, a passing snapshot test could mean only that the interleaved
    write never landed. Here the same interleaving is run on an autocommit
    read session, which takes a snapshot per statement and so must observe it.
    """
    now = datetime.now(UTC)
    await _seed(admin_db, now - timedelta(minutes=5))

    async with admin_db.read_session() as session:
        before = (await session.execute(_COUNT)).scalar_one()
        await _seed(admin_db, now - timedelta(minutes=4))
        after = (await session.execute(_COUNT)).scalar_one()

    assert before == 1
    assert after == 2


async def test_a_read_snapshot_pins_the_rows_for_every_statement(
    admin_db: DatabaseSession, clean_execution_records: None, postgres_only: None
) -> None:
    """Every statement in the snapshot reads the rows the first one saw.

    This is the property the usage and overview fan-outs rely on: a commit
    landing between two of their statements must not reach the second, or the
    parts of one response describe different instants.
    """
    now = datetime.now(UTC)
    await _seed(admin_db, now - timedelta(minutes=5))

    async with admin_db.read_snapshot() as session:
        before = (await session.execute(_COUNT)).scalar_one()
        await _seed(admin_db, now - timedelta(minutes=4))
        after = (await session.execute(_COUNT)).scalar_one()

    assert before == 1
    assert after == before

    # The write did commit — it is simply outside the snapshot.
    async with admin_db.session() as session:
        assert (await session.execute(_COUNT)).scalar_one() == 2


async def test_usage_totals_agree_with_their_buckets_across_an_interleaved_write(
    integration_context: Context,
    admin_db: DatabaseSession,
    clean_execution_records: None,
    postgres_only: None,
) -> None:
    """The headline total and the sum of the buckets come from one snapshot.

    ``overall_stats`` and ``time_buckets`` are separate statements, so this is
    the self-consistency a caller sees: whatever commits while the response is
    being assembled, the two must still describe the same rows.
    """
    now = datetime.now(UTC)
    until = int((now + timedelta(minutes=1)).timestamp())
    since = int((now - timedelta(minutes=30)).timestamp())
    for minutes in (20, 15, 10):
        await _seed(admin_db, now - timedelta(minutes=minutes))

    svc = MonitoringService(integration_context)

    # A row committed inside the window while the response is assembled is the
    # race condition itself; the snapshot decides it is simply not in this answer.
    async with admin_db.read_snapshot() as session:
        total = (
            await MonitoringRepository.overall_stats(
                session,
                datetime.fromtimestamp(since, tz=UTC),
                datetime.fromtimestamp(until, tz=UTC),
            )
        ).total
        await _seed(admin_db, now - timedelta(minutes=5))
        buckets = await MonitoringRepository.time_buckets(
            session,
            datetime.fromtimestamp(since, tz=UTC),
            datetime.fromtimestamp(until, tz=UTC),
            60,
        )

    assert total == 3
    assert sum(b.total for b in buckets) == total

    # And the assembled response is self-consistent too, now that the row is in.
    result = await svc.get_usage_stats(since=since, until=until)
    assert result.total == 4
    assert sum(int(b["total"]) for b in result.buckets) == result.total
