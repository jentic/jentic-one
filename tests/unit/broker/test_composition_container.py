"""``composition.build_app`` forwards a caller's container to the standalone broker.

An integrator composes the broker through the same path ``jentic-one serve``
uses and passes its own ``AppContainer`` (extra routers, an injected broker).
The container must reach the broker's factory, the post-build wiring (the
registry resolver) must still run, and the root ``/{upstream_url:path}``
catch-all must still answer every path the extra routers do not claim.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.testclient import TestClient

from jentic_one.composition import build_app, build_context
from jentic_one.shared.config import AppConfig
from jentic_one.shared.web.container import AppContainer

_extra_router = APIRouter()


@_extra_router.get("/status")
async def _extra_status() -> dict[str, str]:
    return {"source": "extra-router"}


def _broker_config(sample_config_dict: dict[str, Any]) -> AppConfig:
    return AppConfig.model_validate({**sample_config_dict, "apps": ["broker"]})


def test_build_app_forwards_container_to_standalone_broker(
    sample_config_dict: dict[str, Any],
) -> None:
    ctx = build_context(_broker_config(sample_config_dict), ["broker"])
    container = AppContainer(ctx=ctx, extra_routers=[(_extra_router, "/overlay", ["overlay"])])

    app = build_app(ctx, ["broker"], container=container)

    # The post-build wiring still ran on the container-built app.
    assert app.state.broker_registry_resolver is not None

    # The extra router answers: the catch-all, included after it, cannot
    # shadow it.
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/overlay/status")
    assert resp.status_code == 200
    assert resp.json() == {"source": "extra-router"}

    # Any other path still reaches the forward-proxy catch-all, which answers
    # an unauthenticated call with its 401 problem rather than a 404.
    proxied = client.get("/api.example.com/v1/things")
    assert proxied.status_code == 401
    assert proxied.headers["content-type"].startswith("application/problem+json")


def test_build_app_without_container_keeps_default_broker(
    sample_config_dict: dict[str, Any],
) -> None:
    ctx = build_context(_broker_config(sample_config_dict), ["broker"])

    app = build_app(ctx, ["broker"])

    assert app.state.broker_registry_resolver is not None
    # No extra router: the same path falls through to the catch-all.
    resp = TestClient(app, raise_server_exceptions=False).get("/overlay/status")
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")
