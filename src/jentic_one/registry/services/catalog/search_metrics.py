"""Catalog search telemetry: ranking-strategy failures on ``GET /catalog?q=``.

Goes through the sanctioned facade in ``shared/metrics.py`` (``get_meter``).
The counter is created lazily on first use so importing this module has no
side effects. Attributes are bounded: ``strategy`` is the configured strategy
name (or ``unknown`` for a client-supplied cursor name), ``reason`` and
``outcome`` are closed sets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from jentic_one.shared.metrics import get_meter

if TYPE_CHECKING:
    from opentelemetry.metrics import Counter

_METER_NAME = "jentic_one.catalog_search"

#: Why a strategy call did not produce a ranking: it timed out, raised, isn't
#: registered in this process, returned rows none of which were valid, or (a
#: later page) its cursor names a strategy other than the configured one.
FailureReason = Literal["timeout", "error", "unregistered", "invalid", "not_configured"]
#: What the service did about it: served the walk with ``token`` (first page),
#: answered 503 (a later page), or rejected the cursor as invalid (a later page
#: whose cursor names a strategy that isn't the configured one).
FailureOutcome = Literal["fallback", "unavailable", "rejected"]

_failure_counter: Counter | None = None


def record_strategy_failure(
    strategy: str, *, reason: FailureReason, outcome: FailureOutcome
) -> None:
    """A catalog search strategy failed; the service fell back or answered 503."""
    global _failure_counter
    if _failure_counter is None:
        _failure_counter = get_meter(_METER_NAME).create_counter(
            "jentic_one.catalog_search.strategy_failures",
            description=(
                "Catalog search ranking calls that failed, by strategy, reason and outcome."
            ),
        )
    _failure_counter.add(1, {"strategy": strategy, "reason": reason, "outcome": outcome})
