"""Integration tests for EventStreamService against real PostgreSQL."""

from __future__ import annotations

import asyncio
import contextlib
import warnings
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import patch

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import SAWarning

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.repos import EventRepository
from jentic_one.admin.services.event_stream_service import EventStreamService
from jentic_one.admin.services.schemas.events import EventView, Heartbeat
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_events(integration_context: Context) -> None:
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(Event))
        await session.commit()


async def test_stream_yields_events(integration_context: Context, clean_events: None) -> None:
    ctx = integration_context
    since = datetime.now(UTC) - timedelta(seconds=1)

    async with ctx.admin_db.session() as session:
        await EventRepository.create(
            session,
            type="toolkit.error",
            severity="error",
            summary="Stream test event",
            created_by="usr_test",
        )
        await session.commit()

    service = EventStreamService(ctx)
    items: list[EventView | Heartbeat] = []
    gen = service.stream(since=since, poll_interval_seconds=0)
    async for item in gen:
        items.append(item)
        if len(items) >= 1:
            break

    assert len(items) == 1
    assert isinstance(items[0], EventView)
    assert items[0].summary == "Stream test event"


async def test_stream_emits_heartbeat_when_no_events(
    integration_context: Context, clean_events: None
) -> None:
    ctx = integration_context
    service = EventStreamService(ctx)
    gen = service.stream(since=datetime.now(UTC), poll_interval_seconds=0)
    item = await gen.__anext__()
    assert isinstance(item, Heartbeat)


async def test_stream_resumes_from_last_event_id(
    integration_context: Context, clean_events: None
) -> None:
    """Passing last_event_id skips earlier events and only yields later ones."""
    ctx = integration_context

    id_first = "evt_00000001aaaaaaaaaaaaaaaa"
    id_second = "evt_00000002bbbbbbbbbbbbbbbb"

    async with ctx.admin_db.session() as session:
        first = Event(
            id=id_first,
            type="toolkit.error",
            severity="error",
            summary="Event one",
            requires_action=False,
            acknowledged=False,
            data={},
            created_by="usr_test",
        )
        second = Event(
            id=id_second,
            type="toolkit.error",
            severity="error",
            summary="Event two",
            requires_action=False,
            acknowledged=False,
            data={},
            created_by="usr_test",
        )
        session.add_all([first, second])
        await session.commit()

    service = EventStreamService(ctx)
    items: list[EventView | Heartbeat] = []
    gen = service.stream(last_event_id=id_first, poll_interval_seconds=0)
    async for item in gen:
        items.append(item)
        if len(items) >= 1:
            break

    assert len(items) == 1
    assert isinstance(items[0], EventView)
    assert items[0].id == id_second
    assert items[0].summary == "Event two"


async def test_stream_last_event_id_takes_precedence_over_since(
    integration_context: Context, clean_events: None
) -> None:
    """When both last_event_id and since are provided, cursor-based resumption wins."""
    ctx = integration_context
    old_time = datetime(2020, 1, 1, tzinfo=UTC)

    id_first = "evt_00000003cccccccccccccccc"
    id_second = "evt_00000004dddddddddddddddd"

    async with ctx.admin_db.session() as session:
        first = Event(
            id=id_first,
            type="toolkit.error",
            severity="error",
            summary="Event one",
            requires_action=False,
            acknowledged=False,
            data={},
            created_by="usr_test",
        )
        second = Event(
            id=id_second,
            type="toolkit.error",
            severity="error",
            summary="Event two",
            requires_action=False,
            acknowledged=False,
            data={},
            created_by="usr_test",
        )
        session.add_all([first, second])
        await session.commit()

    service = EventStreamService(ctx)
    items: list[EventView | Heartbeat] = []
    gen = service.stream(since=old_time, last_event_id=id_first, poll_interval_seconds=0)
    async for item in gen:
        items.append(item)
        if len(items) >= 1:
            break

    assert len(items) == 1
    assert isinstance(items[0], EventView)
    assert items[0].id == id_second


async def test_stream_same_second_events_not_dropped(
    integration_context: Context, clean_events: None
) -> None:
    """Events with the same created_at but reversed KSUID order are both yielded."""
    ctx = integration_context
    same_ts = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)

    # id_a is lexicographically LARGER than id_b, but they share the same created_at.
    # Under pure KSUID ordering, cursor set to id_a would skip id_b permanently.
    id_a = "evt_ZZZZZZZZZZZZZZZZZZZZZZZZ"
    id_b = "evt_AAAAAAAAAAAAAAAAAAAAAAAB"

    async with ctx.admin_db.session() as session:
        event_a = Event(
            id=id_a,
            type="execution.completed",
            severity="info",
            summary="Event A (larger KSUID)",
            requires_action=False,
            acknowledged=False,
            data={},
            created_by="usr_test",
            created_at=same_ts,
        )
        event_b = Event(
            id=id_b,
            type="execution.completed",
            severity="info",
            summary="Event B (smaller KSUID)",
            requires_action=False,
            acknowledged=False,
            data={},
            created_by="usr_test",
            created_at=same_ts,
        )
        session.add_all([event_a, event_b])
        await session.commit()

    service = EventStreamService(ctx)
    items: list[EventView | Heartbeat] = []
    # Resume from event_b (smaller ID) — event_a (larger ID, same second) must appear.
    gen = service.stream(last_event_id=id_b, poll_interval_seconds=0)
    async for item in gen:
        items.append(item)
        if len(items) >= 1:
            break

    assert len(items) == 1
    assert isinstance(items[0], EventView)
    assert items[0].id == id_a


