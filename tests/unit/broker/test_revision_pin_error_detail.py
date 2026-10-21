"""Unit tests: ``Jentic-Revision`` pin errors name the pin as the caller wrote it.

A caller pins by the public ``revision_id`` UUID (#1720); the resolver only ever
sees the ``rev_<uuid.hex>`` label derived from it. Every pin error the broker
raises — from ``resolve_pin_for_api`` and from ``discover_via_pins`` — must echo
the caller's own value, not the internal label, so an agent searching the error
for what it sent finds it.
"""

from __future__ import annotations

import uuid

import pytest

from jentic_one.broker.core.exceptions import (
    InvalidRevisionPinError,
    UnauthorizedRevisionPinError,
)
from jentic_one.broker.core.revisions import parse_revisions
from jentic_one.broker.services.discovery import discover_via_pins, resolve_pin_for_api
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import (
    ResolveResult,
    RevisionPinOutcome,
    RevisionPinResult,
)
from jentic_one.shared.schemas import APIReference, OperationInfo

REVISION_ID = "3f2b8c1e-7d4a-4e9b-a6c5-1d2e3f4a5b6c"
LABEL = "rev_3f2b8c1e7d4a4e9ba6c51d2e3f4a5b6c"
API = APIReference(vendor="acme", name="widgets", version="1.0.0")
IDENTITY = Identity(sub="usr_agent", permissions=[])


class _FixedOutcomeResolver:
    """A resolver whose every pin resolves to ``outcome``; records the label it saw."""

    def __init__(self, outcome: RevisionPinOutcome) -> None:
        self.outcome = outcome
        self.labels: list[str] = []

    async def resolve_operation(
        self, *, method: str, url: str, revision_id: uuid.UUID | None = None
    ) -> ResolveResult | None:
        return ResolveResult(operation=OperationInfo(id="op-1"), api=API, path_params={})

    async def resolve_revision_pin(
        self, *, vendor: str, name: str, version: str, rev_label: str, identity: Identity
    ) -> RevisionPinResult:
        self.labels.append(rev_label)
        return RevisionPinResult(self.outcome, revision_id=uuid.UUID(REVISION_ID))


@pytest.mark.parametrize(
    ("outcome", "error", "problem_type"),
    [
        (RevisionPinOutcome.UNKNOWN, InvalidRevisionPinError, "unknown_revision_pin"),
        (RevisionPinOutcome.ARCHIVED, InvalidRevisionPinError, "archived_revision_pin"),
        (RevisionPinOutcome.FORBIDDEN, UnauthorizedRevisionPinError, "unauthorized_revision_pin"),
        (
            RevisionPinOutcome.HOST_CHANGE_HELD,
            UnauthorizedRevisionPinError,
            "host_change_requires_operator",
        ),
    ],
)
@pytest.mark.parametrize("sent", [REVISION_ID, REVISION_ID.upper(), LABEL])
@pytest.mark.asyncio
async def test_resolve_pin_error_echoes_the_revision_the_caller_sent(
    outcome: RevisionPinOutcome,
    error: type[Exception],
    problem_type: str,
    sent: str,
) -> None:
    resolver = _FixedOutcomeResolver(outcome)
    pins = parse_revisions(f"acme:widgets:1.0.0={sent}")

    with pytest.raises(error) as excinfo:
        await resolve_pin_for_api(resolver, api=API, pins=pins, identity=IDENTITY)

    detail = excinfo.value.detail  # type: ignore[attr-defined]
    assert excinfo.value.type == problem_type  # type: ignore[attr-defined]
    assert f"acme:widgets:1.0.0={sent}" in detail
    if sent != LABEL:
        assert "rev_" not in detail
    assert resolver.labels == [LABEL]


@pytest.mark.parametrize("sent", [REVISION_ID, LABEL])
@pytest.mark.asyncio
async def test_held_pin_via_pins_echoes_the_revision_the_caller_sent(sent: str) -> None:
    resolver = _FixedOutcomeResolver(RevisionPinOutcome.HOST_CHANGE_HELD)
    pins = parse_revisions(f"acme:widgets:1.0.0={sent}")

    with pytest.raises(UnauthorizedRevisionPinError) as excinfo:
        await discover_via_pins(
            resolver, method="GET", url="https://x.test/w", pins=pins, identity=IDENTITY
        )

    assert excinfo.value.type == "host_change_requires_operator"
    assert f"acme:widgets:1.0.0={sent}" in excinfo.value.detail
    if sent != LABEL:
        assert "rev_" not in excinfo.value.detail
    assert resolver.labels == [LABEL]
