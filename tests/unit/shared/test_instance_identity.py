"""Unit tests for the public backend-identity endpoint (``GET /instance``).

The endpoint lets a client (an MCP server, the CLI, an agent) tell which backend
it is bound to — cloud vs. a local self-hosted install — so it can label its
responses and avoid mistaking a different backend for data loss (issue #702). It
reads only config off the live ``Context`` (no DB), so these run as fast units.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from jentic_one import __version__
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.control.web.app import create_app as create_control_app
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app
from jentic_one.shared.web.instance_identity import (
    InstanceIdentityResponse,
    resolve_instance_identity,
)


def _ctx(sample_config_dict: dict[str, Any], canonical_base_url: str) -> Context:
    cfg = dict(sample_config_dict)
    cfg["auth"] = {**cfg.get("auth", {}), "canonical_base_url": canonical_base_url}
    return Context(AppConfig.model_validate(cfg))


def test_instance_endpoint_reports_local_backend(sample_config_dict: dict[str, Any]) -> None:
    ctx = _ctx(sample_config_dict, "http://127.0.0.1:8000")
    client = TestClient(create_combined_app(ctx, ["control"]), raise_server_exceptions=False)

    resp = client.get("/instance")

    assert resp.status_code == 200
    data = resp.json()
    assert data["backend"] == "local"
    assert data["canonical_base_url"] == "http://127.0.0.1:8000"
    assert data["host"] == "127.0.0.1:8000"
    assert data["version"] == __version__
    # Telemetry has not resolved an id in this bare app, so it is null.
    assert data["instance_id"] is None


def test_instance_endpoint_reports_cloud_backend(sample_config_dict: dict[str, Any]) -> None:
    ctx = _ctx(sample_config_dict, "https://app.jentic.com")
    client = TestClient(create_combined_app(ctx, ["control"]), raise_server_exceptions=False)

    data = client.get("/instance").json()

    assert data["backend"] == "cloud"
    assert data["host"] == "app.jentic.com"


def test_instance_endpoint_reports_self_hosted_backend(sample_config_dict: dict[str, Any]) -> None:
    ctx = _ctx(sample_config_dict, "https://jentic.acme.example")
    client = TestClient(create_combined_app(ctx, ["control"]), raise_server_exceptions=False)

    data = client.get("/instance").json()

    assert data["backend"] == "self-hosted"
    assert data["host"] == "jentic.acme.example"


def test_instance_endpoint_unset_base_url_is_self_hosted(
    sample_config_dict: dict[str, Any],
) -> None:
    ctx = _ctx(sample_config_dict, "")
    client = TestClient(create_combined_app(ctx, ["control"]), raise_server_exceptions=False)

    data = client.get("/instance").json()

    assert data["backend"] == "self-hosted"
    assert data["canonical_base_url"] == ""
    assert data["host"] == ""


def test_instance_endpoint_is_unauthenticated(sample_config_dict: dict[str, Any]) -> None:
    """No Authorization header is required — it is a public identity probe."""
    ctx = _ctx(sample_config_dict, "http://127.0.0.1:8000")
    client = TestClient(create_combined_app(ctx, ["control"]), raise_server_exceptions=False)

    assert client.get("/instance").status_code == 200


def test_instance_endpoint_present_on_standalone_surface(
    sample_config_dict: dict[str, Any],
) -> None:
    """The identity surface is mounted in standalone surface apps too, not just combined."""
    ctx = _ctx(sample_config_dict, "http://127.0.0.1:8000")
    client = TestClient(create_control_app(ctx), raise_server_exceptions=False)

    assert client.get("/instance").status_code == 200


def test_instance_endpoint_not_mounted_on_broker(sample_config_dict: dict[str, Any]) -> None:
    """The broker data plane must not advertise the control-plane identity surface.

    The broker is a forward proxy whose only public routes are its liveness /
    readiness probes; ``/instance`` belongs to the control plane.
    """
    ctx = _ctx(sample_config_dict, "http://127.0.0.1:8000")
    client = TestClient(create_broker_app(ctx), raise_server_exceptions=False)

    resp = client.get("/instance")
    # The broker has no /instance route; the path falls through to its auth-gated
    # forward proxy (401), so it never serves a backend-identity payload.
    assert resp.status_code != 200
    assert "backend" not in resp.text


def test_instance_endpoint_surfaces_resolved_instance_id(
    sample_config_dict: dict[str, Any],
) -> None:
    ctx = _ctx(sample_config_dict, "http://127.0.0.1:8000")
    ctx.instance_id = "inst-abc-123"
    client = TestClient(create_combined_app(ctx, ["control"]), raise_server_exceptions=False)

    assert client.get("/instance").json()["instance_id"] == "inst-abc-123"


def test_instance_endpoint_hidden_public_in_openapi(sample_config_dict: dict[str, Any]) -> None:
    """It is a real (schema-visible) route stamped public (no BearerAuth)."""
    ctx = _ctx(sample_config_dict, "http://127.0.0.1:8000")
    app = create_combined_app(ctx, ["control"])

    op = app.openapi()["paths"]["/instance"]["get"]
    assert op["security"] == []
    assert op["tags"] == ["System"]


@pytest.mark.parametrize(
    ("base_url", "expected_backend", "expected_host"),
    [
        ("http://localhost:9000", "local", "localhost:9000"),
        ("http://jentic.local", "local", "jentic.local"),
        ("https://app.jentic.com/", "cloud", "app.jentic.com"),
        ("https://jentic.com", "cloud", "jentic.com"),
    ],
)
def test_resolve_instance_identity_classification(
    sample_config_dict: dict[str, Any],
    base_url: str,
    expected_backend: str,
    expected_host: str,
) -> None:
    identity = resolve_instance_identity(_ctx(sample_config_dict, base_url))
    assert isinstance(identity, InstanceIdentityResponse)
    assert identity.backend == expected_backend
    assert identity.host == expected_host
