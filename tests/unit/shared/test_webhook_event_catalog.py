"""Unit tests for the subscribable webhook event catalog (backend source of truth).

The catalog served to the UI (``GET /webhooks/event-catalog``) is
``EventType.ALL`` minus the never-relayed denylist (``fanout.NEVER_RELAYED``).
These specs are the **backend half of the catalog drift pin**: they assert the
computed set is exactly what the curated frontend list mirrors, so a type added,
renamed, or newly withheld on the backend fails here (and the sibling UI test
``ui/src/modules/webhooks/__tests__/eventCatalog.test.ts`` fails too), forcing the
two lists to move in lockstep. Together they make silent drift impossible.
"""

from __future__ import annotations

from jentic_one.admin.services.webhooks.fanout import NEVER_RELAYED
from jentic_one.shared.models.events import EventType
from jentic_one.shared.webhooks.event_catalog import (
    subscribable_event_catalog,
    subscribable_event_types,
)

# The exact subscribable set, kept in lockstep with the UI's
# ``SUBSCRIBABLE_EVENT_TYPES`` drift pin. If this list changes, update the
# frontend catalog + its pin in the same change (and vice versa).
EXPECTED_SUBSCRIBABLE = frozenset(
    {
        "access_request.approved",
        "access_request.denied",
        "access_request.filed",
        "access_request.withdrawn",
        "agent.created",
        "agent.registration_approved",
        "agent.registration_denied",
        "agent.self_registered",
        "broker.pbac_denied",
        "broker.toolkit_binding_unserved",
        "catalog.update_available",
        "catalog.update_conflicts_overlay",
        "credential.bound_to_toolkit",
        "credential.connected",
        "credential.connection_failed",
        "credential.expired",
        "credential.expiring_soon",
        "credential.not_provisioned",
        "credential.refresh_failed",
        "credential.stored",
        "credential.unbound_from_toolkit",
        "credential.undecryptable",
        "execution.completed",
        "execution.failed",
        "execution.repeated_failure",
        "import.completed",
        "import.failed",
        "job.failed_permanently",
        "overlay.deprecated",
        "security.unauthorized_access_attempt",
        "toolkit.bound_to_agent",
        "toolkit.created",
        "toolkit.key_created",
        "toolkit.permission_rule_set",
        "toolkit.unbound_from_agent",
        "upstream.circuit_open",
    }
)


def test_catalog_is_all_minus_never_relayed() -> None:
    """The subscribable set is exactly ``EventType.ALL`` minus the denylist."""
    assert subscribable_event_types() == EventType.ALL - NEVER_RELAYED


def test_catalog_matches_the_pinned_set() -> None:
    """The computed catalog matches the list the UI mirrors (drift pin).

    The UI's curated ``WEBHOOK_EVENT_CATALOG`` is asserted equal to the same
    literal set in the frontend test, so pinning both sides to this constant is
    what keeps the picker from ever offering (or hiding) a type the backend
    doesn't agree with.
    """
    assert subscribable_event_types() == EXPECTED_SUBSCRIBABLE


def test_catalog_is_sorted_and_deterministic() -> None:
    """The served list is sorted, so the UI order is stable across restarts."""
    catalog = subscribable_event_catalog()
    assert catalog == sorted(catalog)
    assert set(catalog) == EXPECTED_SUBSCRIBABLE


def test_never_relayed_types_are_excluded() -> None:
    """A withheld type is never offered — subscribing to it would be a no-op."""
    for withheld in NEVER_RELAYED:
        assert withheld not in subscribable_event_types()
    # The synthetic wiring probe is not a subscribable type either.
    assert "webhook.test" not in subscribable_event_types()
