"""Unit tests for emit_event tag validation + telemetry forwarding."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from jentic_one.shared.events import emit_event
from jentic_one.shared.models.actors import Origin
from jentic_one.shared.models.events import (
    EVENT_TAGS,
    ErrorSource,
    EventSeverity,
    EventTag,
    EventType,
    HostOs,
    McpClient,
    McpConfigRuntime,
    SpecSource,
)
from jentic_one.shared.telemetry.events import TELEMETRY_EVENTS, TelemetryEventName


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


# --- Regression suite over the widened EVENT_TAGS map (lane D, #1177) ---------
# EVENT_TAGS values are now tuples of allowed tag types; the widening touches
# _validate_tags for every tagged event, so pin every (event, allowed-type)
# pair: a representative member of each allowed enum must be stored + forwarded.

_TAGGED_EVENT_CASES: list[tuple[str, EventTag]] = [
    (event_type, cast("EventTag", next(iter(allowed_type))))
    for event_type, allowed_types in sorted(EVENT_TAGS.items())
    for allowed_type in allowed_types
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "tag"),
    _TAGGED_EVENT_CASES,
    ids=[f"{e}:{type(t).__name__}" for e, t in _TAGGED_EVENT_CASES],
)
async def test_every_tagged_event_accepts_each_allowed_tag_type(
    event_type: str, tag: EventTag
) -> None:
    """Each event in EVENT_TAGS stores + forwards a member of every allowed type."""
    sink = _RecordingSink(enabled=True)
    create = _fake_create()

    with (
        patch("jentic_one.shared.events.EventRepository.create", create),
        patch("jentic_one.shared.events.get_active_sink", return_value=sink),
        patch("jentic_one.shared.events.logger.warning") as warn,
    ):
        await emit_event(
            session=AsyncMock(),
            type=event_type,
            severity=EventSeverity.INFO,
            summary="tagged event",
            created_by="usr_1",
            tags={tag},
        )

    warn.assert_not_called()
    assert create.call_args.kwargs["data"]["tags"] == [str(tag)]
    # Every tagged event happens to be on the telemetry allowlist today; the
    # validated tag must ride the forwarded record.
    assert event_type in TELEMETRY_EVENTS
    assert sink.records == [(TELEMETRY_EVENTS[event_type], (tag,), None)]


@pytest.mark.asyncio
async def test_execution_failed_carries_error_source_and_origin_together() -> None:
    """The widened map lets EXECUTION_FAILED split by both source and origin."""
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
            severity=EventSeverity.ERROR,
            summary="execution failed",
            created_by="agt_1",
            tags={ErrorSource.AUTH_THIRDPARTY_UNAUTHORIZED, Origin.MCP},
        )

    warn.assert_not_called()
    stored_data = create.call_args.kwargs["data"]
    assert set(stored_data["tags"]) == {"auth_thirdparty_unauthorized", "mcp"}
    assert len(sink.records) == 1
    _name, forwarded, _actor = sink.records[0]
    assert set(forwarded) == {ErrorSource.AUTH_THIRDPARTY_UNAUTHORIZED, Origin.MCP}


@pytest.mark.asyncio
async def test_execution_completed_carries_origin_tag() -> None:
    """EXECUTION_COMPLETED accepts the Origin tag."""
    sink = _RecordingSink(enabled=True)
    create = _fake_create()

    with (
        patch("jentic_one.shared.events.EventRepository.create", create),
        patch("jentic_one.shared.events.get_active_sink", return_value=sink),
    ):
        await emit_event(
            session=AsyncMock(),
            type=EventType.EXECUTION_COMPLETED,
            severity=EventSeverity.INFO,
            summary="execution completed",
            created_by="agt_1",
            tags={Origin.MCP},
        )

    assert create.call_args.kwargs["data"]["tags"] == ["mcp"]
    assert sink.records == [(TelemetryEventName.BROKER_EXECUTION, (Origin.MCP,), None)]


@pytest.mark.asyncio
async def test_origin_tag_dropped_on_event_that_does_not_allow_it() -> None:
    """event_tag_dropped fallback: Origin on IMPORT_COMPLETED is discarded, event emits."""
    sink = _RecordingSink(enabled=True)
    create = _fake_create()

    with (
        patch("jentic_one.shared.events.EventRepository.create", create),
        patch("jentic_one.shared.events.get_active_sink", return_value=sink),
        patch("jentic_one.shared.events.logger.warning") as warn,
    ):
        await emit_event(
            session=AsyncMock(),
            type=EventType.IMPORT_COMPLETED,
            severity=EventSeverity.INFO,
            summary="import completed",
            created_by="usr_1",
            tags={Origin.MCP, SpecSource.CATALOG},
        )

    warn.assert_called_once()
    # Only the allowed SpecSource tag survives; the event still emits + forwards.
    assert create.call_args.kwargs["data"]["tags"] == ["catalog"]
    assert sink.records == [(TelemetryEventName.SPEC_IMPORTED, (SpecSource.CATALOG,), None)]


@pytest.mark.asyncio
async def test_mcp_session_started_forwards_only_closed_client_tag() -> None:
    """mcp_session_started telemetry carries the McpClient tag; data stays internal."""
    sink = _RecordingSink(enabled=True)
    create = _fake_create()

    with (
        patch("jentic_one.shared.events.EventRepository.create", create),
        patch("jentic_one.shared.events.get_active_sink", return_value=sink),
    ):
        await emit_event(
            session=AsyncMock(),
            type=EventType.MCP_SESSION_STARTED,
            severity=EventSeverity.INFO,
            summary="MCP session started",
            created_by="agt_1",
            actor_id="agt_1",
            actor_type="agent",
            data={"session_id": "sess-1", "client_name": "Cursor IDE"},
            tags={McpClient.CURSOR},
        )

    assert sink.records == [(TelemetryEventName.MCP_SESSION_STARTED, (McpClient.CURSOR,), "agent")]
    # The full-fidelity clientInfo stays on the internal event's data.
    stored_data = create.call_args.kwargs["data"]
    assert stored_data["client_name"] == "Cursor IDE"
    assert stored_data["tags"] == ["cursor"]


@pytest.mark.asyncio
async def test_mcp_config_registered_forwards_runtime_tag() -> None:
    sink = _RecordingSink(enabled=True)

    with (
        patch("jentic_one.shared.events.EventRepository.create", _fake_create()),
        patch("jentic_one.shared.events.get_active_sink", return_value=sink),
    ):
        await emit_event(
            session=AsyncMock(),
            type=EventType.MCP_CONFIG_REGISTERED,
            severity=EventSeverity.INFO,
            summary="MCP config registered",
            created_by="usr_1",
            tags={McpConfigRuntime.CLAUDE_DESKTOP},
        )

    assert sink.records == [
        (
            TelemetryEventName.MCP_CONFIG_REGISTERED,
            (McpConfigRuntime.CLAUDE_DESKTOP,),
            None,
        )
    ]


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
