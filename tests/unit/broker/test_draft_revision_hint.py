"""Unit tests for the draft-revision hint on the unregistered-URL 404 (#1721).

Which draft is disclosed, and with which pin outcome, is the registry's
decision (``RegistryService.find_draft_match``); that is covered against a real
database and real broker auth in
``tests/integration/broker/test_draft_revision_hint.py``. These cover the
broker's pure branches: rendering each pin outcome, keeping the request's own
pins, the best-effort fallback and a resolver that breaks the protocol.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from jentic_one.broker.services.discovery import (
    UNREGISTERED_URL_DETAIL,
    unregistered_operation_error,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import DraftRevisionMatch, RevisionPinOutcome
from jentic_one.shared.models import ActorType
from jentic_one.shared.schemas import APIReference

_URL = "https://api.example.com/v1/widgets"
_REVISION = uuid.UUID("3f2b8c1e-7d4a-4e9b-a6c5-1d2e3f4a5b6c")
_DRAFT_PIN = f"api.example.com:main:1.0.0=rev_{_REVISION.hex}"


def _identity() -> Identity:
    return Identity(
        sub="agnt_reader",
        actor_type=ActorType.AGENT,
        permissions=["execute"],
        expires_at=datetime(2999, 1, 1, tzinfo=UTC),
        active=True,
    )


def _resolver(outcome: RevisionPinOutcome) -> AsyncMock:
    resolver = AsyncMock()
    resolver.find_draft_match.return_value = DraftRevisionMatch(
        api=APIReference(vendor="api.example.com", name="main", version="1.0.0"),
        revision_id=_REVISION,
        pin_outcome=outcome,
    )
    return resolver


@pytest.mark.asyncio
async def test_probe_failure_falls_back_to_the_generic_404() -> None:
    resolver = AsyncMock()
    resolver.find_draft_match.side_effect = RuntimeError("db down")

    err = await unregistered_operation_error(
        resolver, method="GET", url=_URL, identity=_identity(), pins={}
    )

    assert err.detail == UNREGISTERED_URL_DETAIL
    assert err.type == "operation_not_found"
    assert err.directive is None


@pytest.mark.asyncio
async def test_resolver_breaking_the_protocol_is_not_swallowed() -> None:
    """A resolver without ``find_draft_match`` is a wiring bug, not a miss."""

    class _NoDraftMatch:
        pass

    with pytest.raises(AttributeError):
        await unregistered_operation_error(
            _NoDraftMatch(),  # type: ignore[arg-type]
            method="GET",
            url=_URL,
            identity=_identity(),
            pins={},
        )


@pytest.mark.asyncio
async def test_no_disclosable_draft_is_the_generic_404() -> None:
    resolver = AsyncMock()
    resolver.find_draft_match.return_value = None

    err = await unregistered_operation_error(
        resolver, method="GET", url=_URL, identity=_identity(), pins={}
    )

    assert err.detail == UNREGISTERED_URL_DETAIL
    assert err.directive is None
    assert "draft_revision" not in err.extra
    resolver.find_draft_match.assert_awaited_once_with(method="GET", url=_URL, identity=_identity())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "strategy"),
    [
        (RevisionPinOutcome.RESOLVED, "modify_headers"),
        (RevisionPinOutcome.HOST_CHANGE_HELD, "prompt_human"),
        (RevisionPinOutcome.FORBIDDEN, "prompt_human"),
    ],
)
async def test_directive_offers_the_pin_only_when_the_registry_resolves_it(
    outcome: RevisionPinOutcome, strategy: str
) -> None:
    err = await unregistered_operation_error(
        _resolver(outcome), method="GET", url=_URL, identity=_identity(), pins={}
    )

    assert err.type == "operation_not_found"
    assert err.directive is not None
    assert err.directive.strategy == strategy
    headers = err.directive.parameters.get("headers")
    if strategy == "modify_headers":
        assert headers == {"Jentic-Revision": _DRAFT_PIN}
    else:
        assert headers is None
    assert err.extra["draft_revision"]["promoted"] is False


@pytest.mark.asyncio
async def test_suggested_header_keeps_the_requests_other_pins() -> None:
    """Resending with the suggested header loses none of the caller's pins;
    a stale pin for the draft's own API is replaced."""
    pins = {
        ("other.example.com", "main", "2.0.0"): "rev_aaaa",
        ("api.example.com", "main", "1.0.0"): "rev_stale",
    }

    err = await unregistered_operation_error(
        _resolver(RevisionPinOutcome.RESOLVED),
        method="GET",
        url=_URL,
        identity=_identity(),
        pins=pins,
    )

    assert err.directive is not None
    assert err.directive.parameters["headers"] == {
        "Jentic-Revision": f"other.example.com:main:2.0.0=rev_aaaa, {_DRAFT_PIN}"
    }
