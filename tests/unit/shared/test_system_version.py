"""Unit tests for the public version endpoint (``GET /system/version``).

Reads the running ``__version__`` (always) and the last-known-latest release
from the admin DB (only when present). On a surface without the admin database
it degrades to ``latest=null`` / ``update_available=false`` rather than erroring,
which is exactly the fast (DB-less) path exercised here.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from jentic_one import __version__
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.control.web.app import create_app as create_control_app
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app


def _ctx(sample_config_dict: dict[str, Any]) -> Context:
    return Context(AppConfig.model_validate(dict(sample_config_dict)))


def test_version_endpoint_reports_current(sample_config_dict: dict[str, Any]) -> None:
    """Without an admin DB the endpoint still reports the running version."""
    ctx = _ctx(sample_config_dict)
    client = TestClient(create_combined_app(ctx, ["control"]), raise_server_exceptions=False)

    resp = client.get("/system/version")

    assert resp.status_code == 200
    data = resp.json()
    assert data["current"] == __version__
    # No admin DB on a control-only surface, so nothing has been reported.
    assert data["latest"] is None
    assert data["update_available"] is False


def test_version_endpoint_is_unauthenticated(sample_config_dict: dict[str, Any]) -> None:
    ctx = _ctx(sample_config_dict)
    client = TestClient(create_combined_app(ctx, ["control"]), raise_server_exceptions=False)

    assert client.get("/system/version").status_code == 200


def test_version_endpoint_present_on_standalone_surface(
    sample_config_dict: dict[str, Any],
) -> None:
    ctx = _ctx(sample_config_dict)
    client = TestClient(create_control_app(ctx), raise_server_exceptions=False)

    assert client.get("/system/version").status_code == 200


def test_version_endpoint_not_mounted_on_broker(sample_config_dict: dict[str, Any]) -> None:
    """The broker data plane must not advertise the control-plane version surface."""
    ctx = _ctx(sample_config_dict)
    client = TestClient(create_broker_app(ctx), raise_server_exceptions=False)

    assert client.get("/system/version").status_code != 200


def test_version_endpoint_response_has_exactly_the_documented_fields(
    sample_config_dict: dict[str, Any],
) -> None:
    ctx = _ctx(sample_config_dict)
    client = TestClient(create_combined_app(ctx, ["control"]), raise_server_exceptions=False)

    data = client.get("/system/version").json()

    assert set(data) == {"current", "latest", "update_available"}


def test_version_endpoint_schema_visible_and_public(sample_config_dict: dict[str, Any]) -> None:
    """A real (schema-visible) route stamped public (no BearerAuth), tagged System."""
    ctx = _ctx(sample_config_dict)
    app = create_combined_app(ctx, ["control"])

    op = app.openapi()["paths"]["/system/version"]["get"]
    assert op["security"] == []
    assert op["tags"] == ["System"]
    assert op["operationId"] == "getVersion"
