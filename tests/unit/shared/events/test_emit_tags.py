"""Unit tests for emit_event tag validation + telemetry forwarding."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from jentic_one.shared.events import emit_event
from jentic_one.shared.models.events import (
    ErrorSource,
    EventSeverity,
    EventType,
    HostOs,
    SpecSource,
)
from jentic_one.shared.telemetry.events import TelemetryEventName


class _RecordingSink:
    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self.records: list[tuple[TelemetryEventName, tuple[Any, ...], str | None]] = []

    def record(
        self, name: TelemetryEventName, tags: Any = (), actor_type: str | None = None
    ) -> None:
        self.records.append((name, tuple(tags), actor_type))


class _FakeEvent:
    id = "evt_123"


def _fake_create() -> AsyncMock:
    return AsyncMock(return_value=_FakeEvent())


@pytest.mark.asyncio
async def test_valid_tag_stored_on_event_and_forwarded() -> None:
    """A tag allowed for the event is stored in data and forwarded to the sink."""
    sink = _RecordingSink(enabled=True)
    create = _fake_create()

    with (
        patch("jentic_one.shared.events.EventRepository.create", create),
        patch("jentic_one.shared.events.get_active_sink", return_value=sink),
    ):
        await emit_event(
            session=AsyncMock(),
            type=EventType.EXECUTION_FAILED,
            severity=EventSeverity.WARNING,
            summary="execution failed",
            created_by="usr_1",
            tags={ErrorSource.AUTH_THIRDPARTY},
        )

    stored_data = create.call_args.kwargs["data"]
    assert stored_data["tags"] == ["auth_thirdparty"]
    assert sink.records == [
        (TelemetryEventName.BROKER_EXECUTION_FAILED, (ErrorSource.AUTH_THIRDPARTY,), None)
    ]


@pytest.mark.asyncio
async def test_all_valid_tags_forwarded_to_sink() -> None:
    """Every validated tag on an emission is forwarded — not just the first."""
    sink = _RecordingSink(enabled=True)
    create = _fake_create()

    with (
        patch("jentic_one.shared.events.EventRepository.create", create),
        patch("jentic_one.shared.events.get_active_sink", return_value=sink),
    ):
        await emit_event(
            session=AsyncMock(),
            type=EventType.EXECUTION_FAILED,
            severity=EventSeverity.WARNING,
            summary="execution failed",
            created_by="usr_1",
            tags={ErrorSource.AUTH_JENTIC, ErrorSource.AUTH_THIRDPARTY_FORBIDDEN},
        )

    assert len(sink.records) == 1
    name, forwarded, _actor_type_recorded = sink.records[0]
    assert name == TelemetryEventName.BROKER_EXECUTION_FAILED
    assert set(forwarded) == {ErrorSource.AUTH_JENTIC, ErrorSource.AUTH_THIRDPARTY_FORBIDDEN}
    stored_data = create.call_args.kwargs["data"]
    assert set(stored_data["tags"]) == {"auth_jentic", "auth_thirdparty_forbidden"}


@pytest.mark.asyncio
async def test_host_os_tag_stored_and_forwarded_on_instance_booted() -> None:
    """The per-boot instance_booted event carries the HostOs tag to the sink."""
    sink = _RecordingSink(enabled=True)
    create = _fake_create()

    with (
        patch("jentic_one.shared.events.EventRepository.create", create),
        patch("jentic_one.shared.events.get_active_sink", return_value=sink),
    ):
        await emit_event(
            session=AsyncMock(),
            type=EventType.INSTANCE_BOOTED,
            severity=EventSeverity.INFO,
            summary="Instance booted",
            created_by=None,
            tags={HostOs.current()},
        )

    stored_data = create.call_args.kwargs["data"]
    assert stored_data["tags"] == [str(HostOs.current())]
    assert sink.records == [(TelemetryEventName.INSTANCE_BOOTED, (HostOs.current(),), None)]


@pytest.mark.asyncio
async def test_invalid_tag_dropped_with_warning_event_still_emits() -> None:
    """A tag not allowed for the event is dropped (logged) — emission never raises."""
    sink = _RecordingSink(enabled=True)
    create = _fake_create()

    with (
        patch("jentic_one.shared.events.EventRepository.create", create),
        patch("jentic_one.shared.events.get_active_sink", return_value=sink),
        patch("jentic_one.shared.events.logger.warning") as warn,
    ):
        await emit_event(
            session=AsyncMock(),
            type=EventType.EXECUTION_FAILED,
            severity=EventSeverity.WARNING,
            summary="execution failed",
            created_by="usr_1",
            tags={SpecSource.CATALOG},  # not allowed for EXECUTION_FAILED
        )

    create.assert_awaited_once()
    assert create.call_args.kwargs["data"] is None
    warn.assert_called_once()
    # No valid tag → forwarded with no tags.
    assert sink.records == [(TelemetryEventName.BROKER_EXECUTION_FAILED, (), None)]


@pytest.mark.asyncio
async def test_internal_only_event_not_forwarded() -> None:
    """An event type absent from TELEMETRY_EVENTS is never handed to the sink."""
    sink = _RecordingSink(enabled=True)

    with (
        patch("jentic_one.shared.events.EventRepository.create", _fake_create()),
        patch("jentic_one.shared.events.get_active_sink", return_value=sink),
    ):
        await emit_event(
            session=AsyncMock(),
            type=EventType.UPSTREAM_CIRCUIT_OPEN,  # internal-only
            severity=EventSeverity.WARNING,
            summary="circuit open",
            created_by=None,
        )

    assert sink.records == []


@pytest.mark.asyncio
async def test_disabled_sink_not_forwarded() -> None:
    """When telemetry is disabled, an allowlisted event is not forwarded."""
    sink = _RecordingSink(enabled=False)

    with (
        patch("jentic_one.shared.events.EventRepository.create", _fake_create()),
        patch("jentic_one.shared.events.get_active_sink", return_value=sink),
    ):
        await emit_event(
            session=AsyncMock(),
            type=EventType.CREDENTIAL_STORED,
            severity=EventSeverity.INFO,
            summary="stored",
            created_by="usr_1",
        )

    assert sink.records == []
