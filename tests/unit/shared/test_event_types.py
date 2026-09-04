"""Unit tests for EventType constants and the closed-enum event tags."""

from unittest.mock import patch

import pytest

from jentic_one.shared.models.actors import Origin
from jentic_one.shared.models.events import (
    EVENT_TAGS,
    ErrorSource,
    EventType,
    HostOs,
    McpClient,
    McpConfigRuntime,
)
from jentic_one.shared.telemetry.events import TELEMETRY_EVENTS, TelemetryEventName


def test_new_event_types_in_all() -> None:
    """All three new event types must appear in EventType.ALL."""
    assert EventType.UPSTREAM_CIRCUIT_OPEN in EventType.ALL
    assert EventType.JOB_FAILED_PERMANENTLY in EventType.ALL
    assert EventType.UNAUTHORIZED_ACCESS_ATTEMPT in EventType.ALL


def test_mcp_event_types_in_all() -> None:
    """The lane-D MCP event types are registered in EventType.ALL."""
    assert EventType.MCP_SESSION_STARTED in EventType.ALL
    assert EventType.MCP_CONFIG_REGISTERED in EventType.ALL


def test_event_type_values() -> None:
    """Event type string values match the namespaced convention."""
    assert EventType.UPSTREAM_CIRCUIT_OPEN == "upstream.circuit_open"
    assert EventType.JOB_FAILED_PERMANENTLY == "job.failed_permanently"
    assert EventType.UNAUTHORIZED_ACCESS_ATTEMPT == "security.unauthorized_access_attempt"
    assert EventType.MCP_SESSION_STARTED == "mcp.session_started"
    assert EventType.MCP_CONFIG_REGISTERED == "mcp.config_registered"


def test_mcp_events_on_telemetry_allowlist() -> None:
    """The MCP events forward to the telemetry wire under their snake_case names."""
    assert TELEMETRY_EVENTS[EventType.MCP_SESSION_STARTED] is TelemetryEventName.MCP_SESSION_STARTED
    assert (
        TELEMETRY_EVENTS[EventType.MCP_CONFIG_REGISTERED]
        is TelemetryEventName.MCP_CONFIG_REGISTERED
    )
    assert TelemetryEventName.MCP_SESSION_STARTED.value == "mcp_session_started"
    assert TelemetryEventName.MCP_CONFIG_REGISTERED.value == "mcp_config_registered"


def test_oauth_client_event_types_registered() -> None:
    """The OAuth-client lifecycle events are in ALL."""
    assert EventType.OAUTH_CLIENT_REGISTERED == "oauth_client.registered"
    assert EventType.OAUTH_CLIENT_APPROVED == "oauth_client.approved"
    assert EventType.OAUTH_CLIENT_REGISTERED in EventType.ALL
    assert EventType.OAUTH_CLIENT_APPROVED in EventType.ALL


def test_oauth_client_events_are_internal_only() -> None:
    """The design assigns no telemetry wire names — these never leave the box,
    and they carry no closed-enum tags."""
    assert EventType.OAUTH_CLIENT_REGISTERED not in TELEMETRY_EVENTS
    assert EventType.OAUTH_CLIENT_APPROVED not in TELEMETRY_EVENTS
    assert EventType.OAUTH_CLIENT_REGISTERED not in EVENT_TAGS
    assert EventType.OAUTH_CLIENT_APPROVED not in EVENT_TAGS


def test_all_contains_every_class_constant() -> None:
    """Every string constant on EventType must be in ALL (no drift)."""
    constants = {v for k, v in vars(EventType).items() if not k.startswith("_") and k != "ALL"}
    assert constants == EventType.ALL


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ("Linux", HostOs.LINUX),
        ("Darwin", HostOs.DARWIN),
        ("Windows", HostOs.WINDOWS),
        # Anything outside the closed set collapses to OTHER — a free-form
        # platform string can never become a tag value.
        ("FreeBSD", HostOs.OTHER),
        ("Java", HostOs.OTHER),
        ("", HostOs.OTHER),
    ],
)
def test_host_os_current_classifies_into_closed_set(system: str, expected: HostOs) -> None:
    """platform.system() output maps onto the closed enum, unknowns to OTHER."""
    with patch("jentic_one.shared.models.events.platform.system", return_value=system):
        assert HostOs.current() is expected