async def test_stream_delivers_event_committed_after_watermark_passed(
    integration_context: Context, clean_events: None
) -> None:
    """A late-committing event whose created_at is already behind the watermark still arrives.

    ``created_at`` is stamped at ORM flush, but the row only becomes visible at
    COMMIT — e.g. ``agent.self_registered`` is stamped early inside the DCR
    ``/register`` transaction and committed at the end. A strict high-watermark
    poll would skip it forever (the rail then misses the registration until a
    page refresh). The overlap-window re-query must deliver it — exactly once.
    """
    ctx = integration_context
    service = EventStreamService(ctx)

    # Connect FIRST: watermark = now. The event then appears with a created_at
    # 5 seconds in the past (inside the overlap), as if its transaction had
    # been open across the connect and committed late.
    gen = cast(
        "AsyncGenerator[EventView | Heartbeat, None]",
        service.stream(poll_interval_seconds=0, overlap_seconds=15.0),
    )
    first = await gen.__anext__()
    assert isinstance(first, Heartbeat)

    late_id = "evt_1a7ec0331775c0331775c033"
    async with ctx.admin_db.session() as session:
        session.add(
            Event(
                id=late_id,
                type="agent.self_registered",
                severity="info",
                summary="Late-committed registration",
                requires_action=True,
                acknowledged=False,
                data={},
                created_by="usr_test",
                created_at=datetime.now(UTC) - timedelta(seconds=5),
            )
        )
        await session.commit()

    item = await gen.__anext__()
    assert isinstance(item, EventView)
    assert item.id == late_id

    # The overlap re-query must not re-yield it on the next poll.
    dup_check = await gen.__anext__()
    assert isinstance(dup_check, Heartbeat)
    await gen.aclose()


async def test_stream_accepts_timezone_naive_since(
    integration_context: Context, clean_events: None
) -> None:
    """A naive ``since`` (offset-less ``?since=`` query param) must not crash the stream.

    FastAPI parses ``?since=2026-07-27T10:00:00`` (no offset) as a NAIVE
    datetime; rows come back timezone-aware from ``UTCDateTime``. The
    overlap-window fix introduced Python-side comparisons against ``since``
    (watermark advance + dedup pruning) which raise ``TypeError`` on
    naive-vs-aware — killing the generator mid-SSE and hot-looping the client's
    reconnect. The service must coerce naive input to UTC.
    """
    ctx = integration_context

    async with ctx.admin_db.session() as session:
        await EventRepository.create(
            session,
            type="toolkit.error",
            severity="error",
            summary="After naive since",
            created_by="usr_test",
        )
        await session.commit()

    naive_since = (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None)
    service = EventStreamService(ctx)
    gen = cast(
        "AsyncGenerator[EventView | Heartbeat, None]",
        service.stream(since=naive_since, poll_interval_seconds=0),
    )
    item = await gen.__anext__()
    assert isinstance(item, EventView)
    assert item.summary == "After naive since"
    # The pruning comprehension also compares timestamps — drive one more poll.
    beat = await gen.__anext__()
    assert isinstance(beat, Heartbeat)
    await gen.aclose()


async def test_stream_fresh_connect_does_not_replay_visible_history(
    integration_context: Context, clean_events: None
) -> None:
    """A fresh connect (``since=None``) must not replay already-visible events.

    The overlap window exists to rescue LATE COMMITS, not to re-deliver
    history: an event committed (visible) before the client connected is
    already served by the durable backlog fetch, and replaying it over SSE
    re-fires client-side toasts/audio/invalidations on every page load. The
    connect-time seed scan must swallow it.
    """
    ctx = integration_context

    async with ctx.admin_db.session() as session:
        await EventRepository.create(
            session,
            type="toolkit.error",
            severity="error",
            summary="Committed before connect",
            created_by="usr_test",
        )
        await session.commit()

    service = EventStreamService(ctx)
    gen = cast(
        "AsyncGenerator[EventView | Heartbeat, None]",
        service.stream(poll_interval_seconds=0, overlap_seconds=15.0),
    )
    first = await gen.__anext__()
    assert isinstance(first, Heartbeat)
    await gen.aclose()


