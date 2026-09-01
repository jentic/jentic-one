"""Unit tests for the /mcp-scoped OAuth discovery surface (phase-3a §4.7, D10).

Covers both arms of the ``server.mcp.oauth.enabled`` gate, the D10 document
shapes, the ``/mcp`` 401 challenge, the RFC 9728 → RFC 8414 discovery chain,
and the acceptance-critical regression pin: the **root** RFC 8414 document
stays byte-identical (the agent ``/register`` remains its advertised DCR
endpoint).
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.auth.web import app as auth_app
from jentic_one.auth.web.routers import discovery
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.scopes import MCP_TOOL_SCOPES

_BASE = "https://auth.example.com"

_MCP_DOC_PATHS = (
    "/.well-known/oauth-authorization-server/mcp",
    "/.well-known/oauth-protected-resource/mcp",
    "/.well-known/oauth-protected-resource",
)

#: Byte-identical golden of the ROOT RFC 8414 document (§9 regression pin):
#: the agent ``/register`` stays the advertised agent-DCR endpoint and nothing
#: the /mcp-scoped documents add may reshape this body.
_ROOT_AS_GOLDEN = (
    b'{"issuer":"https://auth.example.com",'
    b'"authorization_endpoint":"https://auth.example.com/authorize",'
    b'"token_endpoint":"https://auth.example.com/oauth/token",'
    b'"registration_endpoint":"https://auth.example.com/register",'
    b'"revocation_endpoint":"https://auth.example.com/oauth/revoke",'
    b'"introspection_endpoint":"https://auth.example.com/oauth/introspect",'
    b'"jwks_uri":"https://auth.example.com/.well-known/jwks.json",'
    b'"grant_types_supported":["authorization_code",'
    b'"urn:ietf:params:oauth:grant-type:jwt-bearer","refresh_token",'
    b'"client_credentials"],'
    b'"token_endpoint_auth_methods_supported":["private_key_jwt",'
    b'"client_secret_basic","client_secret_post","none"],'
    b'"response_types_supported":["code"],'
    b'"code_challenge_methods_supported":["S256"],'
    b'"id_token_signing_alg_values_supported":["ES256"],'
    b'"token_endpoint_auth_signing_alg_values_supported":["EdDSA"]}'
)


def _make_client(
    *, oauth_enabled: bool = True, canonical_base_url: str = _BASE, full_wiring: bool = False
) -> TestClient:
    app = FastAPI()
    if full_wiring:
        # The real auth-surface router set, in its real order — proves the
        # mcp_router is actually wired (not just unit-testable in isolation).
        for router, prefix, _tags in auth_app.get_routers():
            app.include_router(router, prefix=prefix)
    else:
        app.include_router(discovery.router)
        app.include_router(discovery.mcp_router)

    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(canonical_base_url=canonical_base_url)
    server = ServerConfig()
    server.mcp.oauth.enabled = oauth_enabled
    mock_ctx.config.server = server
    app.state.ctx = mock_ctx
    return TestClient(app)


@pytest.fixture()
def enabled_client() -> TestClient:
    return _make_client(oauth_enabled=True)


@pytest.fixture()
def disabled_client() -> TestClient:
    return _make_client(oauth_enabled=False)


# --- the disabled arm: indistinguishable from not-shipped -------------------


@pytest.mark.parametrize("path", _MCP_DOC_PATHS)
def test_disabled_mcp_discovery_docs_are_plain_404(disabled_client: TestClient, path: str) -> None:
    """server.mcp.oauth.enabled=false → the framework's own route-not-found."""
    resp = disabled_client.get(path)
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_disabled_mcp_probe_is_plain_404_without_challenge(
    disabled_client: TestClient, method: str
) -> None:
    """When off, /mcp keeps today's behaviour: 404 and no WWW-Authenticate."""
    resp = disabled_client.request(method, "/mcp")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
    assert "www-authenticate" not in resp.headers


# --- the /mcp-scoped RFC 8414 document (D10) --------------------------------


def test_mcp_as_document_matches_d10_exactly(enabled_client: TestClient) -> None:
    resp = enabled_client.get("/.well-known/oauth-authorization-server/mcp")
    assert resp.status_code == 200
    assert resp.json() == {
        "issuer": f"{_BASE}/mcp",
        "authorization_endpoint": f"{_BASE}/authorize",
        "token_endpoint": f"{_BASE}/oauth/token",
        "registration_endpoint": f"{_BASE}/oauth-clients",
        "revocation_endpoint": f"{_BASE}/oauth/revoke",
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": sorted(MCP_TOOL_SCOPES),
    }


def test_mcp_as_document_advertises_only_the_public_client_profile(
    enabled_client: TestClient,
) -> None:
    """No CIMD flag yet (§6 follow-on), no confidential/JWT profiles, and the
    registration endpoint is the OAuth-client door — never the agent /register."""
    data = enabled_client.get("/.well-known/oauth-authorization-server/mcp").json()
    assert "client_id_metadata_document_supported" not in data
    assert data["token_endpoint_auth_methods_supported"] == ["none"]
    assert "urn:ietf:params:oauth:grant-type:jwt-bearer" not in data["grant_types_supported"]
    assert "client_credentials" not in data["grant_types_supported"]
    assert data["registration_endpoint"] != f"{_BASE}/register"


