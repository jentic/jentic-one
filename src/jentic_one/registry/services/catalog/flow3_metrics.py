"""Flow-3 (catalog-update / overlay reconciliation) telemetry counters — L6 (#924).

Minimal, always-on OpenTelemetry counters so operators can tell whether the update
loop actually *works*: how often the badge is raised (emit), resolved by re-import
(settle), quieted (snooze), and — for the conflict half — how often an authorized
re-import auto-deprecates an overlay. Metrics go through the sanctioned facade in
``shared/metrics.py`` (``get_meter``); never import the OTel/Prometheus exporters
directly (arch-test enforced).

Counters are created lazily on first use so importing this module has no side
effects and the tests that reset the meter provider still see fresh instruments.
All increments are wrapped best-effort by the callers (telemetry must never fail a
request or a job), but incrementing a counter is itself non-throwing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jentic_one.shared.metrics import get_meter

if TYPE_CHECKING:
    from opentelemetry.metrics import Counter

_METER_NAME = "jentic_one.flow3"

# Counter instances, created once on first increment. Module-level cache (not a
# per-call get_meter) keeps the instrument identity stable across increments.
_emit_counter: Counter | None = None
_settle_counter: Counter | None = None
_snooze_counter: Counter | None = None
_reimport_counter: Counter | None = None
_deprecate_counter: Counter | None = None


def record_update_notified(event_class: str) -> None:
    """A Flow-3 update notification was emitted (badge raised). ``event_class`` =
    ``catalog.update_available`` | ``catalog.update_conflicts_overlay``."""
    global _emit_counter
    if _emit_counter is None:
        _emit_counter = get_meter(_METER_NAME).create_counter(
            "jentic_one.flow3.update_notified",
            description="Catalog upstream-change notifications emitted by the update sweep.",
        )
    _emit_counter.add(1, {"event_class": event_class})


def record_update_settled(count: int = 1) -> None:
    """``count`` outstanding Flow-3 notifications were settled (resolved by re-import)."""
    global _settle_counter
    if count <= 0:
        return
    if _settle_counter is None:
        _settle_counter = get_meter(_METER_NAME).create_counter(
            "jentic_one.flow3.update_settled",
            description="Flow-3 update notifications settled by an adopting re-import.",
        )
    _settle_counter.add(count)


def record_update_snoozed() -> None:
    """An operator snoozed/muted a Flow-3 update notification (C1)."""
    global _snooze_counter
    if _snooze_counter is None:
        _snooze_counter = get_meter(_METER_NAME).create_counter(
            "jentic_one.flow3.update_snoozed",
            description="Flow-3 update notifications snoozed/muted by an operator.",
        )
    _snooze_counter.add(1)


def record_reimport_from_catalog() -> None:
    """A catalog re-import was enqueued to adopt an upstream change (badge → re-imported)."""
    global _reimport_counter
    if _reimport_counter is None:
        _reimport_counter = get_meter(_METER_NAME).create_counter(
            "jentic_one.flow3.reimport_enqueued",
            description="Catalog re-imports enqueued to adopt an upstream change.",
        )
    _reimport_counter.add(1)


def record_overlay_auto_deprecated() -> None:
    """An authorized re-import auto-deprecated a live confirmed overlay (A4b/L2)."""
    global _deprecate_counter
    if _deprecate_counter is None:
        _deprecate_counter = get_meter(_METER_NAME).create_counter(
            "jentic_one.flow3.overlay_auto_deprecated",
            description="Confirmed overlays auto-deprecated by an authorized catalog re-import.",
        )
    _deprecate_counter.add(1)
