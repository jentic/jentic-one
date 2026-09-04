"""Integration tests for event emission from jobs and executions."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos import EventRepository
from jentic_one.shared.db.errors import DatabaseIntegrityError
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
    """These event types persist and filter correctly."""
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


async def _emit_session_started(session: AsyncSession, session_id: str) -> None:
    await emit_event(
        session,
        type=EventType.MCP_SESSION_STARTED,
        severity=EventSeverity.INFO,
        summary="MCP session started for agnt_test",
        created_by="agnt_test",
        actor_id="agnt_test",
        actor_type="agent",
        data={"session_id": session_id, "transport": "stdio"},
    )


async def test_mcp_session_started_duplicate_insert_rejected_by_unique_index(
    admin_db: DatabaseSession, clean_events: None
) -> None:
    """The partial unique index makes the emit idempotent at the store.

    Two workers racing past the exists_with_data_value lookup both insert;
    uq_events_mcp_session_started_session must reject the second commit so the
    table can never hold two mcp.session_started rows for one session id.
    ``transaction()`` maps the raw IntegrityError to DatabaseIntegrityError.
    """
    async with admin_db.transaction() as session:
        await _emit_session_started(session, "sess-race")

    with pytest.raises(DatabaseIntegrityError):
        async with admin_db.transaction() as session:
            await _emit_session_started(session, "sess-race")

    async with admin_db.session() as session:
        rows = (
            (
                await session.execute(
                    select(Event.id).where(Event.type == EventType.MCP_SESSION_STARTED)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


async def test_mcp_session_started_index_scoped_to_one_event_type(
    admin_db: DatabaseSession, clean_events: None
) -> None:
    """Other event types (and other session ids) are not constrained by the index."""
    async with admin_db.transaction() as session:
        await _emit_session_started(session, "sess-scoped")
        # A second session id of the same type is fine.
        await _emit_session_started(session, "sess-scoped-2")
        # A different event type reusing the same session id is fine too —
        # the unique index is partial, scoped to mcp.session_started.
        await emit_event(
            session,
            type=EventType.MCP_CONFIG_REGISTERED,
            severity=EventSeverity.INFO,
            summary="MCP config registered",
            created_by="agnt_test",
            data={"session_id": "sess-scoped"},
        )
        await emit_event(
            session,
            type=EventType.MCP_CONFIG_REGISTERED,
            severity=EventSeverity.INFO,
            summary="MCP config registered again",
            created_by="agnt_test",
            data={"session_id": "sess-scoped"},
        )

    async with admin_db.session() as session:
        rows = (await session.execute(select(Event.id))).scalars().all()
        assert len(rows) == 4
