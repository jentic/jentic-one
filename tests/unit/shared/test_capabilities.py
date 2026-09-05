"""Unit tests for the public capability document (``GET /capabilities``, #1279).

One consolidated, unauthenticated deployment self-description (auth methods,
broker URL, surface composition, feature flags) so a client can onboard from a
single URL instead of probe-and-guess across ``/instance``, ``/auth/idp``, and
the RFC 8414 document. Config-only (no DB), so these run as fast units.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from fastapi.testclient import TestClient

import jentic_one.shared.web.capabilities as capabilities_module
from jentic_one import __version__
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.control.web.app import create_app as create_control_app
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app
from jentic_one.shared.web.capabilities import (
    register_capability_contributor,
    resolve_capabilities,
)

_ALL_APPS = ["admin", "auth", "control", "registry"]


def _ctx(sample_config_dict: dict[str, Any], **overrides: Any) -> Context:
    """Context with ``auth``/``server`` sections deep-merged over the sample config."""
    cfg = dict(sample_config_dict)
    cfg["auth"] = {
        **cfg.get("auth", {}),
        "canonical_base_url": "http://127.0.0.1:8000",
        **overrides.get("auth", {}),
    }
    if "server" in overrides:
        cfg["server"] = {**cfg.get("server", {}), **overrides["server"]}
    return Context(AppConfig.model_validate(cfg))


@pytest.fixture(autouse=True)
def _isolated_contributor_registry() -> Iterator[None]:
    """Snapshot/restore the process-global contributor registry around each test."""
    saved = list(capabilities_module._capability_contributors)
    yield
    capabilities_module._capability_contributors[:] = saved


def test_capabilities_default_body_is_golden(sample_config_dict: dict[str, Any]) -> None:
    """Byte-shape golden of the default document (regression pin).

    The document is a client contract (login pickers and onboarding flows parse
    it), so any change to the default body must be deliberate — update this pin
    and bump ``capabilities_version`` if the shape changed incompatibly.
    """
    ctx = _ctx(sample_config_dict)
    client = TestClient(create_combined_app(ctx, _ALL_APPS), raise_server_exceptions=False)

    resp = client.get("/capabilities")

    assert resp.status_code == 200
    assert resp.json() == {
        "instance": {
            "backend": "local",
            "canonical_base_url": "http://127.0.0.1:8000",
        },
        "surfaces": ["admin", "auth", "control", "registry"],
        "urls": {
            "broker": "http://127.0.0.1:8100",
            "authorization_server_metadata": "/.well-known/oauth-authorization-server",
        },
        "auth": {
            "methods": {
                "idp": {"enabled": False, "provider": None},
                "local_login": {"enabled": False},
                "oauth_client_dcr": {"enabled": False, "approval": "manual"},
                "agent_dcr": {"enabled": True},
                "service_accounts": {"enabled": True},
            }
        },
        "features": {"mcp": False},
        "capabilities_version": 1,
    }


def test_capabilities_is_unauthenticated(sample_config_dict: dict[str, Any]) -> None:
    """No Authorization header required — it is the public onboarding document."""
    client = TestClient(
        create_combined_app(_ctx(sample_config_dict), _ALL_APPS),
        raise_server_exceptions=False,
    )
    assert client.get("/capabilities").status_code == 200


def test_capabilities_reflects_idp_enabled(sample_config_dict: dict[str, Any]) -> None:
    ctx = _ctx(sample_config_dict, auth={"idp": {"enabled": True, "provider": "google"}})
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.auth.methods.idp.enabled is True
    assert doc.auth.methods.idp.provider == "google"


def test_capabilities_hides_provider_when_idp_disabled(
    sample_config_dict: dict[str, Any],
) -> None:
    """Mirrors ``GET /auth/idp``: a disabled IdP never names its provider."""
    ctx = _ctx(sample_config_dict, auth={"idp": {"enabled": False, "provider": "google"}})
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.auth.methods.idp.enabled is False
    assert doc.auth.methods.idp.provider is None


def test_capabilities_reflects_mcp_flags(sample_config_dict: dict[str, Any]) -> None:
    ctx = _ctx(
        sample_config_dict,
        server={"mcp": {"enabled": True, "oauth": {"enabled": True, "auto_approve_clients": True}}},
    )
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.features["mcp"] is True
    assert doc.auth.methods.oauth_client_dcr.enabled is True
    assert doc.auth.methods.oauth_client_dcr.approval == "auto"


def test_capabilities_reflects_surface_composition(sample_config_dict: dict[str, Any]) -> None:
    """Without the auth surface there is no login door to advertise."""
    ctx = _ctx(sample_config_dict)
    doc = resolve_capabilities(ctx, ["registry", "control"])
    assert doc.surfaces == ["control", "registry"]  # sorted
    assert doc.urls.authorization_server_metadata is None
    assert doc.auth.methods.agent_dcr.enabled is False
    assert doc.auth.methods.service_accounts.enabled is False


def test_capabilities_broker_url_prefers_advertised_key(
    sample_config_dict: dict[str, Any],
) -> None:
    ctx = _ctx(
        sample_config_dict,
        server={"advertised_broker_url": "https://broker.jentic.example"},
    )
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.urls.broker == "https://broker.jentic.example"


def test_capabilities_broker_url_falls_back_to_mcp_hop(
    sample_config_dict: dict[str, Any],
) -> None:
    """Unset advertised key → the deployment's own broker-hop URL (local topology)."""
    doc = resolve_capabilities(_ctx(sample_config_dict), _ALL_APPS)
    assert doc.urls.broker == "http://127.0.0.1:8100"


