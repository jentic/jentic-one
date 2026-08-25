"""Tests for combined and individual app construction."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from jentic_one import __version__
from jentic_one.__main__ import _expand_allowed_dbs
from jentic_one.auth.web.app import create_app as create_auth_app
from jentic_one.control.web.app import create_app as create_control_app
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import SURFACE_MODULES, create_combined_app


@pytest.fixture()
def app_config(sample_config_dict: dict[str, Any]) -> AppConfig:
    return AppConfig.model_validate(sample_config_dict)


@pytest.fixture()
def ctx(app_config: AppConfig) -> Context:
    return Context(app_config)


def test_combined_app_mounts_all_surfaces(ctx: Context) -> None:
    app = create_combined_app(ctx, ["registry", "admin", "control"])
    client = TestClient(app, raise_server_exceptions=False)
    for surface in ("registry", "admin", "control"):
        resp = client.get(f"/{surface}/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["surface"] == surface


def test_combined_app_subset_only_mounts_requested(ctx: Context) -> None:
    app = create_combined_app(ctx, ["control"])
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/control/health")
    assert resp.status_code == 200
    assert resp.json()["surface"] == "control"

    resp = client.get("/registry/health")
    assert resp.status_code == 404

    resp = client.get("/admin/health")
    assert resp.status_code == 404


def test_combined_app_two_surfaces(ctx: Context) -> None:
    app = create_combined_app(ctx, ["registry", "admin"])
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/registry/health").status_code == 200
    assert client.get("/admin/health").status_code == 200
    assert client.get("/control/health").status_code == 404


def test_single_surface_app_factory(ctx: Context) -> None:
    app = create_control_app(ctx)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "surface": "control", "version": __version__}


def test_context_scoped_to_allowed_dbs(app_config: AppConfig) -> None:
    scoped_ctx = Context(app_config, allowed_dbs={"control"})
    app = create_combined_app(scoped_ctx, ["control"])
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/control/health").status_code == 200
    with pytest.raises(RuntimeError, match="not allowed"):
        _ = scoped_ctx.registry_db


def test_combined_app_unified_openapi_spec(ctx: Context) -> None:
    app = create_combined_app(ctx, ["registry", "admin", "control"])
    spec = app.openapi()
    paths = spec["paths"]
    assert "/registry/health" in paths
    assert "/admin/health" in paths
    assert "/control/health" in paths


def test_auth_surface_in_surface_modules() -> None:
    assert "auth" in SURFACE_MODULES
    assert SURFACE_MODULES["auth"] == "jentic_one.auth.web.app"


def test_combined_app_mounts_auth(ctx: Context) -> None:
    app = create_combined_app(ctx, ["admin", "auth"])
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/auth/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "surface": "auth", "version": __version__}


def test_sso_callback_not_shadowed_by_credentials_connect_callback(ctx: Context) -> None:
    """The SSO login callback must resolve to its own root path, not /credentials.

    The auth surface's login callback and the control surface's credential-connect
    callback are both Python functions named ``oauth_callback``. ``/authorize``
    builds its redirect_uri via ``url_path_for``, so the SSO route carries a unique
    name (``authorize_oauth_callback``) to avoid the connect route winning the
    name lookup and sending Google's login code to the wrong handler.
    """
    app = create_combined_app(ctx, ["control", "auth"])
    # SSO login callback resolves to the root path...
    assert app.url_path_for("authorize_oauth_callback") == "/oauth/callback"
    # ...distinct from the credentials-connect callback, which is unchanged.
    assert app.url_path_for("oauth_callback") == "/credentials/oauth/callback"


def test_auth_standalone_app(ctx: Context) -> None:
    app = create_auth_app(ctx)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "surface": "auth", "version": __version__}


def test_auth_surface_gets_admin_db_access(app_config: AppConfig) -> None:
    allowed = _expand_allowed_dbs(["auth"])
    assert "admin" in allowed
    ctx = Context(app_config, allowed_dbs=allowed)
    _ = ctx.admin_db


def test_broker_surface_gets_admin_db_access(app_config: AppConfig) -> None:
    allowed = _expand_allowed_dbs(["broker"])
    assert "admin" in allowed
    ctx = Context(app_config, allowed_dbs=allowed)
    _ = ctx.admin_db


def test_reference_endpoint_serves_scope_join(ctx: Context) -> None:
    """GET /reference/endpoints.json serves the canonical scope reference."""
    app = create_combined_app(ctx, ["registry", "admin", "control", "auth"])
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/reference/endpoints.json")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["schema"] == "jentic.endpoint-scope-tree/v1"
    assert payload["total"] == len(payload["endpoints"])
    assert payload["total"] > 10
    # Every row carries the fields the CLI / docs SPA need.
    for ep in payload["endpoints"]:
        assert {"method", "path", "public", "required_scopes", "group"} <= ep.keys()


def test_reference_endpoint_hidden_from_schema(ctx: Context) -> None:
    """The reference endpoint is tooling metadata, kept out of the OpenAPI spec."""
    app = create_combined_app(ctx, ["control"])
    assert "/reference/endpoints.json" not in app.openapi()["paths"]


# ── Anonymous HTML navigations that 401 land in the SPA (#813) ──────────────


def _html_headers() -> dict[str, str]:
    # What a browser address-bar navigation sends: HTML preferred, no credential.
    return {"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}


def test_anonymous_html_get_401_redirects_to_spa(ctx: Context) -> None:
    """An anonymous browser navigation to a protected URL lands in the SPA."""
    app = create_combined_app(ctx, ["admin"])
    app.state.spa_mounted = True  # Simulate a bundled UI (tests ship no bundle).
    client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    resp = client.get("/users", headers=_html_headers())
    assert resp.status_code == 302
    assert resp.headers["location"] == "/app/"


def test_json_get_401_keeps_problem_details(ctx: Context) -> None:
    """API clients (JSON Accept) still get the RFC 9457 body, never a redirect."""
    app = create_combined_app(ctx, ["admin"])
    app.state.spa_mounted = True
    client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    resp = client.get("/users", headers={"Accept": "application/json"})
    assert resp.status_code == 401
    assert resp.json()["status"] == 401


def test_html_get_401_with_credential_keeps_problem_details(ctx: Context) -> None:
    """A request that presented a (bad) credential is an API call, not a navigation."""
    app = create_combined_app(ctx, ["admin"])
    app.state.spa_mounted = True
    client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    resp = client.get(
        "/users", headers={**_html_headers(), "Authorization": "Bearer not-a-valid-token"}
    )
    assert resp.status_code == 401


def test_html_get_401_with_api_key_keeps_problem_details(ctx: Context) -> None:
    """The API-key header counts as a credential too — no redirect."""
    app = create_combined_app(ctx, ["admin"])
    app.state.spa_mounted = True
    client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    resp = client.get("/users", headers={**_html_headers(), "x-jentic-api-key": "not-a-key"})
    assert resp.status_code == 401


def test_html_post_401_keeps_problem_details(ctx: Context) -> None:
    """Only GET navigations redirect — non-GETs keep the JSON error."""
    app = create_combined_app(ctx, ["admin"])
    app.state.spa_mounted = True
    client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    resp = client.post("/users", headers=_html_headers(), json={})
    assert resp.status_code == 401


def test_html_get_401_without_spa_keeps_problem_details(ctx: Context) -> None:
    """Headless deployments (no UI bundle) never redirect."""
    app = create_combined_app(ctx, ["admin"])
    # Force headless: a developer's local `ui/dist` build would otherwise get
    # auto-mounted and flip this test into the redirect path (mirror of the
    # `spa_mounted = True` forcing in the redirect tests above).
    app.state.spa_mounted = False
    client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    resp = client.get("/users", headers=_html_headers())
    assert resp.status_code == 401
