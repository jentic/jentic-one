"""Integration tests: streaming-path persistence is bounded per process.

``_persist_streaming_outcome`` runs after the streaming response is sent, one
admin transaction per request. These tests burst ``M`` persists at a real admin
database whose ``execution_records`` table is held under an ``EXCLUSIVE`` lock,
so every insert that reaches Postgres blocks there. Counting the backends
blocked on that lock (``pg_stat_activity``) measures how many persists hold an
admin connection at once — it must never exceed the configured limit ``N``,
and every record must land exactly once after the lock is released.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import delete, func, select, text

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.web.routers.execute import (
    _persist_streaming_outcome,
    _streaming_persist_backlog,
    _streaming_persist_failures,
)
from jentic_one.broker.web.streaming import StreamingOutcome
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.schemas import OperationInfo

pytestmark = pytest.mark.integration

_ID_PREFIX = "integ-stream-bound-"
_LIMIT = 3
_BURST = 8

_BLOCKED_INSERTS = text(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE wait_event_type = 'Lock' "
    "AND pid <> pg_backend_pid() "
    "AND query ILIKE '%execution_records%'"
)


@pytest.fixture()
async def bounded_ctx(integration_config: AppConfig) -> AsyncGenerator[Context, None]:
    """Admin-only Context with a roomy pool and a persist limit of ``_LIMIT``.

    The pool is sized well above ``_BURST`` so an unbounded burst could hold
    one connection per persist — the semaphore, not pool starvation, is what
    the assertions observe.
    """
    if integration_config.databases.admin.backend != "postgres":
        pytest.skip("lock-wait observation needs Postgres pg_stat_activity")
    admin = integration_config.databases.admin.model_copy(update={"pool_max": 10})
    config = integration_config.model_copy(
        update={
            "databases": integration_config.databases.model_copy(update={"admin": admin}),
            "broker": integration_config.broker.model_copy(
                update={"streaming_persist_max_concurrency": _LIMIT}
            ),
        }
    )
    async with Context(config, allowed_dbs={"admin"}, refresh_providers_on_boot=False) as ctx:

        async def _cleanup() -> None:
            async with ctx.admin_db.transaction() as session:
                await session.execute(
                    delete(ExecutionRecord).where(ExecutionRecord.id.like(f"{_ID_PREFIX}%"))
                )

        await _cleanup()
        yield ctx
        await _cleanup()


@pytest.fixture()
def backlog_spy() -> Iterator[dict[str, Any]]:
    """Track the running value (and peak) of the persist-backlog up-down counter.

    Wraps ``add`` so the real OTel instrument still records every delta.
    """
    state: dict[str, Any] = {"waiting": 0, "in_flight": 0, "peak": 0}
    real_add: Callable[..., None] = _streaming_persist_backlog.add

    def _add(amount: int, attributes: dict[str, str] | None = None, **kw: Any) -> None:
        real_add(amount, attributes, **kw)
        assert attributes is not None
        state[attributes["state"]] += amount
        state["peak"] = max(state["peak"], state["waiting"] + state["in_flight"])

    with patch.object(_streaming_persist_backlog, "add", side_effect=_add):
        yield state


def _ctx_req() -> ExecuteRequestContext:
    return ExecuteRequestContext(
        upstream_url="https://api.example.com/data",
        method="GET",
        trace_id="trace-integ-bound",
        toolkit_id="tk-integ-bound",
        operation=OperationInfo(id="getData", path="/data", method="GET"),
        api_vendor="example",
        api_name="data-api",
        api_version="v1",
    )


async def _persist(ctx: Context, execution_id: str) -> None:
    outcome = StreamingOutcome(execution_id=execution_id, http_status=200)
    outcome.duration_ms = 5
    await _persist_streaming_outcome(
        outcome,
        ctx,
        _ctx_req(),
        datetime.now(UTC),
        "agent-integ-bound",
        "agent",
    )


async def _blocked_inserts(ctx: Context) -> int:
    async with ctx.admin_db.session() as session:
        return int((await session.execute(_BLOCKED_INSERTS)).scalar_one())


async def _observe_peak_blocked(ctx: Context, *, settle_s: float = 1.5) -> int:
    """Wait for the first ``_LIMIT`` inserts to block, then sample for ``settle_s``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10.0
    peak = 0
    while peak < _LIMIT and loop.time() < deadline:
        peak = max(peak, await _blocked_inserts(ctx))
        await asyncio.sleep(0.05)
    settle_until = loop.time() + settle_s
    while loop.time() < settle_until:
        peak = max(peak, await _blocked_inserts(ctx))
        await asyncio.sleep(0.05)
    return peak


async def _stored_ids(ctx: Context) -> list[str]:
    async with ctx.admin_db.session() as session:
        rows = await session.execute(
            select(ExecutionRecord.id).where(ExecutionRecord.id.like(f"{_ID_PREFIX}%"))
        )
        return [r[0] for r in rows]


@pytest.mark.asyncio
async def test_streaming_persist_burst_is_bounded_and_lossless(
    bounded_ctx: Context, backlog_spy: dict[str, Any]
) -> None:
    """A burst of M > N persists holds at most N admin writes; all M land once."""
    ids = [f"{_ID_PREFIX}{i:03d}" for i in range(_BURST)]

    async with asyncio.TaskGroup() as tg, bounded_ctx.admin_db.session() as lock_session:
        await lock_session.execute(text("LOCK TABLE execution_records IN EXCLUSIVE MODE"))
        for execution_id in ids:
            tg.create_task(_persist(bounded_ctx, execution_id))
        peak_blocked = await _observe_peak_blocked(bounded_ctx)
        queued = dict(backlog_spy)
    # The lock session exits first (rollback releases the table lock), then the
    # TaskGroup waits for every queued persist to finish.

    assert peak_blocked == _LIMIT
    assert queued["in_flight"] == _LIMIT
    assert queued["waiting"] == _BURST - _LIMIT
    assert backlog_spy["peak"] == _BURST
    assert backlog_spy["waiting"] == 0
    assert backlog_spy["in_flight"] == 0

    stored = await _stored_ids(bounded_ctx)
    assert sorted(stored) == ids


@pytest.mark.asyncio
async def test_streaming_persist_failure_is_counted_and_releases_slot(
    bounded_ctx: Context, backlog_spy: dict[str, Any]
) -> None:
    """A real write failure is counted and swallowed, and frees its slot."""
    execution_id = f"{_ID_PREFIX}dup"
    await _persist(bounded_ctx, execution_id)

    with patch.object(
        _streaming_persist_failures, "add", wraps=_streaming_persist_failures.add
    ) as failures:
        # Same primary key → IntegrityError inside the admin transaction.
        await _persist(bounded_ctx, execution_id)
        failures.assert_called_once_with(1)

    assert backlog_spy["waiting"] == 0
    assert backlog_spy["in_flight"] == 0

    # Every slot is free again: a full burst of N still proceeds concurrently.
    follow_up = [f"{_ID_PREFIX}after-{i}" for i in range(_LIMIT)]
    async with asyncio.TaskGroup() as tg:
        for fid in follow_up:
            tg.create_task(_persist(bounded_ctx, fid))

    async with bounded_ctx.admin_db.session() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(ExecutionRecord)
                .where(ExecutionRecord.id.like(f"{_ID_PREFIX}%"))
            )
        ).scalar_one()
    assert count == 1 + _LIMIT
