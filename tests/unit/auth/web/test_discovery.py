"""Unit tests for OAuth discovery and JWKS endpoints."""

from __future__ import annotations

import collections.abc
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.auth.web.routers import discovery
from jentic_one.shared.config import AuthConfig


@pytest.fixture()
def _make_client() -> collections.abc.Callable[[str], TestClient]:
    def _factory(canonical_base_url: str = "https://auth.example.com") -> TestClient:
        app = FastAPI()
        app.include_router(discovery.router)

        mock_ctx = MagicMock()
        mock_ctx.config.auth = AuthConfig(canonical_base_url=canonical_base_url)
        # effective_auth_base_url also reads server.public_base_url; keep it
        # empty so the canonical/request-fallback behaviour under test holds.
        mock_ctx.config.server.public_base_url = ""
        app.state.ctx = mock_ctx
        return TestClient(app)

    return _factory


@pytest.fixture()
def client(_make_client: collections.abc.Callable[[str], TestClient]) -> TestClient:
    return _make_client("https://auth.example.com")


def test_discovery_returns_200(client: TestClient) -> None:
    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200


def test_discovery_issuer_matches_canonical_base_url(client: TestClient) -> None:
    resp = client.get("/.well-known/oauth-authorization-server")
    data = resp.json()
    assert data["issuer"] == "https://auth.example.com"


def test_discovery_contains_required_endpoints(client: TestClient) -> None:
    resp = client.get("/.well-known/oauth-authorization-server")
    data = resp.json()
    issuer = "https://auth.example.com"
    assert data["token_endpoint"] == f"{issuer}/oauth/token"
    assert data["registration_endpoint"] == f"{issuer}/register"
    assert data["revocation_endpoint"] == f"{issuer}/oauth/revoke"
    assert data["introspection_endpoint"] == f"{issuer}/oauth/introspect"
    assert data["jwks_uri"] == f"{issuer}/.well-known/jwks.json"


def test_discovery_contains_grant_types_and_auth_methods(client: TestClient) -> None:
    resp = client.get("/.well-known/oauth-authorization-server")
    data = resp.json()
    assert "urn:ietf:params:oauth:grant-type:jwt-bearer" in data["grant_types_supported"]
    assert "refresh_token" in data["grant_types_supported"]
    assert "private_key_jwt" in data["token_endpoint_auth_methods_supported"]


def test_discovery_response_types_supported_includes_code(client: TestClient) -> None:
    resp = client.get("/.well-known/oauth-authorization-server")
    data = resp.json()
    assert "code" in data["response_types_supported"]


def test_discovery_ignores_spoofed_host_header(client: TestClient) -> None:
    resp = client.get("/.well-known/oauth-authorization-server", headers={"Host": "evil.com"})
    data = resp.json()
    assert data["issuer"] == "https://auth.example.com"


def test_discovery_falls_back_to_request_host_when_canonical_empty(
    _make_client: collections.abc.Callable[[str], TestClient],
) -> None:
    client = _make_client("")
    resp = client.get("/.well-known/oauth-authorization-server")
    data = resp.json()
    assert data["issuer"] == "http://testserver"


def test_discovery_no_auth_required(client: TestClient) -> None:
    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200


def test_jwks_returns_200(client: TestClient) -> None:
    resp = client.get("/.well-known/jwks.json")
    assert resp.status_code == 200


def test_jwks_returns_empty_keys(client: TestClient) -> None:
    resp = client.get("/.well-known/jwks.json")
    assert resp.json() == {"keys": []}


def test_jwks_content_type_is_json(client: TestClient) -> None:
    resp = client.get("/.well-known/jwks.json")
    assert "application/json" in resp.headers["content-type"]


def test_jwks_no_auth_required(client: TestClient) -> None:
    resp = client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
