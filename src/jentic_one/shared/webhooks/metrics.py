"""Outbound-webhook pipeline telemetry — counters + histograms.

Minimal, always-on OpenTelemetry instruments so operators can see whether the
notification pipeline is healthy: how many deliveries succeed/fail/dead-letter,
how long a send takes, how far the relay lags behind wall-clock, and the two
events that warrant an alert (a dead-letter, and a ``410`` that deactivates an
endpoint). Everything goes through the sanctioned facade in
``shared/metrics.py`` (``get_meter``); the OTel/Prometheus exporters are never
imported directly (arch-test enforced).

Instruments are created lazily on first use so importing this module has no side
effects and the tests that reset the meter provider still see fresh instruments.
All record helpers are non-throwing so telemetry can never break a tick.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jentic_one.shared.metrics import get_meter

if TYPE_CHECKING:
    from opentelemetry.metrics import Counter, Histogram

_METER_NAME = "jentic_one.webhooks"

# Module-level instrument cache (not a per-call get_meter) so instrument identity
# stays stable across records.
_deliveries_counter: Counter | None = None
_dead_letter_counter: Counter | None = None
_deactivated_counter: Counter | None = None
_send_duration_hist: Histogram | None = None
_relay_lag_hist: Histogram | None = None


def record_delivery(status: str) -> None:
    """One delivery attempt reached a terminal-for-this-tick outcome.

    ``status`` is the low-cardinality result bucket: ``succeeded`` | ``failed``
    (will retry) | ``dead`` (dead-lettered) | ``deactivated`` (410 Gone).
    """
    global _deliveries_counter
    if _deliveries_counter is None:
        _deliveries_counter = get_meter(_METER_NAME).create_counter(
            "jentic_one.webhooks.deliveries_total",
            description="Outbound webhook delivery outcomes, labelled by status.",
        )
    _deliveries_counter.add(1, {"status": status})


def record_dead_letter() -> None:
    """A delivery exhausted its attempts and was dead-lettered — alerting hook."""
    global _dead_letter_counter
    if _dead_letter_counter is None:
        _dead_letter_counter = get_meter(_METER_NAME).create_counter(
            "jentic_one.webhooks.dead_letter_total",
            description="Webhook deliveries dead-lettered after exhausting attempts.",
        )
    _dead_letter_counter.add(1)


def record_endpoint_deactivated(reason: str) -> None:
    """An endpoint was deactivated (e.g. ``410`` Gone) — alerting hook."""
    global _deactivated_counter
    if _deactivated_counter is None:
        _deactivated_counter = get_meter(_METER_NAME).create_counter(
            "jentic_one.webhooks.endpoint_deactivated_total",
            description="Webhook endpoints auto-deactivated by the dispatcher.",
        )
    _deactivated_counter.add(1, {"reason": reason})


def record_send_duration(seconds: float, *, outcome: str) -> None:
    """Wall-clock duration of one outbound send attempt (seconds)."""
    global _send_duration_hist
    if _send_duration_hist is None:
        _send_duration_hist = get_meter(_METER_NAME).create_histogram(
            "jentic_one.webhooks.send_duration_seconds",
            unit="s",
            description="Duration of a single outbound webhook send attempt.",
        )
    _send_duration_hist.record(max(0.0, seconds), {"outcome": outcome})


def record_relay_lag(seconds: float) -> None:
    """Age of the newest event relayed this tick, i.e. relay lag (seconds).

    A rising value means the relay is falling behind event production. Recorded
    only when a batch was actually relayed.
    """
    global _relay_lag_hist
    if _relay_lag_hist is None:
        _relay_lag_hist = get_meter(_METER_NAME).create_histogram(
            "jentic_one.webhooks.relay_lag_seconds",
            unit="s",
            description="Lag between an event's creation and its relay to the outbound queue.",
        )
    _relay_lag_hist.record(max(0.0, seconds))
