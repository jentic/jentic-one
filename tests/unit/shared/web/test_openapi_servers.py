"""The OpenAPI ``servers`` block: live deployment origin vs the published artefact.

The live ``/openapi.json`` advertises ``server.public_base_url`` when the
operator sets it (falling back to the same-origin relative server), while the
checked-in artefact always carries the placeholder hosts.
"""

from __future__ import annotations

import pytest
from tools.openapi_export import (
    CONTROL_PLANE_SURFACES,
    DEFAULT_CONFIG,
    build_control_plane_spec,
    published_spec,
)

from jentic_one.shared.config import AppConfig, load_config
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app, create_surface_app
from jentic_one.shared.web.openapi_meta import (
    PUBLISHED_SERVERS,
    SERVERS,
    deployment_servers,
    fastapi_metadata_kwargs,
)


def test_deployment_servers_falls_back_to_same_origin() -> None:
    assert deployment_servers("") == SERVERS
    assert fastapi_metadata_kwargs()["servers"] == SERVERS


def test_deployment_servers_uses_public_base_url() -> None:
    servers = deployment_servers("https://jentic.acme.test")
    assert servers == [{"url": "https://jentic.acme.test", "description": "This deployment"}]


_PUBLIC = "https://jentic.acme.test"


def _config(monkeypatch: pytest.MonkeyPatch, public_base_url: str) -> AppConfig:
    monkeypatch.setenv("JENTIC_CONFIG_FILE", str(DEFAULT_CONFIG))
    config = load_config()
    return config.model_copy(
        update={"server": config.server.model_copy(update={"public_base_url": public_base_url})}
    )


@pytest.mark.parametrize(("public_base_url", "expected_url"), [("", "/"), (_PUBLIC, _PUBLIC)])
def test_live_spec_servers_follow_config(
    monkeypatch: pytest.MonkeyPatch, public_base_url: str, expected_url: str
) -> None:
    ctx = Context(
        _config(monkeypatch, public_base_url), allowed_dbs={"registry", "admin", "control"}
    )
    app = create_combined_app(ctx, list(CONTROL_PLANE_SURFACES))
    assert [s["url"] for s in app.openapi()["servers"]] == [expected_url]


def test_standalone_broker_keeps_same_origin_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """``public_base_url`` names the control plane's origin, not a split broker's."""
    ctx = Context(_config(monkeypatch, _PUBLIC), allowed_dbs={"control"})
    app = create_surface_app(ctx, title="broker", routers=[], enabled_apps={"broker"})
    assert app.openapi()["servers"] == SERVERS


def test_exported_specs_pin_their_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The UI codegen input stays same-origin; only the YAML artefact is published."""
    monkeypatch.setenv("JENTIC_CONFIG_FILE", str(DEFAULT_CONFIG))
    spec = build_control_plane_spec()
    assert spec["servers"] == SERVERS
    assert published_spec(spec)["servers"] == PUBLISHED_SERVERS
