"""Unit tests for EventType constants and the HostOs closed-enum tag."""

from unittest.mock import patch

import pytest

from jentic_one.shared.models.events import EVENT_TAGS, EventType, HostOs


def test_new_event_types_in_all() -> None:
    """All three new event types must appear in EventType.ALL."""
    assert EventType.UPSTREAM_CIRCUIT_OPEN in EventType.ALL
    assert EventType.JOB_FAILED_PERMANENTLY in EventType.ALL
    assert EventType.UNAUTHORIZED_ACCESS_ATTEMPT in EventType.ALL


def test_event_type_values() -> None:
    """Event type string values match the namespaced convention."""
    assert EventType.UPSTREAM_CIRCUIT_OPEN == "upstream.circuit_open"
    assert EventType.JOB_FAILED_PERMANENTLY == "job.failed_permanently"
    assert EventType.UNAUTHORIZED_ACCESS_ATTEMPT == "security.unauthorized_access_attempt"


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
    ],
)
def test_host_os_resolve_prefers_config_value(configured: str, expected: HostOs) -> None:
    """resolve() uses the install-time config stamp when present."""
    with patch("jentic_one.shared.models.events.platform.system", return_value="Linux"):
        assert HostOs.resolve(configured) is expected


@pytest.mark.parametrize("absent", [None, ""])
def test_host_os_resolve_falls_back_to_runtime_detection(absent: str | None) -> None:
    """Hand-rolled configs without a stamp fall back to platform.system()."""
    with patch("jentic_one.shared.models.events.platform.system", return_value="Darwin"):
        assert HostOs.resolve(absent) is HostOs.DARWIN


def test_host_os_allowed_only_on_instance_initialized() -> None:
    """HostOs is the tag type for the one-time instance_initialized event only."""
    assert EVENT_TAGS[EventType.INSTANCE_INITIALIZED] is HostOs
    others = {k: v for k, v in EVENT_TAGS.items() if k != EventType.INSTANCE_INITIALIZED}
    assert HostOs not in others.values()
