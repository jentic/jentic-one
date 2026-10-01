"""The OpenAPI ``servers`` block: live deployment origin vs the published artefact.

The live ``/openapi.json`` advertises ``server.public_base_url`` when the
operator sets it (falling back to the same-origin relative server), while the
checked-in artefact always carries the placeholder hosts.
"""

from __future__ import annotations

import pytest
from tools.openapi_export import CONTROL_PLANE_SURFACES, DEFAULT_CONFIG, build_control_plane_spec

from jentic_one.shared.config import load_config
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app
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


@pytest.mark.parametrize(
    ("public_base_url", "expected_url"),
    [("", "/"), ("https://jentic.acme.test", "https://jentic.acme.test")],
)
def test_live_spec_servers_follow_config(
    monkeypatch: pytest.MonkeyPatch, public_base_url: str, expected_url: str
) -> None:
    monkeypatch.setenv("JENTIC_CONFIG_FILE", str(DEFAULT_CONFIG))
    config = load_config()
    config = config.model_copy(
        update={"server": config.server.model_copy(update={"public_base_url": public_base_url})}
    )
    ctx = Context(config, allowed_dbs={"registry", "admin", "control"})
    app = create_combined_app(ctx, list(CONTROL_PLANE_SURFACES))
    assert [s["url"] for s in app.openapi()["servers"]] == [expected_url]


def test_published_spec_carries_placeholder_servers() -> None:
    assert build_control_plane_spec()["servers"] == PUBLISHED_SERVERS