# --- the RFC 9728 protected-resource documents ------------------------------


def test_prm_document_matches_d10_exactly(enabled_client: TestClient) -> None:
    resp = enabled_client.get("/.well-known/oauth-protected-resource/mcp")
    assert resp.status_code == 200
    assert resp.json() == {
        "resource": f"{_BASE}/mcp",
        "authorization_servers": [f"{_BASE}/mcp"],
        "scopes_supported": sorted(MCP_TOOL_SCOPES),
        "bearer_methods_supported": ["header"],
    }


def test_root_prm_alias_serves_the_same_body(enabled_client: TestClient) -> None:
    """claude-code#58802 fallback: root and path-scoped PRM docs are one body."""
    scoped = enabled_client.get("/.well-known/oauth-protected-resource/mcp")
    root = enabled_client.get("/.well-known/oauth-protected-resource")
    assert root.status_code == 200
    assert root.content == scoped.content


@pytest.mark.parametrize("path", _MCP_DOC_PATHS)
def test_mcp_docs_are_json_like_existing_well_known_endpoints(
    enabled_client: TestClient, path: str
) -> None:
    """Header posture matches the root discovery docs: plain application/json,
    no auth challenge, no bespoke CORS/cache headers."""
    resp = enabled_client.get(path)
    root = enabled_client.get("/.well-known/oauth-authorization-server")
    assert "application/json" in resp.headers["content-type"]
    assert "www-authenticate" not in resp.headers
    assert ("access-control-allow-origin" in resp.headers) == (
        "access-control-allow-origin" in root.headers
    )
    assert ("cache-control" in resp.headers) == ("cache-control" in root.headers)


# --- the /mcp 401 challenge + discovery chain -------------------------------


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_mcp_probe_answers_401_with_resource_metadata(
    enabled_client: TestClient, method: str
) -> None:
    resp = enabled_client.request(method, "/mcp")
    assert resp.status_code == 401
    assert (
        resp.headers["www-authenticate"]
        == f'Bearer resource_metadata="{_BASE}/.well-known/oauth-protected-resource/mcp"'
    )


def test_discovery_chain_e2e_from_unauthenticated_probe() -> None:
    """§9 chain: unauthenticated /mcp → 401 resource_metadata → PRM → AS doc.

    Follows the pointers the way a spec-following MCP client does (RFC 9728
    then RFC 8414 §3.1 path insertion for the path-scoped issuer), against the
    real auth-surface wiring (get_routers order).
    """
    client = _make_client(oauth_enabled=True, full_wiring=True)

    probe = client.post("/mcp")
    assert probe.status_code == 401
    match = re.search(r'resource_metadata="([^"]+)"', probe.headers["www-authenticate"])
    assert match is not None
    prm_url = match.group(1)
    assert prm_url.startswith(_BASE)

    prm = client.get(prm_url.removeprefix(_BASE))
    assert prm.status_code == 200
    prm_doc = prm.json()
    assert prm_doc["resource"] == f"{_BASE}/mcp"
    (authorization_server,) = prm_doc["authorization_servers"]
    assert authorization_server == f"{_BASE}/mcp"

    # RFC 8414 §3.1: insert the well-known segment between host and path.
    issuer_path = authorization_server.removeprefix(_BASE)
    as_doc = client.get(f"/.well-known/oauth-authorization-server{issuer_path}")
    assert as_doc.status_code == 200
    data = as_doc.json()
    assert data["issuer"] == authorization_server
    assert data["registration_endpoint"] == f"{_BASE}/oauth-clients"
    assert data["token_endpoint_auth_methods_supported"] == ["none"]
    assert data["code_challenge_methods_supported"] == ["S256"]


# --- base-URL posture (same rules as the root discovery doc) ----------------


def test_mcp_docs_ignore_spoofed_host_header(enabled_client: TestClient) -> None:
    resp = enabled_client.get(
        "/.well-known/oauth-authorization-server/mcp", headers={"Host": "evil.com"}
    )
    assert resp.json()["issuer"] == f"{_BASE}/mcp"


def test_mcp_docs_fall_back_to_request_host_when_canonical_empty() -> None:
    client = _make_client(oauth_enabled=True, canonical_base_url="")
    resp = client.get("/.well-known/oauth-protected-resource/mcp")
    assert resp.json()["resource"] == "http://testserver/mcp"
    probe = client.get("/mcp")
    assert probe.headers["www-authenticate"] == (
        'Bearer resource_metadata="http://testserver/.well-known/oauth-protected-resource/mcp"'
    )


# --- the acceptance-critical root-document regression pins ------------------


@pytest.mark.parametrize("oauth_enabled", [True, False])
def test_root_as_document_is_byte_identical_golden(oauth_enabled: bool) -> None:
    """The ROOT RFC 8414 document must not change in either gate arm (§9):
    its registration_endpoint stays the agent /register, byte for byte."""
    client = _make_client(oauth_enabled=oauth_enabled)
    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    assert resp.content == _ROOT_AS_GOLDEN


def test_root_as_document_still_advertises_agent_register(enabled_client: TestClient) -> None:
    data = enabled_client.get("/.well-known/oauth-authorization-server").json()
    assert data["registration_endpoint"] == f"{_BASE}/register"
