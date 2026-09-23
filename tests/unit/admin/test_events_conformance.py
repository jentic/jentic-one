"""Unit tests for event response shape conformance with OpenAPI spec."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from jentic_one.admin.services.schemas.events import EventView
from jentic_one.admin.web.schemas.events import EventLinks, EventResponse
from jentic_one.shared.events import emit_event
from jentic_one.shared.models.events import EventSeverity


def _make_response() -> EventResponse:
    return EventResponse(
        event_id="evt_abc123",
        type="import.completed",
        severity=EventSeverity.INFO,
        summary="Import completed",
        requires_action=False,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        trace_id="a" * 32,
        links=EventLinks(
            self_="/events/evt_abc123",
            execution="/executions/exc_xyz",
            job="/jobs/job_456",
        ),
    )


def test_event_response_uses_event_id_not_id() -> None:
    resp = _make_response()
    data = resp.model_dump(by_alias=True)
    assert "event_id" in data
    assert "id" not in data


def test_event_response_links_self_present() -> None:
    resp = _make_response()
    data = resp.model_dump(by_alias=True)
    assert "_links" in data
    assert "self" in data["_links"]
    assert data["_links"]["self"] == "/events/evt_abc123"


def test_event_response_links_execution_and_job() -> None:
    resp = _make_response()
    data = resp.model_dump(by_alias=True)
    assert data["_links"]["execution"] == "/executions/exc_xyz"
    assert data["_links"]["job"] == "/jobs/job_456"


def test_event_response_has_no_acknowledgement_fields() -> None:
    resp = _make_response()
    data = resp.model_dump(by_alias=True)
    assert "acknowledgement_note" not in data
    assert "acknowledged" not in data
    assert "acknowledged_at" not in data
    assert "acknowledged_by" not in data


def test_event_response_no_flat_execution_id_or_job_id() -> None:
    resp = _make_response()
    data = resp.model_dump(by_alias=True)
    assert "execution_id" not in data
    assert "job_id" not in data


def test_event_response_severity_is_valid_enum() -> None:
    resp = _make_response()
    data = resp.model_dump(by_alias=True)
    assert data["severity"] in {"info", "warning", "error", "critical"}


def test_event_response_invalid_severity_rejected() -> None:
    with pytest.raises(ValueError):
        EventResponse(
            event_id="evt_abc123",
            type="import.completed",
            severity="invalid",  # type: ignore[arg-type]
            summary="test",
            requires_action=False,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            links=EventLinks(self_="/events/evt_abc123"),
        )


def test_event_response_json_serialization_uses_aliases() -> None:
    resp = _make_response()
    json_str = resp.model_dump_json(by_alias=True)
    assert '"event_id"' in json_str
    assert '"_links"' in json_str
    assert '"self"' in json_str


async def test_emit_event_invalid_trace_id_raises_value_error() -> None:
    session = AsyncMock()
    with pytest.raises(ValueError, match="trace_id must match"):
        await emit_event(
            session,
            type="import.completed",
            severity=EventSeverity.INFO,
            summary="test",
            trace_id="not-a-valid-trace-id",
            created_by="usr_test",
        )


async def test_emit_event_valid_trace_id_accepted() -> None:
    session = AsyncMock()
    mock_event = AsyncMock()
    mock_event.id = "evt_test123"
    with patch(
        "jentic_one.shared.events.EventRepository.create",
        return_value=mock_event,
    ):
        result = await emit_event(
            session,
            type="import.completed",
            severity=EventSeverity.INFO,
            summary="test",
            trace_id="a" * 32,
            created_by="usr_test",
        )
        assert result == "evt_test123"


async def test_emit_event_none_trace_id_accepted() -> None:
    session = AsyncMock()
    mock_event = AsyncMock()
    mock_event.id = "evt_test456"
    with patch(
        "jentic_one.shared.events.EventRepository.create",
        return_value=mock_event,
    ):
        result = await emit_event(
            session,
            type="import.completed",
            severity=EventSeverity.INFO,
            summary="test",
            trace_id=None,
            created_by="usr_test",
        )
        assert result == "evt_test456"


def test_event_view_includes_actor_fields() -> None:
    view = EventView(
        id="evt_x",
        type="import.completed",
        severity=EventSeverity.INFO,
        summary="test",
        requires_action=False,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        data={},
        actor_id="agt_123",
        actor_type="agent",
    )
    assert view.actor_id == "agt_123"
    assert view.actor_type == "agent"


def test_event_view_actor_fields_default_to_none() -> None:
    view = EventView(
        id="evt_y",
        type="import.completed",
        severity=EventSeverity.INFO,
        summary="test",
        requires_action=False,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        data={},
    )
    assert view.actor_id is None
    assert view.actor_type is None


def test_event_response_includes_actor_fields() -> None:
    resp = EventResponse(
        event_id="evt_abc123",
        type="import.completed",
        severity=EventSeverity.INFO,
        summary="test",
        requires_action=False,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        actor_id="usr_456",
        actor_type="user",
        links=EventLinks(self_="/events/evt_abc123"),
    )
    data = resp.model_dump(by_alias=True)
    assert data["actor_id"] == "usr_456"
    assert data["actor_type"] == "user"


def test_event_response_actor_fields_default_to_none() -> None:
    resp = _make_response()
    data = resp.model_dump(by_alias=True)
    assert data["actor_id"] is None
    assert data["actor_type"] is None
