"""Tests for the public agent-discovery documents (onboarding skill + llms.txt).

Covers ``GET /skills/jentic.md`` / ``GET /SKILL.md`` (#651) and
``GET /llms.txt`` / ``GET /.well-known/llms.txt`` (#809): content, content
type, base-URL resolution, schema hiding, and availability on both the
combined app and standalone surface apps.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from jentic_one.__main__ import _expand_allowed_dbs
from jentic_one.auth.web.app import create_app as create_auth_app
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.agent_discovery import (
    LLMS_TXT_PATH,
    LLMS_TXT_WELL_KNOWN_PATH,
    SKILL_ALIAS_PATH,
    SKILL_PATH,
    load_skill_markdown,
    render_llms_txt,
)
from jentic_one.shared.web.app_factory import create_combined_app


@pytest.fixture()
def app_config(sample_config_dict: dict[str, Any]) -> AppConfig:
    return AppConfig.model_validate(sample_config_dict)


@pytest.fixture()
def ctx(app_config: AppConfig) -> Context:
    return Context(app_config)


@pytest.fixture()
def client(ctx: Context) -> TestClient:
    app = create_combined_app(ctx, ["registry", "admin", "control", "auth"])
    return TestClient(app, raise_server_exceptions=False)


def test_skill_served_as_markdown(client: TestClient) -> None:
    """GET /skills/jentic.md returns the onboarding skill as markdown."""
    resp = client.get(SKILL_PATH)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
    assert resp.text == load_skill_markdown()
    assert "# Using Jentic from the CLI" in resp.text


def test_skill_alias_serves_same_content(client: TestClient) -> None:
    """GET /SKILL.md is an alias for the skill document."""
    resp = client.get(SKILL_ALIAS_PATH)
    assert resp.status_code == 200
    assert resp.text == client.get(SKILL_PATH).text


def test_llms_txt_served_with_request_base_url(client: TestClient) -> None:
    """GET /llms.txt links every discovery document under the request base URL."""
    resp = client.get(LLMS_TXT_PATH)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
    body = resp.text
    assert body.startswith("# Jentic One")
    # TestClient's base URL is http://testserver.
    for path in (
        "/skills/jentic.md",
        "/openapi.json",
        "/reference/endpoints.json",
        "/.well-known/oauth-authorization-server",
        "/register",
        "/oauth/token",
        "/access-requests",
    ):
        assert f"http://testserver{path}" in body, f"llms.txt missing link to {path}"


def test_llms_txt_well_known_alias(client: TestClient) -> None:
    """GET /.well-known/llms.txt serves the same document as /llms.txt."""
    resp = client.get(LLMS_TXT_WELL_KNOWN_PATH)
    assert resp.status_code == 200
    assert resp.text == client.get(LLMS_TXT_PATH).text


def test_llms_txt_honors_canonical_base_url(sample_config_dict: dict[str, Any]) -> None:
    """A configured auth.canonical_base_url overrides the request base URL."""
    config_dict = dict(sample_config_dict)
    config_dict["auth"] = {
        **config_dict.get("auth", {}),
        "canonical_base_url": "https://jentic.example.com/",
    }
    ctx = Context(AppConfig.model_validate(config_dict))
    app = create_combined_app(ctx, ["control"])
    client = TestClient(app, raise_server_exceptions=False)
    body = client.get(LLMS_TXT_PATH).text
    assert "https://jentic.example.com/skills/jentic.md" in body
    assert "http://testserver" not in body


def test_discovery_documents_hidden_from_schema(ctx: Context) -> None:
    """Onboarding documents are tooling metadata, kept out of the OpenAPI spec."""
    app = create_combined_app(ctx, ["control"])
    paths = app.openapi()["paths"]
    for path in (SKILL_PATH, SKILL_ALIAS_PATH, LLMS_TXT_PATH, LLMS_TXT_WELL_KNOWN_PATH):
        assert path not in paths


def test_standalone_surface_serves_discovery_documents(ctx: Context) -> None:
    """Standalone surface apps serve the documents too (split deployments)."""
    app = create_auth_app(ctx)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get(SKILL_PATH).status_code == 200
    assert client.get(LLMS_TXT_PATH).status_code == 200


def test_broker_catch_all_does_not_shadow_discovery_documents(
    app_config: AppConfig,
) -> None:
    """The broker's /{upstream_url:path} proxy must not swallow the documents.

    The discovery router is registered before the surface routers precisely so
    the broker's auth-gated catch-all cannot shadow these literal paths.
    """
    broker_ctx = Context(app_config, allowed_dbs=_expand_allowed_dbs(["broker"]))
    app = create_broker_app(broker_ctx)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(SKILL_PATH)
    assert resp.status_code == 200
    assert resp.text == load_skill_markdown()
    assert client.get(LLMS_TXT_PATH).status_code == 200


def test_llms_txt_quickstart_matches_backend_contract(client: TestClient) -> None:
    """Served instructions an LLM follows verbatim must match the backend.

    Regression guard for review findings: the poll loop must key on the real
    ``active`` status (there is no ``approved``), and the token call must be
    described as JSON (the endpoint 422s a form-encoded body).
    """
    body = client.get(LLMS_TXT_PATH).text
    assert "`active`" in body
    assert "approved" not in body  # the status value is `active`, never `approved`
    assert "JSON body" in body
    assert "unique `jti`" in body


def test_render_llms_txt_stamps_base_everywhere() -> None:
    """No hardcoded host survives rendering; every link uses the given base."""
    body = render_llms_txt("https://example.test")
    assert "https://example.test/skills/jentic.md" in body
    assert "{base}" not in body
    assert "testserver" not in body
