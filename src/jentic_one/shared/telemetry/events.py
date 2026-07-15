"""Telemetry event names — the canonical, code-defined allowlist.

``TelemetryEventName`` is the **only** set of events an opted-in instance may
send to Jentic. It is fixed in code (never config- or DB-tunable): consent is
all-or-nothing, so the ingest side always knows the full event vocabulary and
funnels are never silently skewed by suppressed events.

``TELEMETRY_EVENTS`` maps the internal ``EventType`` taxonomy to the wire name.
An ``EventType`` absent from this map is internal-only and never forwarded —
``emit_event`` consults this map to decide what (if anything) to hand the sink.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.models.events import EventTag, EventType


class TelemetryEventName(StrEnum):
    """Every event name the client may put on the wire (the allowlist)."""

    # Lifecycle / liveness
    INSTANCE_INITIALIZED = "instance_initialized"
    INSTANCE_BOOTED = "instance_booted"
    # Setup / activation funnel
    SPEC_IMPORTED = "spec_imported"
    CREDENTIAL_STORED = "credential_stored"
    CREDENTIAL_CONNECTED = "credential_connected"
    TOOLKIT_CREATED = "toolkit_created"
    TOOLKIT_KEY_CREATED = "toolkit_key_created"
    TOOLKIT_PERMISSION_RULE_SET = "toolkit_permission_rule_set"
    CREDENTIAL_BOUND_TO_TOOLKIT = "credential_bound_to_toolkit"
    CREDENTIAL_UNBOUND_FROM_TOOLKIT = "credential_unbound_from_toolkit"
    AGENT_CREATED = "agent_created"
    AGENT_SELF_REGISTERED = "agent_self_registered"
    AGENT_REGISTRATION_APPROVED = "agent_registration_approved"
    AGENT_REGISTRATION_DENIED = "agent_registration_denied"
    TOOLKIT_BOUND_TO_AGENT = "toolkit_bound_to_agent"
    TOOLKIT_UNBOUND_FROM_AGENT = "toolkit_unbound_from_agent"
    # Access-request flow
    ACCESS_REQUEST_FILED = "access_request_filed"
    ACCESS_REQUEST_APPROVED = "access_request_approved"
    ACCESS_REQUEST_DENIED = "access_request_denied"
    # Usage / activation moment
    BROKER_EXECUTION = "broker_execution"
    # Health / friction
    PBAC_DENIED = "pbac_denied"
    BROKER_EXECUTION_FAILED = "broker_execution_failed"
    CREDENTIAL_NOT_PROVISIONED = "credential_not_provisioned"
    SPEC_IMPORT_FAILED = "spec_import_failed"
    CREDENTIAL_CONNECTION_FAILED = "credential_connection_failed"
    CREDENTIAL_REFRESH_FAILED = "credential_refresh_failed"


#: Allowlist: internal ``EventType`` → wire ``TelemetryEventName``. Only events
#: present here are forwarded to Jentic (when telemetry is enabled). ``emit_event``
#: looks the event up here; a miss means "internal-only, do not forward".
TELEMETRY_EVENTS: dict[str, TelemetryEventName] = {
    EventType.INSTANCE_INITIALIZED: TelemetryEventName.INSTANCE_INITIALIZED,
    EventType.INSTANCE_BOOTED: TelemetryEventName.INSTANCE_BOOTED,
    EventType.IMPORT_COMPLETED: TelemetryEventName.SPEC_IMPORTED,
    EventType.IMPORT_FAILED: TelemetryEventName.SPEC_IMPORT_FAILED,
    EventType.CREDENTIAL_STORED: TelemetryEventName.CREDENTIAL_STORED,
    EventType.CREDENTIAL_CONNECTED: TelemetryEventName.CREDENTIAL_CONNECTED,
    EventType.CREDENTIAL_CONNECTION_FAILED: TelemetryEventName.CREDENTIAL_CONNECTION_FAILED,
    EventType.CREDENTIAL_REFRESH_FAILED: TelemetryEventName.CREDENTIAL_REFRESH_FAILED,
    EventType.CREDENTIAL_NOT_PROVISIONED: TelemetryEventName.CREDENTIAL_NOT_PROVISIONED,
    EventType.CREDENTIAL_BOUND_TO_TOOLKIT: TelemetryEventName.CREDENTIAL_BOUND_TO_TOOLKIT,
    EventType.CREDENTIAL_UNBOUND_FROM_TOOLKIT: TelemetryEventName.CREDENTIAL_UNBOUND_FROM_TOOLKIT,
    EventType.TOOLKIT_CREATED: TelemetryEventName.TOOLKIT_CREATED,
    EventType.TOOLKIT_KEY_CREATED: TelemetryEventName.TOOLKIT_KEY_CREATED,
    EventType.TOOLKIT_PERMISSION_RULE_SET: TelemetryEventName.TOOLKIT_PERMISSION_RULE_SET,
    EventType.TOOLKIT_BOUND_TO_AGENT: TelemetryEventName.TOOLKIT_BOUND_TO_AGENT,
    EventType.TOOLKIT_UNBOUND_FROM_AGENT: TelemetryEventName.TOOLKIT_UNBOUND_FROM_AGENT,
    EventType.AGENT_CREATED: TelemetryEventName.AGENT_CREATED,
    EventType.AGENT_SELF_REGISTERED: TelemetryEventName.AGENT_SELF_REGISTERED,
    EventType.AGENT_REGISTRATION_APPROVED: TelemetryEventName.AGENT_REGISTRATION_APPROVED,
    EventType.AGENT_REGISTRATION_DENIED: TelemetryEventName.AGENT_REGISTRATION_DENIED,
    EventType.ACCESS_REQUEST_FILED: TelemetryEventName.ACCESS_REQUEST_FILED,
    EventType.ACCESS_REQUEST_APPROVED: TelemetryEventName.ACCESS_REQUEST_APPROVED,
    EventType.ACCESS_REQUEST_DENIED: TelemetryEventName.ACCESS_REQUEST_DENIED,
    EventType.EXECUTION_COMPLETED: TelemetryEventName.BROKER_EXECUTION,
    EventType.EXECUTION_FAILED: TelemetryEventName.BROKER_EXECUTION_FAILED,
    EventType.PBAC_DENIED: TelemetryEventName.PBAC_DENIED,
}


# --- Runtime-registered events -----------------------------------------------
# Built-in events stay in the closed TelemetryEventName enum + TELEMETRY_EVENTS
# map above. A downstream package can register extra (internal EventType value ->
# wire name) pairs here at import time. Wire names are validated plain strings;
# they never enter the closed enum, so strict typing of the built-in set is
# unaffected. The emit path resolves via resolve_wire_name(), which consults the
# built-in map first, then this registry.
_RUNTIME_TELEMETRY_EVENTS: dict[str, str] = {}

# Wire-name syntax the ingest side accepts: lower_snake_case, matching the enum.
_WIRE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def register_telemetry_event(event_type_key: str, wire_name: str) -> None:
    """Register an extra event: internal EventType value -> wire name.

    Rejects collisions with the built-in enum/map and malformed wire names so a
    typo can't silently disable forwarding. Idempotent for the same pair. Call at
    import time (e.g. in a registering package's __init__).
    """
    if not _WIRE_NAME_RE.match(wire_name):
        raise ValueError(f"Invalid telemetry wire name {wire_name!r}")
    if event_type_key in TELEMETRY_EVENTS:
        raise ValueError(f"{event_type_key!r} is already a built-in telemetry event")
    if wire_name in {e.value for e in TelemetryEventName}:
        raise ValueError(f"Wire name {wire_name!r} collides with a built-in event")
    existing = _RUNTIME_TELEMETRY_EVENTS.get(event_type_key)
    if existing is not None and existing != wire_name:
        raise ValueError(f"{event_type_key!r} already registered to {existing!r}")
    _RUNTIME_TELEMETRY_EVENTS[event_type_key] = wire_name


def resolve_wire_name(event_type_key: str) -> str | None:
    """Resolve an internal EventType value to a wire name, built-ins first.

    Returns None when the event is internal-only (not forwarded). This is the
    single lookup the emit path uses instead of indexing TELEMETRY_EVENTS
    directly, so built-in and registered events flow through one chokepoint.
    """
    builtin = TELEMETRY_EVENTS.get(event_type_key)
    if builtin is not None:
        return builtin.value
    return _RUNTIME_TELEMETRY_EVENTS.get(event_type_key)


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    """A single queued telemetry event.

    Carries **only** the wire name, the validated closed-enum tags, the actor
    type, and a record timestamp — there is deliberately nowhere to put
    free-form props, URLs, identities, or secrets. The opaque instance id and
    app version are stamped on at flush time (request-level fields), not per
    event.

    ``actor_type`` is typed as the closed ``ActorType`` enum (never a free-form
    ``str``): the sink coerces the caller's value at record time and drops
    anything that is not an enum member, so a raw label/email can never reach
    the wire. This makes the "PII is structurally impossible" guarantee real for
    ``actor_type`` rather than by-convention.
    """

    name: TelemetryEventName | str
    tags: tuple[EventTag, ...]
    ts: datetime
    actor_type: ActorType | None = None
