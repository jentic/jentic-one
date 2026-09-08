"""Tests for the telemetry-event extension seam.

Covers ``register_telemetry_event`` + ``resolve_wire_name`` — the seam that lets
a downstream package forward extra events without editing the closed
``TelemetryEventName`` enum:

- wire-name syntax validation (``lower_snake_case``),
- collision rejection against the built-in enum and the built-in event map,
- idempotency for the same pair, conflict on a re-register,
- ``resolve_wire_name`` precedence (built-ins first, then runtime registry,
  ``None`` for internal-only events).

Each test snapshots/restores the process-global runtime registry so a
registration never leaks into another test.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from jentic_one.shared.telemetry import events as events_mod
from jentic_one.shared.telemetry.events import (
    TELEMETRY_EVENTS,
    TelemetryEventName,
    register_telemetry_event,
    resolve_wire_name,
)


@pytest.fixture(autouse=True)
def _isolate_runtime_registry() -> Iterator[None]:
    snapshot = dict(events_mod._RUNTIME_TELEMETRY_EVENTS)
    try:
        yield
    finally:
        events_mod._RUNTIME_TELEMETRY_EVENTS.clear()
        events_mod._RUNTIME_TELEMETRY_EVENTS.update(snapshot)


def test_register_and_resolve_runtime_event() -> None:
    register_telemetry_event("my_ext.thing_happened", "thing_happened")
    assert resolve_wire_name("my_ext.thing_happened") == "thing_happened"


def test_resolve_wire_name_prefers_builtin() -> None:
    """A built-in EventType resolves via the closed map, never the runtime one."""
    # PBAC_DENIED is a built-in mapping.
    key = next(iter(TELEMETRY_EVENTS))
    assert resolve_wire_name(key) == TELEMETRY_EVENTS[key].value


def test_resolve_wire_name_none_for_internal_only() -> None:
    assert resolve_wire_name("some.internal_only_event") is None


@pytest.mark.parametrize(
    "bad_wire_name",
    ["CamelCase", "with-dash", "1leading_digit", "has space", ""],
)
def test_register_rejects_malformed_wire_name(bad_wire_name: str) -> None:
    with pytest.raises(ValueError, match="Invalid telemetry wire name"):
        register_telemetry_event("my_ext.evt", bad_wire_name)


def test_register_rejects_builtin_event_type_key() -> None:
    builtin_key = next(iter(TELEMETRY_EVENTS))
    with pytest.raises(ValueError, match="already a built-in telemetry event"):
        register_telemetry_event(builtin_key, "some_new_wire_name")


def test_register_rejects_wire_name_colliding_with_builtin_enum() -> None:
    builtin_wire = TelemetryEventName.BROKER_EXECUTION.value
    with pytest.raises(ValueError, match="collides with a built-in event"):
        register_telemetry_event("my_ext.custom", builtin_wire)


def test_register_is_idempotent_for_same_pair() -> None:
    register_telemetry_event("my_ext.evt", "custom_event")
    register_telemetry_event("my_ext.evt", "custom_event")  # no raise
    assert resolve_wire_name("my_ext.evt") == "custom_event"


def test_register_rejects_conflicting_reregister() -> None:
    register_telemetry_event("my_ext.evt", "custom_event")
    with pytest.raises(ValueError, match="already registered"):
        register_telemetry_event("my_ext.evt", "different_wire_name")
