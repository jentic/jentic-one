"""Unit tests for the broker registry-resolver composition wiring.

Verifies the in-process resolver satisfies ``RegistryResolverProtocol``, that
``install_broker_registry_resolver`` injects it onto broker app state, so the
broker can resolve URLs without importing ``jentic_one.registry``, and that a
resolver missing a protocol method, or whose method is not async or cannot
take the protocol's arguments, is rejected at injection rather than on the
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


class _Conforming:
    async def resolve_operation(self, *, method: str, url: str, revision_id: object = None) -> None:
        return None

    async def resolve_revision_pin(
        self, *, vendor: str, name: str, version: str, rev_label: str, identity: object
    ) -> None:
        return None

    async def find_draft_match(self, *, method: str, url: str, identity: object) -> None:
        return None


def test_conforming_resolver_with_extra_optional_parameters_is_accepted() -> None:
    class _ExtraOptional(_Conforming):
        async def find_draft_match(
            self, *, method: str, url: str, identity: object, trace: bool = False
        ) -> None:
            return None

    resolver: object = _ExtraOptional()
    assert require_registry_resolver(resolver) is resolver


async def _drops_a_parameter(self: object, *, method: str, url: str) -> None:
    return None


async def _requires_an_extra_parameter(
    self: object, *, method: str, url: str, identity: object, limit: int
) -> None:
    return None


async def _requires_an_optional_parameter(
    self: object, *, method: str, url: str, revision_id: object
) -> None:
    return None


def _not_async(self: object, *, method: str, url: str, identity: object) -> None:
    return None


@pytest.mark.parametrize(
    ("name", "method", "problem"),
    [
        pytest.param(
            "find_draft_match", _drops_a_parameter, "find_draft_match cannot take", id="drops"
        ),
        pytest.param(
            "find_draft_match",
            _requires_an_extra_parameter,
            "find_draft_match cannot take",
            id="requires-extra",
        ),
        pytest.param(
            "resolve_operation",
            _requires_an_optional_parameter,
            "resolve_operation cannot take",
            id="requires-optional",
        ),
        pytest.param("find_draft_match", _not_async, "find_draft_match is not async", id="sync"),
    ],
)
def test_resolver_with_a_wrong_method_signature_is_rejected_at_injection(
    name: str, method: object, problem: str
) -> None:
    """A method the broker would call with a ``TypeError`` fails at startup, not per request."""
    broken = type("_Broken", (_Conforming,), {name: method})

    with pytest.raises(TypeError, match=problem):
        require_registry_resolver(broken())
