"""Unit tests for the broker registry-resolver composition wiring.

Verifies the in-process resolver satisfies ``RegistryResolverProtocol``, that
``install_broker_registry_resolver`` injects it onto broker app state, so the
broker can resolve URLs without importing ``jentic_one.registry``, and that a
resolver missing a protocol method is rejected at injection rather than on the
first request that reaches it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from jentic_one.shared.broker.protocols import (
    RegistryResolverProtocol,
    require_registry_resolver,
)
from jentic_one.wiring import InProcessRegistryResolver, install_broker_registry_resolver


def test_in_process_resolver_satisfies_protocol() -> None:
    resolver = InProcessRegistryResolver(MagicMock())
    assert isinstance(resolver, RegistryResolverProtocol)
    assert require_registry_resolver(resolver) is resolver


def test_install_sets_broker_registry_resolver_on_app_state() -> None:
    app = FastAPI()
    ctx: Any = MagicMock()

    install_broker_registry_resolver(app, ctx)

    assert isinstance(app.state.broker_registry_resolver, InProcessRegistryResolver)
    assert isinstance(app.state.broker_registry_resolver, RegistryResolverProtocol)


def test_resolver_breaking_the_protocol_is_rejected_at_injection() -> None:
    """A resolver without ``find_draft_match`` is a wiring bug, caught before any request."""

    class _NoDraftMatch:
        async def resolve_operation(self, **_: object) -> None:
            return None

        async def resolve_revision_pin(self, **_: object) -> None:
            return None

    with pytest.raises(TypeError, match="missing find_draft_match"):
        require_registry_resolver(_NoDraftMatch())
