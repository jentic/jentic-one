"""Unit tests for ``EVENT_TYPE_SEVERITIES`` — the severity-classification matrix.

Issue #907: the audit that produced this map found every individual
``severity=`` assignment internally consistent, but the boundary between ERROR
and CRITICAL was undocumented anywhere a developer or operator could find it.
These tests turn the documented boundary into an enforced one — mirroring the
existing drift guard for ``EVENT_TAGS``
(``test_event_types.py::test_all_contains_every_class_constant`` and
``test_event_tags_values_are_tuples_of_enums``). See
``docs/operations/monitoring.md`` for the operator-facing version of the table.
"""

from __future__ import annotations

from jentic_one.shared.models.events import (
    EVENT_TYPE_SEVERITIES,
    EventSeverity,
    EventType,
)


def test_every_event_type_has_a_severity_entry() -> None:
    """No ``EventType`` constant may be undocumented in the severity matrix.

    A new event type added to ``EventType.ALL`` without a matching entry here
    means the author never made (or recorded) a severity decision for it.
    """
    assert set(EVENT_TYPE_SEVERITIES) == EventType.ALL


def test_every_entry_is_a_nonempty_frozenset_of_severities() -> None:
    for event_type, allowed in EVENT_TYPE_SEVERITIES.items():
        assert isinstance(allowed, frozenset), event_type
        assert allowed, f"{event_type} maps an empty set — remove the entry instead"
        assert all(isinstance(s, EventSeverity) for s in allowed), event_type


def test_critical_is_reserved_to_repeated_failure() -> None:
    """CRITICAL must stay rare-by-design: only one type may ever emit it.

    If a future emitter starts using CRITICAL, this test forces a deliberate,
    reviewable decision (widen this assertion + the matrix + the docs table)
    instead of a silent severity change diluting the signal the
    repeated-failure escalation relies on.
    """
    critical_types = {t for t, s in EVENT_TYPE_SEVERITIES.items() if EventSeverity.CRITICAL in s}
    assert critical_types == {EventType.EXECUTION_REPEATED_FAILURE}


def test_repeated_failure_allows_error_and_critical() -> None:
    assert EVENT_TYPE_SEVERITIES[EventType.EXECUTION_REPEATED_FAILURE] == frozenset(
        {EventSeverity.ERROR, EventSeverity.CRITICAL}
    )


def test_every_other_type_is_a_fixed_single_severity() -> None:
    """Every type except the one escalating type maps to exactly one severity."""
    multi_severity = {t for t, s in EVENT_TYPE_SEVERITIES.items() if len(s) > 1}
    assert multi_severity == {EventType.EXECUTION_REPEATED_FAILURE}
