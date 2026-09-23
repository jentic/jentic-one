"""Integration tests for EventService against real PostgreSQL."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.repos import EventRepository
from jentic_one.admin.services.errors import EventNotFoundError
from jentic_one.admin.services.event_service import EventService
from jentic_one.admin.services.schemas.events import EventFilter
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_events(integration_context: Context) -> None:
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(Event))
        await session.commit()


async def test_list_returns_page(integration_context: Context, clean_events: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        for i in range(3):
            await EventRepository.create(
                session,
                type="toolkit.error",
                severity="error",
                summary=f"Event {i}",
                created_by="usr_test",
            )
        await session.commit()

    service = EventService(ctx)
    page = await service.list_all(EventFilter(), limit=50)
    assert len(page.data) == 3
    assert page.has_more is False


async def test_list_pagination(integration_context: Context, clean_events: None) -> None:
    ctx = integration_context
    for i in range(5):
        async with ctx.admin_db.session() as session:
            await EventRepository.create(
                session,
                type="toolkit.error",
                severity="warning",
                summary=f"Event {i}",
                created_by="usr_test",
            )
            await session.commit()

    service = EventService(ctx)
    page1 = await service.list_all(EventFilter(), limit=2)
    assert len(page1.data) == 2
    assert page1.has_more is True
    assert page1.next_cursor is not None

    page2 = await service.list_all(EventFilter(), cursor=page1.next_cursor, limit=2)
    assert len(page2.data) == 2
    assert page2.has_more is True


async def test_list_with_type_filter(integration_context: Context, clean_events: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        await EventRepository.create(
            session, type="toolkit.error", severity="error", summary="Error", created_by="usr_test"
        )
        await EventRepository.create(
            session, type="job.completed", severity="info", summary="Done", created_by="usr_test"
        )
        await session.commit()

    service = EventService(ctx)
    page = await service.list_all(EventFilter(event_type=["toolkit.error"]), limit=50)
    assert len(page.data) == 1
    assert page.data[0].type == "toolkit.error"


async def test_get_by_id(integration_context: Context, clean_events: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        event = await EventRepository.create(
            session,
            type="toolkit.error",
            severity="error",
            summary="Test event",
            trace_id="trace_abc",
            created_by="usr_test",
        )
        await session.commit()
    event_id = event.id

    service = EventService(ctx)
    view = await service.get_by_id(event_id)
    assert view.id == event_id
    assert view.type == "toolkit.error"
    assert view.trace_id == "trace_abc"


async def test_get_by_id_not_found(integration_context: Context, clean_events: None) -> None:
    service = EventService(integration_context)
    with pytest.raises(EventNotFoundError):
        await service.get_by_id("evt_nonexistent000000000000")