def test_capabilities_broker_url_strips_userinfo(sample_config_dict: dict[str, Any]) -> None:
    """Credentials embedded in a configured broker URL are never published."""
    ctx = _ctx(
        sample_config_dict,
        server={"advertised_broker_url": "https://user:secret@broker.jentic.example:8443"},
    )
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.urls.broker == "https://broker.jentic.example:8443"
    assert "secret" not in (doc.urls.broker or "")


def test_capabilities_instance_slice_strips_userinfo(
    sample_config_dict: dict[str, Any],
) -> None:
    cfg = dict(sample_config_dict)
    cfg["auth"] = {
        **cfg.get("auth", {}),
        "canonical_base_url": "https://user:secret@jentic.acme.example",
    }
    ctx = Context(AppConfig.model_validate(cfg))
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.instance.canonical_base_url == "https://jentic.acme.example"


def test_contributor_extends_features(sample_config_dict: dict[str, Any]) -> None:
    """A registered contributor's keys surface in ``features`` (the seam contract)."""

    def contribute(ctx: Context) -> Mapping[str, object]:
        return {"acme_sso": True}

    register_capability_contributor(contribute)
    doc = resolve_capabilities(_ctx(sample_config_dict), _ALL_APPS)
    assert doc.features == {"mcp": False, "acme_sso": True}


def test_contributor_cannot_override_built_in_keys(sample_config_dict: dict[str, Any]) -> None:
    """First writer wins: a contribution never rewrites a built-in flag's meaning."""

    def contribute(ctx: Context) -> Mapping[str, object]:
        return {"mcp": True, "acme_extra": 1}

    register_capability_contributor(contribute)
    doc = resolve_capabilities(_ctx(sample_config_dict), _ALL_APPS)
    assert doc.features["mcp"] is False  # built-in value preserved
    assert doc.features["acme_extra"] == 1  # non-colliding key still lands


def test_contributors_merge_in_registration_order(sample_config_dict: dict[str, Any]) -> None:
    register_capability_contributor(lambda ctx: {"shared_key": "first"})
    register_capability_contributor(lambda ctx: {"shared_key": "second"})
    doc = resolve_capabilities(_ctx(sample_config_dict), _ALL_APPS)
    assert doc.features["shared_key"] == "first"


def test_capabilities_present_on_standalone_surface(sample_config_dict: dict[str, Any]) -> None:
    """Mounted in standalone surface apps too — with that surface's composition."""
    client = TestClient(create_control_app(_ctx(sample_config_dict)), raise_server_exceptions=False)
    resp = client.get("/capabilities")
    assert resp.status_code == 200
    assert resp.json()["surfaces"] == ["control"]


def test_capabilities_not_mounted_on_broker(sample_config_dict: dict[str, Any]) -> None:
    """The broker data plane opts out exactly as it does for ``/instance``."""
    client = TestClient(create_broker_app(_ctx(sample_config_dict)), raise_server_exceptions=False)
    resp = client.get("/capabilities")
    assert resp.status_code != 200
    assert "capabilities_version" not in resp.text


def test_capabilities_never_publishes_a_version_string(
    sample_config_dict: dict[str, Any],
) -> None:
    """ASVS fingerprinting posture: no exact version (that stays behind auth)."""
    client = TestClient(
        create_combined_app(_ctx(sample_config_dict), _ALL_APPS),
        raise_server_exceptions=False,
    )
    assert __version__ not in client.get("/capabilities").text


def test_capabilities_schema_visible_and_public(sample_config_dict: dict[str, Any]) -> None:
    """It is a real (schema-visible) route stamped public (no BearerAuth)."""
    app = create_combined_app(_ctx(sample_config_dict), _ALL_APPS)
    op = app.openapi()["paths"]["/capabilities"]["get"]
    assert op["security"] == []
    assert op["tags"] == ["Discovery"]
