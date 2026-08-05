"""Unit tests for the version endpoint (``GET /system/version``).

Reads the running ``__version__`` (always) and the last-known-latest release
from the admin DB (only when present). On a surface without the admin database
it degrades to ``latest=null`` / ``update_available=false`` rather than erroring,
which is exactly the fast (DB-less) path exercised here.

The endpoint requires an authenticated session (any valid caller, no special
permission), so the behaviour tests inject a fixed identity via the canonical
``dependency_overrides[resolve_identity]`` seam; an unauthenticated request is
rejected.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one import __version__
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.control.web.app import create_app as create_control_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app
from jentic_one.shared.web.deps import resolve_identity


def _ctx(sample_config_dict: dict[str, Any]) -> Context:
    return Context(AppConfig.model_validate(dict(sample_config_dict)))


def _authed(app: FastAPI) -> FastAPI:
    """Override identity resolution with a fixed, ordinary (no-scope) caller.

    The version endpoint needs only *a* session, so a bare identity suffices.
    """

    async def _override(_: object = None) -> Identity:
        return Identity(sub="usr_version_test", email="version@test.local", permissions=[])

    app.dependency_overrides[resolve_identity] = _override
    return app


def _authed_client(ctx: Context, surfaces: list[str]) -> TestClient:
    return TestClient(_authed(create_combined_app(ctx, surfaces)), raise_server_exceptions=False)


def test_version_endpoint_reports_current(sample_config_dict: dict[str, Any]) -> None:
    """Without an admin DB the endpoint still reports the running version."""
    client = _authed_client(_ctx(sample_config_dict), ["control"])

    resp = client.get("/system/version")

    assert resp.status_code == 200
    data = resp.json()
    assert data["current"] == __version__
    # No admin DB on a control-only surface, so nothing has been reported.
    assert data["latest"] is None
    assert data["update_available"] is False


def test_version_endpoint_requires_authentication(sample_config_dict: dict[str, Any]) -> None:
    """No credential -> rejected (the version is not published unauthenticated)."""
    ctx = _ctx(sample_config_dict)
    client = TestClient(create_combined_app(ctx, ["control"]), raise_server_exceptions=False)

    assert client.get("/system/version").status_code == 401


def test_version_endpoint_present_on_standalone_surface(
    sample_config_dict: dict[str, Any],
) -> None:
    ctx = _ctx(sample_config_dict)
    client = TestClient(_authed(create_control_app(ctx)), raise_server_exceptions=False)

    assert client.get("/system/version").status_code == 200


def test_version_endpoint_not_mounted_on_broker(sample_config_dict: dict[str, Any]) -> None:
    """The broker data plane must not advertise the control-plane version surface.

    Even an authenticated caller must not get a 200 here — the route is simply
    absent (the broker's auth middleware may answer an unknown path with 401
    before routing, so assert "not served" rather than a specific 404).
    """
    ctx = _ctx(sample_config_dict)
    client = TestClient(_authed(create_broker_app(ctx)), raise_server_exceptions=False)

    assert client.get("/system/version").status_code != 200


def test_version_endpoint_response_has_exactly_the_documented_fields(
    sample_config_dict: dict[str, Any],
) -> None:
    client = _authed_client(_ctx(sample_config_dict), ["control"])

    data = client.get("/system/version").json()

    assert set(data) == {"current", "latest", "update_available"}


def test_version_endpoint_schema_requires_bearer_auth(sample_config_dict: dict[str, Any]) -> None:
    """A real (schema-visible) route that requires BearerAuth, tagged System."""
    ctx = _ctx(sample_config_dict)
    app = create_combined_app(ctx, ["control"])

    op = app.openapi()["paths"]["/system/version"]["get"]
    # Not public: the platform bearer requirement is present (security is non-empty).
    assert op.get("security")
    assert op["tags"] == ["System"]
    assert op["operationId"] == "getVersion"
