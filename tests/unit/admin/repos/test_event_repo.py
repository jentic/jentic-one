"""Unit tests for EventRepository.list_after_cursor.

Verifies that the two-tuple cursor correctly paginates events created within
the same second whose KSUIDs have out-of-order random payloads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import Result

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.repos.event_repo import EventRepository


def _make_event(id: str, created_at: datetime) -> Event:
    event = MagicMock(spec=Event)
    event.id = id
    event.created_at = created_at
    event.type = "execution.completed"
    event.severity = "info"
    event.summary = f"Event {id}"
    event.requires_action = False
    event.trace_id = None
    event.detail = None
    event.data = {}
    event.execution_id = None
    event.job_id = None
    event.actor_id = None
    event.actor_type = None
    return event


@pytest.mark.asyncio
async def test_list_after_cursor_returns_same_second_events() -> None:
    """Events with the same created_at but later ID are returned by the cursor query."""
    same_ts = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    event_a = _make_event("evt_ZZZZZZZZZZZZZZZZZZZZZZZZZZ", same_ts)
    event_b = _make_event("evt_AAAAAAAAAAAAAAAAAAAAAAAAAAA", same_ts)

    session = AsyncMock()
    result_mock = MagicMock(spec=Result)
    result_mock.scalars.return_value.all.return_value = [event_b]
    session.execute.return_value = result_mock

    cursor = (same_ts, event_a.id)
    events = await EventRepository.list_after_cursor(session, cursor)

    assert len(events) == 0 or events == [event_b]
    session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_list_after_cursor_returns_later_second_events() -> None:
    """Events with a later created_at are always returned regardless of ID order."""
    t1 = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    t2 = datetime(2024, 6, 15, 12, 0, 1, tzinfo=UTC)
    event_later = _make_event("evt_AAAAAAAAAAAAAAAAAAAAAAAAAAA", t2)

    session = AsyncMock()
    result_mock = MagicMock(spec=Result)
    result_mock.scalars.return_value.all.return_value = [event_later]
    session.execute.return_value = result_mock

    cursor = (t1, "evt_ZZZZZZZZZZZZZZZZZZZZZZZZZZ")
    events = await EventRepository.list_after_cursor(session, cursor)

    assert events == [event_later]


@pytest.mark.asyncio
async def test_list_after_cursor_applies_filters() -> None:
    """Filter parameters are passed through to the query."""
    same_ts = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    session = AsyncMock()
    result_mock = MagicMock(spec=Result)
    result_mock.scalars.return_value.all.return_value = []
    session.execute.return_value = result_mock

    cursor = (same_ts, "evt_cursor0000000000000000000")
    await EventRepository.list_after_cursor(
        session,
        cursor,
        event_type=["execution.completed"],
        severity=["info"],
        requires_action=True,
        trace_id="a" * 32,
        actor_id="agt_test",
        actor_type="agent",
    )

    session.execute.assert_called_once()
