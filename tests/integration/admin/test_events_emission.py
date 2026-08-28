"""Integration tests for event emission from jobs and executions."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos import EventRepository
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.events import emit_credential_access, emit_event
from jentic_one.shared.models.events import EventSeverity, EventType

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


@pytest.fixture()
async def clean_jobs(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.session() as session:
        await session.execute(delete(Job))
        await session.commit()
    yield
    async with admin_db.session() as session:
        await session.execute(delete(Job))
        await session.commit()


async def test_emit_event_creates_event(admin_db: DatabaseSession, clean_events: None) -> None:
    async with admin_db.transaction() as session:
        event_id = await emit_event(
            session,
            type=EventType.IMPORT_FAILED,
            severity=EventSeverity.ERROR,
            summary="Import failed: timeout",
            requires_action=True,
            job_id="job_test00000000000000000",
            created_by="usr_test",
        )

    async with admin_db.session() as session:
        event = await EventRepository.get_by_id(session, event_id)
        assert event is not None
        assert event.type == "import.failed"
        assert event.severity == "error"
        assert event.requires_action is True
        assert event.job_id == "job_test00000000000000000"


async def test_emit_event_with_trace_id(admin_db: DatabaseSession, clean_events: None) -> None:
    trace = "a" * 32
    async with admin_db.transaction() as session:
        event_id = await emit_event(
            session,
            type=EventType.EXECUTION_COMPLETED,
            severity=EventSeverity.INFO,
            summary="Execution completed",
            trace_id=trace,
            execution_id="exc_test00000000000000000",
            created_by="usr_test",
        )

    async with admin_db.session() as session:
        event = await EventRepository.get_by_id(session, event_id)
        assert event is not None
        assert event.trace_id == trace
        assert event.execution_id == "exc_test00000000000000000"


async def test_emit_event_invalid_trace_id_rejected(
    admin_db: DatabaseSession, clean_events: None
) -> None:
    with pytest.raises(ValueError, match="trace_id must match"):
        async with admin_db.transaction() as session:
            await emit_event(
                session,
                type=EventType.EXECUTION_FAILED,
                severity=EventSeverity.ERROR,
                summary="test",
                trace_id="invalid-trace",
                created_by="usr_test",
            )


async def test_emitted_events_visible_via_list(
    admin_db: DatabaseSession, clean_events: None
) -> None:
    async with admin_db.transaction() as session:
        await emit_event(
            session,
            type=EventType.IMPORT_FAILED,
            severity=EventSeverity.ERROR,
            summary="Import failed: connection refused",
            requires_action=True,
            job_id="job_test00000000000000000",
            created_by="usr_test",
        )

    async with admin_db.session() as session:
        events = await EventRepository.list_all(session, event_type=["import.failed"])
        assert len(events) == 1
        assert events[0].type == "import.failed"
        assert events[0].requires_action is True


async def test_acknowledge_emitted_event(admin_db: DatabaseSession, clean_events: None) -> None:
    async with admin_db.transaction() as session:
        event_id = await emit_event(
            session,
            type=EventType.IMPORT_FAILED,
            severity=EventSeverity.ERROR,
            summary="Import failed",
            requires_action=True,
            created_by="usr_test",
        )

    async with admin_db.transaction() as session:
        acked = await EventRepository.acknowledge(
            session,
            event_id,
            acknowledged_by="usr_test00000000000000000",
            acknowledgement_note="Investigating",
        )
        assert acked.acknowledged is True
        assert acked.acknowledged_by == "usr_test00000000000000000"
        assert acked.acknowledged_at is not None


async def test_emit_credential_access_persists_audit_event(
    admin_db: DatabaseSession, clean_events: None
) -> None:
    async with admin_db.transaction() as session:
        event_id = await emit_credential_access(
            session,
            actor_id="agent_42",
            actor_type="agent",
            credential_id="cred_abc",
            provider="stripe",
            wire_type="api_key",
            api_vendor="stripe",
            api_name="charges",
            api_version="v1",
        )

    async with admin_db.session() as session:
        event = await EventRepository.get_by_id(session, event_id)
        assert event is not None
        assert event.type == EventType.CREDENTIAL_ACCESSED
        assert event.severity == EventSeverity.INFO.value
        assert event.actor_id == "agent_42"
        assert event.actor_type == "agent"
        assert event.created_by == "agent_42"
        assert event.requires_action is False
        assert event.data == {
            "credential_id": "cred_abc",
            "provider": "stripe",
            "wire_type": "api_key",
            "api_vendor": "stripe",
            "api_name": "charges",
            "api_version": "v1",
        }


async def test_emit_credential_access_never_records_secret(
    admin_db: DatabaseSession, clean_events: None
) -> None:
    """The audit event carries identifiers only — never the decrypted material."""
    async with admin_db.transaction() as session:
        event_id = await emit_credential_access(
            session,
            actor_id="agent_42",
            actor_type="agent",
            credential_id="cred_abc",
            provider="stripe",
            wire_type="api_key",
            api_vendor="stripe",
            api_name="charges",
            api_version="v1",
        )

    async with admin_db.session() as session:
        event = await EventRepository.get_by_id(session, event_id)
        assert event is not None
        serialized = f"{event.summary} {event.detail} {event.data}"
        assert "sk-" not in serialized
        assert "Bearer" not in serialized


@pytest.mark.parametrize(
    ("event_type", "severity", "requires_action"),
    [
        (EventType.EXECUTION_REPEATED_FAILURE, EventSeverity.CRITICAL, True),
        (EventType.CREDENTIAL_EXPIRING_SOON, EventSeverity.WARNING, False),
        (EventType.CREDENTIAL_EXPIRED, EventSeverity.ERROR, True),
    ],
)
async def test_emit_declared_event_round_trips(
    admin_db: DatabaseSession,
    clean_events: None,
    event_type: str,
    severity: EventSeverity,
    requires_action: bool,
) -> None:
    """The three previously-unimplemented event types persist and filter correctly."""
    async with admin_db.transaction() as session:
        event_id = await emit_event(
            session,
            type=event_type,
            severity=severity,
            summary=f"{event_type} happened",
            requires_action=requires_action,
            created_by="usr_test",
            data={"credential_id": "cred_x"},
        )

    async with admin_db.session() as session:
        event = await EventRepository.get_by_id(session, event_id)
        assert event is not None
        assert event.type == event_type
        assert event.severity == severity.value

        by_type = await EventRepository.list_all(session, event_type=[event_type])
        assert [e.id for e in by_type] == [event_id]

        by_severity = await EventRepository.list_all(session, severity=[severity.value])
        assert event_id in [e.id for e in by_severity]


async def test_exists_with_data_value_dedupes_mcp_session(
    admin_db: DatabaseSession, clean_events: None
) -> None:
    """The mcp.session_started dedupe lookup matches on type + data.session_id."""
    async with admin_db.transaction() as session:
        await emit_event(
            session,
            type=EventType.MCP_SESSION_STARTED,
            severity=EventSeverity.INFO,
            summary="MCP session started for agnt_test",
            created_by="agnt_test",
            actor_id="agnt_test",
            actor_type="agent",
            data={
                "session_id": "sess-abc123",
                "transport": "stdio",
                "client_name": "cursor",
                "client_version": "1.0",
            },
        )

    async with admin_db.session() as session:
        assert await EventRepository.exists_with_data_value(
            session,
            event_type=EventType.MCP_SESSION_STARTED,
            key="session_id",
            value="sess-abc123",
        )
        # A different session id does not match.
        assert not await EventRepository.exists_with_data_value(
            session,
            event_type=EventType.MCP_SESSION_STARTED,
            key="session_id",
            value="sess-other",
        )
        # The same data value under a different event type does not match.
        assert not await EventRepository.exists_with_data_value(
            session,
            event_type=EventType.EXECUTION_COMPLETED,
            key="session_id",
            value="sess-abc123",
        )
