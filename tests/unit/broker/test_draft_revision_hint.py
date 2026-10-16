"""Unit tests for the draft-revision hint on the unregistered-URL 404 (#1721).

The registry-backed paths (a draft serves the URL, nothing does, visibility
gating, statement counts) are covered against a real database in
``tests/integration/broker/test_draft_revision_hint.py``. These cover the
pure branches: the pin rule and the best-effort fallback when the probe fails.
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
from jentic_one.shared.broker.protocols import DraftRevisionMatch
from jentic_one.shared.models import ActorType
from jentic_one.shared.schemas import APIReference

_URL = "https://api.example.com/v1/widgets"
_REVISION = uuid.UUID("3f2b8c1e-7d4a-4e9b-a6c5-1d2e3f4a5b6c")


def _identity(*, sub: str = "agnt_reader", permissions: list[str]) -> Identity:
    return Identity(
        sub=sub,
        actor_type=ActorType.AGENT,
        permissions=permissions,
        expires_at=datetime(2999, 1, 1, tzinfo=UTC),
        active=True,
    )


def _match(*, submitted_by: str | None = None, origin: str | None = None) -> DraftRevisionMatch:
    return DraftRevisionMatch(
        api=APIReference(vendor="api.example.com", name="main", version="1.0.0"),
        revision_id=_REVISION,
        submitted_by=submitted_by,
        origin=origin,
    )


@pytest.mark.asyncio
async def test_probe_failure_falls_back_to_the_generic_404() -> None:
    resolver = AsyncMock()
    resolver.find_draft_match.side_effect = RuntimeError("db down")

    err = await unregistered_operation_error(
        resolver, method="GET", url=_URL, identity=_identity(permissions=["apis:read"])
    )

    assert err.detail == UNREGISTERED_URL_DETAIL
    assert err.type == "operation_not_found"
    assert err.directive is None


@pytest.mark.asyncio
async def test_caller_without_apis_read_never_probes() -> None:
    resolver = AsyncMock()

    err = await unregistered_operation_error(
        resolver, method="GET", url=_URL, identity=_identity(permissions=["execute"])
    )

    resolver.find_draft_match.assert_not_called()
    assert err.detail == UNREGISTERED_URL_DETAIL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("match", "permissions", "sub", "strategy"),
    [
        (_match(submitted_by="agnt_reader"), ["apis:read"], "agnt_reader", "modify_headers"),
        (_match(submitted_by="someone_else"), ["apis:read"], "agnt_reader", "prompt_human"),
        (_match(origin="catalog"), ["apis:read"], "agnt_reader", "prompt_human"),
        (
            _match(origin="catalog"),
            ["apis:read", "credentials:write"],
            "agnt_reader",
            "modify_headers",
        ),
    ],
)
async def test_directive_offers_the_pin_only_when_the_registry_would_accept_it(
    match: DraftRevisionMatch, permissions: list[str], sub: str, strategy: str
) -> None:
    resolver = AsyncMock()
    resolver.find_draft_match.return_value = match

    err = await unregistered_operation_error(
        resolver, method="GET", url=_URL, identity=_identity(sub=sub, permissions=permissions)
    )

    assert err.type == "operation_not_found"
    assert err.directive is not None
    assert err.directive.strategy == strategy
    headers = err.directive.parameters.get("headers")
    if strategy == "modify_headers":
        assert headers == {"Jentic-Revision": f"api.example.com:main:1.0.0=rev_{_REVISION.hex}"}
    else:
        assert headers is None
    assert err.extra["draft_revision"]["promoted"] is False