def test_host_os_current_on_this_machine_is_a_member() -> None:
    """Unpatched, current() always yields a real enum member (never raises)."""
    assert HostOs.current() in set(HostOs)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        # Install-time CLI stamp (runtime.GOOS values) wins over runtime detection.
        ("linux", HostOs.LINUX),
        ("darwin", HostOs.DARWIN),
        ("windows", HostOs.WINDOWS),
        ("Darwin", HostOs.DARWIN),
        # A stamped-but-unknown value degrades to OTHER — never silently falls
        # back to the container's OS.
        ("freebsd", HostOs.OTHER),
        ("garbage!!", HostOs.OTHER),
        # Surrounding whitespace (quoted hand-edits like ' darwin ') is forgiven.
        (" darwin ", HostOs.DARWIN),
        ("linux\n", HostOs.LINUX),
    ],
)
def test_host_os_resolve_prefers_config_value(configured: str, expected: HostOs) -> None:
    """resolve() uses the install-time config stamp when present."""
    with patch("jentic_one.shared.models.events.platform.system", return_value="Linux"):
        assert HostOs.resolve(configured) is expected


@pytest.mark.parametrize("absent", [None, "", "   ", "\t\n"])
def test_host_os_resolve_falls_back_to_runtime_detection(absent: str | None) -> None:
    """Hand-rolled configs without a stamp fall back to platform.system()."""
    with patch("jentic_one.shared.models.events.platform.system", return_value="Darwin"):
        assert HostOs.resolve(absent) is HostOs.DARWIN


def test_host_os_allowed_only_on_instance_booted() -> None:
    """HostOs is the tag type for the per-boot instance_booted event only."""
    assert EVENT_TAGS[EventType.INSTANCE_BOOTED] == (HostOs,)
    others = {k: v for k, v in EVENT_TAGS.items() if k != EventType.INSTANCE_BOOTED}
    assert all(HostOs not in allowed for allowed in others.values())


def test_execution_events_carry_origin_tag_type() -> None:
    """The adoption split: both execution lifecycle events accept Origin tags.

    EXECUTION_FAILED keeps its ErrorSource split alongside — the tuple widening
    is exactly what lets one event carry both closed enums.
    """
    assert EVENT_TAGS[EventType.EXECUTION_COMPLETED] == (Origin,)
    assert EVENT_TAGS[EventType.EXECUTION_FAILED] == (ErrorSource, Origin)


def test_event_tags_values_are_tuples_of_enums() -> None:
    """Every EVENT_TAGS value is a tuple of StrEnum types (isinstance-compatible)."""
    for event_type, allowed in EVENT_TAGS.items():
        assert isinstance(allowed, tuple), event_type
        assert allowed, f"{event_type} maps an empty tuple — remove the entry instead"
        assert all(isinstance(t, type) for t in allowed), event_type


def test_mcp_session_started_carries_only_mcp_client_tag() -> None:
    """The telemetry side of mcp_session_started is at most the closed McpClient tag."""
    assert EVENT_TAGS[EventType.MCP_SESSION_STARTED] == (McpClient,)


def test_mcp_config_registered_carries_only_runtime_tag() -> None:
    assert EVENT_TAGS[EventType.MCP_CONFIG_REGISTERED] == (McpConfigRuntime,)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("claude", McpClient.CLAUDE),
        ("Claude Desktop", McpClient.CLAUDE),
        ("claude-code", McpClient.CLAUDE),
        ("cursor", McpClient.CURSOR),
        ("Cursor IDE", McpClient.CURSOR),
        ("codex", McpClient.CODEX),
        ("Codex CLI", McpClient.CODEX),
        # Unknowns and absent clientInfo collapse to OTHER — a raw client name
        # can never become a tag value.
        ("windsurf", McpClient.OTHER),
        ("", McpClient.OTHER),
        (None, McpClient.OTHER),
    ],
)
def test_mcp_client_classifies_into_closed_set(name: str | None, expected: McpClient) -> None:
    assert McpClient.from_client_name(name) is expected


def test_mcp_config_runtime_members() -> None:
    """The runtime tag set matches the runtimes auto-registration writes (2-E3)."""
    assert {m.value for m in McpConfigRuntime} == {
        "claude_desktop",
        "claude_code",
        "cursor",
        "codex",
        "other",
    }
