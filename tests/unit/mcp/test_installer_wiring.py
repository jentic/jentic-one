"""Composition-root wiring for the ``/mcp`` mount (phase-3 item 1).

Pins where the mount rides: ``build_default_container`` carries the installer
+ session-manager lifespan on every app shape that includes the control
surface (the combined app, a standalone control surface) and NEVER on the
broker (master §6 Q1, resolved 2026-09-02: the broker stays MCP-free —
``execute`` proxies control-plane→broker server-side instead).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from jentic_one.__main__ import _build_app, _expand_allowed_dbs
from jentic_one.mcp.installer import install_mcp_mount, mcp_lifespan
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.wiring import build_default_container


def _ctx(sample_config_dict: dict[str, Any], apps: list[str]) -> Context:
    sample_config_dict = {**sample_config_dict, "apps": apps}
    config = AppConfig.model_validate(sample_config_dict)
    return Context(config, allowed_dbs=_expand_allowed_dbs(apps, config))


def test_control_shapes_carry_the_mcp_extras(sample_config_dict: dict[str, Any]) -> None:
    container = build_default_container(_ctx(sample_config_dict, ["control", "admin"]))
    assert install_mcp_mount in container.extra_installers
    assert mcp_lifespan in container.extra_lifespans


def test_broker_shape_stays_mcp_free(sample_config_dict: dict[str, Any]) -> None:
    """Master §6 Q1: the broker never serves /mcp."""
    container = build_default_container(_ctx(sample_config_dict, ["broker"]))
    assert install_mcp_mount not in container.extra_installers
    assert mcp_lifespan not in container.extra_lifespans


def test_combined_app_answers_on_mcp_with_the_gate_default(
    sample_config_dict: dict[str, Any],
) -> None:
    """End-to-end through _build_app: the mount exists on the combined app but
    the default-off gate answers the framework's plain 404 (arm 1) — and the
    path stays out of the OpenAPI schema (a plain Route, like the placeholder)."""
    ctx = _ctx(sample_config_dict, ["control", "admin", "auth", "registry"])
    app = _build_app(ctx, ["control", "admin", "auth", "registry"])
    assert getattr(app.state, "mcp_mount", None) is not None

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/mcp")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
    assert "/mcp" not in client.get("/openapi.json").json()["paths"]


def test_standalone_control_app_carries_the_mount(sample_config_dict: dict[str, Any]) -> None:
    ctx = _ctx(sample_config_dict, ["control"])
    app = _build_app(ctx, ["control"])
    assert getattr(app.state, "mcp_mount", None) is not None


def test_standalone_broker_app_has_no_mount(sample_config_dict: dict[str, Any]) -> None:
    ctx = _ctx(sample_config_dict, ["broker"])
    app = _build_app(ctx, ["broker"])
    assert getattr(app.state, "mcp_mount", None) is None
