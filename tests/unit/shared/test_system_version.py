"""Unit tests for the version endpoint (``GET /system/version``).

Reports the running ``__version__`` (always) and the latest available release,
resolved server-side from GitHub by ``ReleaseChecker`` (cached, best-effort).
When the release check is disabled/air-gapped, a remote backend, or GitHub is
unreachable, it degrades to ``latest=null`` / ``update_available=false`` rather
than erroring.

Most tests disable the release check in config so they make no network call and
``latest`` is deterministically ``null``; a dedicated test stubs the GitHub
fetch to exercise the "update available" verdict and the in-process cache.

The endpoint requires an authenticated session (any valid caller, no special
permission), so the behaviour tests inject a fixed identity via the canonical
``dependency_overrides[resolve_identity]`` seam; an unauthenticated request is
rejected.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one import __version__
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.control.web.app import create_app as create_control_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.release_check import CatalogFetchError
from jentic_one.shared.web.app_factory import create_combined_app
from jentic_one.shared.web.deps import resolve_identity


def _ctx(sample_config_dict: dict[str, Any], *, release_check: bool = False) -> Context:
    """A Context with the release check off by default (no egress in tests)."""
    cfg = dict(sample_config_dict)
    cfg["release_check"] = {"enabled": release_check}
    return Context(AppConfig.model_validate(cfg))


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
    """With the release check off the endpoint still reports the running version."""
    client = _authed_client(_ctx(sample_config_dict), ["control"])

    resp = client.get("/system/version")

    assert resp.status_code == 200
    data = resp.json()
    assert data["current"] == __version__
    # Release check disabled -> latest unknown, no banner.
    assert data["latest"] is None
    assert data["update_available"] is False


def test_version_endpoint_surfaces_a_newer_release(
    sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A newer GitHub release lights up ``update_available`` (fetch is stubbed)."""
    calls = 0

    async def _fake_fetch(url: str, *, config: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"tag_name": "v999.0.0"}

    monkeypatch.setattr("jentic_one.shared.release_check.fetch_json", _fake_fetch)

    ctx = _ctx(sample_config_dict, release_check=True)
    client = _authed_client(ctx, ["control"])

    data = client.get("/system/version").json()
    assert data["current"] == __version__
    assert data["latest"] == "999.0.0"
    assert data["update_available"] is True

    # Second read is served from the in-process cache — GitHub is hit once.
    client.get("/system/version")
    assert calls == 1


def test_version_endpoint_degrades_when_github_unreachable(
    sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fetch failure degrades to latest=null (no banner), never a 500."""

    async def _boom(url: str, *, config: Any) -> dict[str, Any]:
        raise CatalogFetchError("offline")

    monkeypatch.setattr("jentic_one.shared.release_check.fetch_json", _boom)

    ctx = _ctx(sample_config_dict, release_check=True)
    client = _authed_client(ctx, ["control"])

    resp = client.get("/system/version")
    assert resp.status_code == 200
    data = resp.json()
    assert data["latest"] is None
    assert data["update_available"] is False


def test_version_endpoint_skips_check_on_remote_backend(
    sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remote backend never phones GitHub (the operator can't self-update it)."""

    async def _unexpected(url: str, *, config: Any) -> dict[str, Any]:
        raise AssertionError("remote backend must not fetch releases")

    monkeypatch.setattr("jentic_one.shared.release_check.fetch_json", _unexpected)

    cfg = dict(sample_config_dict)
    cfg["release_check"] = {"enabled": True}
    cfg["server"] = {**cfg.get("server", {}), "backend": "remote"}
    ctx = Context(AppConfig.model_validate(cfg))
    client = _authed_client(ctx, ["control"])

    data = client.get("/system/version").json()
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
