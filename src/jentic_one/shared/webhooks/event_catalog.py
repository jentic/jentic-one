"""The canonical set of subscribable webhook event types (backend source of truth).

An endpoint subscribes to event types by name. The set it may choose from is
exactly ``EventType.ALL`` minus the never-relayed denylist
(``fanout.NEVER_RELAYED``) — the same denylist fan-out enforces, so the picker
can never offer a type that would be silently withheld at relay time. The
synthetic ``webhook.test`` type is **not** in ``EventType.ALL`` and so is
correctly excluded here too; it is a wiring probe, not a subscribable type.

Served to the UI via ``GET /webhooks/event-catalog`` so the frontend picker
cannot drift from the backend. A drift test on both sides pins the two lists
together.
"""

from __future__ import annotations

from jentic_one.admin.services.webhooks.fanout import NEVER_RELAYED
from jentic_one.shared.models.events import EventType


def subscribable_event_types() -> frozenset[str]:
    """The set of event types an endpoint may subscribe to."""
    return EventType.ALL - NEVER_RELAYED


def subscribable_event_catalog() -> list[str]:
    """The subscribable event types, sorted for a stable, deterministic order."""
    return sorted(subscribable_event_types())
