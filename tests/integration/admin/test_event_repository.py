"""Integration tests for EventRepository against real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.repos import EventRepository
from jentic_one.admin.services.errors import EventNotFoundError
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_events(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.session() as session:
        await session.execute(delete(Event))
        await session.commit()
    yield
    async with admin_db.session() as session:
        await session.execute(delete(Event))
        await session.commit()


async def test_create_generates_ksuid(admin_db: DatabaseSession, clean_events: None) -> None:
    async with admin_db.session() as session:
        event = await EventRepository.create(
            session,
            type="execution.completed",
            severity="info",
            summary="Test event",
            created_by="usr_test",
        )
        await session.commit()
        assert event.id.startswith("evt_")
        assert len(event.id) == 28


async def test_create_and_get_by_id(admin_db: DatabaseSession, clean_events: None) -> None:
    async with admin_db.session() as session:
        event = await EventRepository.create(
            session,
            type="execution.failed",
            severity="error",
            summary="Something broke",
            requires_action=True,
            trace_id="abc123def456789012345678",
            detail="Full stack trace here",
            data={"key": "value"},
            execution_id="exec_test0000000000000000",
            job_id="job_test00000000000000000",
            created_by="usr_test",
        )
        await session.commit()
        event_id = event.id

    async with admin_db.session() as session:
        loaded = await EventRepository.get_by_id(session, event_id)
        assert loaded is not None
        assert loaded.type == "execution.failed"
        assert loaded.severity == "error"
        assert loaded.summary == "Something broke"
        assert loaded.requires_action is True
        assert loaded.acknowledged is False
        assert loaded.trace_id == "abc123def456789012345678"
        assert loaded.data == {"key": "value"}


async def test_acknowledge(admin_db: DatabaseSession, clean_events: None) -> None:
    async with admin_db.session() as session:
        event = await EventRepository.create(
            session,
            type="alert.triggered",
            severity="warning",
            summary="High latency",
            requires_action=True,
            created_by="usr_test",
        )
        await session.commit()
        event_id = event.id

    async with admin_db.session() as session:
        acked = await EventRepository.acknowledge(
            session,
            event_id,
            acknowledged_by="usr_test00000000000000000",
            acknowledgement_note="Looking into it",
        )
        await session.commit()
        assert acked.acknowledged is True
        assert acked.acknowledged_at is not None
        assert acked.acknowledged_by == "usr_test00000000000000000"
        assert acked.acknowledgement_note == "Looking into it"


async def test_acknowledge_not_found(admin_db: DatabaseSession, clean_events: None) -> None:
    async with admin_db.session() as session:
        with pytest.raises(EventNotFoundError):
            await EventRepository.acknowledge(
                session, "evt_nonexistent000000000", acknowledged_by="usr_x"
            )


async def test_list_all_with_filters(admin_db: DatabaseSession, clean_events: None) -> None:
    async with admin_db.session() as session:
        await EventRepository.create(
            session,
            type="a.one",
            severity="info",
            summary="s1",
            requires_action=False,
            created_by="usr_test",
        )
        await EventRepository.create(
            session,
            type="b.two",
            severity="error",
            summary="s2",
            requires_action=True,
            created_by="usr_test",
        )
        await EventRepository.create(
            session,
            type="a.one",
            severity="error",
            summary="s3",
            requires_action=False,
            created_by="usr_test",
        )
        await session.commit()

    async with admin_db.session() as session:
        by_type = await EventRepository.list_all(session, event_type=["a.one"])
        assert len(by_type) == 2

        by_severity = await EventRepository.list_all(session, severity=["error"])
        assert len(by_severity) == 2

        by_action = await EventRepository.list_all(session, requires_action=True)
        assert len(by_action) == 1


async def test_list_after_cursor(admin_db: DatabaseSession, clean_events: None) -> None:
    """The stream's paging query returns strictly-after rows in cursor order.

    A strict ``created_at >`` repository method is deliberately absent: its
    exclusive semantics were the root of the lost-event race.
    """
    async with admin_db.session() as session:
        await EventRepository.create(
            session, type="old", severity="info", summary="old event", created_by="usr_test"
        )
        await session.commit()

    cutoff = datetime.now(UTC) - timedelta(seconds=1)

    async with admin_db.session() as session:
        await EventRepository.create(
            session, type="new", severity="info", summary="new event", created_by="usr_test"
        )
        await session.commit()

    async with admin_db.session() as session:
        recent = await EventRepository.list_after_cursor(session, (cutoff, ""))
        assert len(recent) >= 1
        assert all(e.created_at > cutoff for e in recent)
