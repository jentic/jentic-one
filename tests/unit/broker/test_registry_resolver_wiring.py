"""Unit tests for the broker registry-resolver composition wiring.

Verifies the in-process resolver satisfies ``RegistryResolverProtocol`` and that
``install_broker_registry_resolver`` injects it onto broker app state, so the
broker can resolve URLs without importing ``jentic_one.registry``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from jentic_one.broker.core.discovery_cache import DiscoveryCache
from jentic_one.shared.broker.protocols import RegistryResolverProtocol
from jentic_one.shared.config import BrokerConfig
from jentic_one.wiring import InProcessRegistryResolver, install_broker_registry_resolver


def test_in_process_resolver_satisfies_protocol() -> None:
    resolver = InProcessRegistryResolver(MagicMock())
    assert isinstance(resolver, RegistryResolverProtocol)


def test_install_sets_broker_registry_resolver_on_app_state() -> None:
    app = FastAPI()
    ctx: Any = MagicMock()
    ctx.config.broker = BrokerConfig()

    install_broker_registry_resolver(app, ctx)

    assert isinstance(app.state.broker_registry_resolver, InProcessRegistryResolver)
    assert isinstance(app.state.broker_registry_resolver, RegistryResolverProtocol)


@pytest.mark.parametrize(("ttl_s", "cached"), [(3.0, True), (0.0, False)])
def test_install_honours_discovery_cache_ttl(ttl_s: float, cached: bool) -> None:
    """Route discovery is cached by default; ``discovery_cache_ttl_s = 0`` turns it off."""
    app = FastAPI()
    ctx: Any = MagicMock()
    ctx.config.broker = BrokerConfig(discovery_cache_ttl_s=ttl_s)

    install_broker_registry_resolver(app, ctx)

    cache = app.state.broker_registry_resolver._discovery_cache
    assert isinstance(cache, DiscoveryCache) is cached
