"""App-factory-level tests for the AWS Marketplace entitlement gate.

Two guarantees:

1. **OSS-inert**: with the default config the built app carries no entitlement
   middleware or state — byte-identical wiring to before the gate existed.
2. With ``entitlement.enabled`` the real combined factory mounts the gate, and
   flipping the gate locks/unlocks the running app (503 problem details for
   API paths, ``not_entitled`` health body) without a rebuild.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.integrations.aws_marketplace.gate import EntitlementGate, EntitlementMiddleware
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration

_APPS = ["registry", "admin", "control", "auth"]


def _build(ctx: Context) -> FastAPI:
    app = create_combined_app(ctx, list(_APPS))
    app.router.lifespan_context = noop_lifespan
    return app


@pytest.fixture()
def entitled_context(web_config: AppConfig) -> Context:
    """Unconnected Context whose config enables the entitlement gate.

    Building an app is synchronous (no DB I/O), and the gate tests below only
    hit paths the middleware short-circuits, so the Context never needs
    ``startup()``.
    """
    config = web_config.model_copy(deep=True)
    config.entitlement.enabled = True
    config.entitlement.product_code = "prod-webtest"
    config.entitlement.license_sku = "prod-id-webtest"  # contract default needs it
    return Context(config)


def test_default_config_has_no_entitlement_wiring(web_context: Context) -> None:
    """The OSS-inert guarantee, asserted explicitly on the middleware list."""
    app = _build(web_context)

    # Starlette types Middleware.cls as an opaque factory; compare as Any.
    middleware_classes: list[Any] = [m.cls for m in app.user_middleware]
    assert EntitlementMiddleware not in middleware_classes
    assert not hasattr(app.state, "entitlement_gate")


def test_enabled_config_mounts_gate_and_only_gate(
    web_context: Context, entitled_context: Context
) -> None:
    """Enabling entitlement adds exactly one middleware — nothing else moves."""
    default_app = _build(web_context)
    entitled_app = _build(entitled_context)

    default_stack: list[Any] = [m.cls for m in default_app.user_middleware]
    entitled_stack: list[Any] = [m.cls for m in entitled_app.user_middleware]
    assert [cls for cls in entitled_stack if cls is not EntitlementMiddleware] == default_stack
    assert entitled_stack.count(EntitlementMiddleware) == 1
    assert isinstance(entitled_app.state.entitlement_gate, EntitlementGate)


def test_gate_locks_and_recovers_live_app(entitled_context: Context) -> None:
    app = _build(entitled_context)
    gate: EntitlementGate = app.state.entitlement_gate

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/health").json()["status"] == "ok"

        gate.lock("subscription expired")
        locked = client.get("/agents/discovery")
        assert locked.status_code == 503
        assert locked.headers["content-type"] == "application/problem+json"
        assert locked.json()["type"] == "https://jentic.com/problems/not-entitled"
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "not_entitled",
            "reason": "subscription expired",
        }

        gate.unlock()
        assert client.get("/health").json()["status"] == "ok"