async def test_stream_future_since_is_clamped_to_now(
    integration_context: Context, clean_events: None
) -> None:
    """A future ``since`` (fast client clock) must not black-hole the stream.

    Un-clamped, ``watermark = since`` puts the poll window's lower bound in the
    future, so every normally-stamped event committed after connect sits below
    it and is never returned — symmetric to the watermark-advance clamp for
    future-stamped ROWS. The service must floor ``since`` at the present.
    """
    ctx = integration_context
    service = EventStreamService(ctx)
    gen = cast(
        "AsyncGenerator[EventView | Heartbeat, None]",
        service.stream(
            since=datetime.now(UTC) + timedelta(seconds=120),
            poll_interval_seconds=0,
            overlap_seconds=15.0,
        ),
    )
    first = await gen.__anext__()
    assert isinstance(first, Heartbeat)

    async with ctx.admin_db.session() as session:
        await EventRepository.create(
            session,
            type="toolkit.error",
            severity="error",
            summary="After future since",
            created_by="usr_test",
        )
        await session.commit()

    item = await gen.__anext__()
    assert isinstance(item, EventView)
    assert item.summary == "After future since"
    await gen.aclose()


async def test_stream_filters_with_cursor(integration_context: Context, clean_events: None) -> None:
    """Cursor-based resumption composes correctly with event_type filters."""
    ctx = integration_context

    id_first = "evt_00000005eeeeeeeeeeeeeeee"
    id_second = "evt_00000006ffffffffffffffff"
    id_third = "evt_00000007aaaaaaaaaaaaaaa0"

    async with ctx.admin_db.session() as session:
        session.add_all(
            [
                Event(
                    id=id_first,
                    type="toolkit.error",
                    severity="error",
                    summary="Error event",
                    requires_action=False,
                    acknowledged=False,
                    data={},
                    created_by="usr_test",
                ),
                Event(
                    id=id_second,
                    type="import.completed",
                    severity="info",
                    summary="Import event",
                    requires_action=False,
                    acknowledged=False,
                    data={},
                    created_by="usr_test",
                ),
                Event(
                    id=id_third,
                    type="toolkit.error",
                    severity="error",
                    summary="Another error",
                    requires_action=False,
                    acknowledged=False,
                    data={},
                    created_by="usr_test",
                ),
            ]
        )
        await session.commit()

    service = EventStreamService(ctx)
    items: list[EventView | Heartbeat] = []
    gen = service.stream(
        last_event_id=id_first,
        event_type=["toolkit.error"],
        poll_interval_seconds=0,
    )
    async for item in gen:
        items.append(item)
        if len(items) >= 1:
            break

    assert len(items) == 1
    assert isinstance(items[0], EventView)
    assert items[0].id == id_third
    assert items[0].summary == "Another error"


async def test_stream_cancellation_mid_query_returns_pooled_connection(
    integration_context: Context, clean_events: None
) -> None:
    """Cancelling the stream *while a query is in flight* must not strand a connection (#627).

    Regression for the SSE-disconnect leak. The earlier version of this test only
    consumed the first heartbeat — which ``stream()`` yields *outside* the
    ``async with session()`` block — so at cancellation time no connection was
    ever checked out and the test passed even with the bug present (false
    positive).

    Here we patch ``EventRepository.list_after_cursor`` to hang forever, freezing the
    generator *inside* the ``async with self._ctx.admin_db.session()`` block with
    a connection actively checked out of the pool. We then prove:

    1. the connection is genuinely acquired (``checkedout() == baseline + 1``)
       while the query hangs, and
    2. cancelling the task unwinds the session context manager and returns the
       connection to the pool (``checkedout() == baseline``),

    with no "non-checked-in connection" ``SAWarning`` leaking out.
    """
    ctx = integration_context
    pool = ctx.admin_db.engine.pool

    def checked_out() -> int:
        # ``checkedout`` lives on the QueuePool subclass, not the base ``Pool``
        # type mypy sees; both Postgres and SQLite engines use such a pool.
        return cast("int", pool.checkedout())  # type: ignore[attr-defined]

    baseline = checked_out()

    # Force a real connection checkout, then block *inside* the session so the
    # generator freezes while the session — and its pooled connection — is held
    # open. We drive a genuine query through the real session (DB mocking is
    # banned; we only delay the repo call) so the lazy ``AsyncSession`` actually
    # pulls a connection from the pool before hanging.
    release = asyncio.Event()
    real_list_after_cursor = EventRepository.list_after_cursor

    async def _hang(session: object, *args: object, **kwargs: object) -> list[Event]:
        await real_list_after_cursor(session, *args, **kwargs)  # type: ignore[arg-type]
        await release.wait()
        return []

    service = EventStreamService(ctx)
    gen = cast(
        "AsyncGenerator[EventView | Heartbeat, None]",
        service.stream(since=datetime.now(UTC), poll_interval_seconds=60),
    )

    async def drive() -> None:
        async for _ in gen:
            break

    with (
        warnings.catch_warnings(),
        patch.object(EventRepository, "list_after_cursor", new=_hang),
    ):
        warnings.simplefilter("error", SAWarning)
        task = asyncio.ensure_future(drive())
        # Yield to the loop so the generator enters the session block and the
        # patched query call blocks on ``release`` with the connection acquired.
        await asyncio.sleep(0.1)

        assert checked_out() == baseline + 1, (
            "expected the stream to be hanging with a connection checked out"
        )

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # Closing the generator runs its cleanup (the same path GeneratorExit
        # triggers on real client disconnect).
        await gen.aclose()

    assert checked_out() == baseline
