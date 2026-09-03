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
from jentic_one.mcp.installer import install_mcp_challenge_placeholder
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.scopes import MCP_TOOL_SCOPES

_BASE = "https://auth.example.com"

_MCP_DOC_PATHS = (
    "/.well-known/oauth-authorization-server/mcp",
    "/.well-known/oauth-protected-resource/mcp",
    "/.well-known/oauth-protected-resource",
)

#: Every method registered on the /mcp placeholder (the full common set, so
#: the disabled arm answers a uniform 404 with no 405/Allow method tell and
#: the enabled arm challenges before method semantics — review F3/F4).
_MCP_METHODS = ("GET", "POST", "DELETE", "HEAD", "OPTIONS", "PUT", "PATCH")

#: RFC 8414/OIDC discovery variants MCP clients probe (§2) that this surface
#: deliberately does NOT serve: path-appending and OIDC arms must fall through
#: to 404 in both gate arms so clients land on the served path-insertion doc.
_UNSERVED_PROBE_PATHS = (
    "/mcp/.well-known/openid-configuration",
    "/mcp/.well-known/oauth-authorization-server",
    "/.well-known/openid-configuration/mcp",
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
    # Phase 3: an auth surface WITHOUT control (this test app's shape) carries
    # the 3a-4 challenge placeholder — exactly what the composition root
    # (build_default_container) installs on that shape, so these 44 pins test
    # the real wiring: the discovery pointers land on the challenge, never a
    # dangling 404, while the real transport stays control-plane-only (§6 Q1).
    install_mcp_challenge_placeholder(app, mock_ctx)
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


@pytest.mark.parametrize("method", _MCP_METHODS)
def test_disabled_mcp_probe_is_plain_404_without_challenge(
    disabled_client: TestClient, method: str
) -> None:
    """When off, /mcp keeps today's behaviour on every registered method:
    the framework's own 404 body and no WWW-Authenticate."""
    resp = disabled_client.request(method, "/mcp")
    assert resp.status_code == 404
    assert "www-authenticate" not in resp.headers
    if method != "HEAD":
        assert resp.json() == {"detail": "Not Found"}


# --- the /mcp-scoped RFC 8414 document (D10) --------------------------------


def test_mcp_as_document_matches_d10(
    enabled_client: TestClient,
) -> None:
    """The full D10 document shape — including the revocation_endpoint that
    3a-4 had deliberately omitted (G11, closed: /oauth/revoke now carries an
    RFC 7009-conformant public-client arm)."""
    resp = enabled_client.get("/.well-known/oauth-authorization-server/mcp")
    assert resp.status_code == 200
    assert resp.json() == {
        "issuer": f"{_BASE}/mcp",
        "authorization_endpoint": f"{_BASE}/authorize",
        "token_endpoint": f"{_BASE}/oauth/token",
        "registration_endpoint": f"{_BASE}/oauth-clients",
        "revocation_endpoint": f"{_BASE}/oauth/revoke",
        "revocation_endpoint_auth_methods_supported": ["none"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": sorted(MCP_TOOL_SCOPES),
    }


def test_mcp_as_document_advertises_rfc7009_revocation(enabled_client: TestClient) -> None:
    """G11 closed (was: a pinned deliberate omission): the /mcp doc advertises
    ``revocation_endpoint={base}/oauth/revoke`` per D10, now that the endpoint
    accepts the RFC 7009 public-client shape (form-encoded, token + optional
    client_id, auth method ``none``). The auth-methods field must be explicit:
    RFC 8414's implicit default is ``client_secret_basic``, which would
    contradict the advertised ``none`` client profile."""
    data = enabled_client.get("/.well-known/oauth-authorization-server/mcp").json()
    assert data["revocation_endpoint"] == f"{_BASE}/oauth/revoke"
    assert data["revocation_endpoint_auth_methods_supported"] == ["none"]


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


@pytest.mark.parametrize("method", _MCP_METHODS)
def test_mcp_probe_answers_401_with_resource_metadata(
    enabled_client: TestClient, method: str
) -> None:
    """Every registered method challenges: auth precedes method semantics on a
    protected resource (the phase-3 mounted app covers all methods too)."""
    resp = enabled_client.request(method, "/mcp")
    assert resp.status_code == 401
    assert (
        resp.headers["www-authenticate"]
        == f'Bearer resource_metadata="{_BASE}/.well-known/oauth-protected-resource/mcp"'
    )
    if method == "HEAD":
        # RFC 9110 §9.3.2: same status and headers as GET, no body.
        assert resp.content == b""
    else:
        assert resp.json() == {"detail": "Unauthorized"}


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


# --- build-level tells: identical in both gate arms (review F3) -------------
#
# The _McpDiscoveryRoute gate only runs on a full route match, so Starlette
# answers partial matches (405 + Allow), issues the redirect_slashes 307, and
# lists the doc paths in the live OpenAPI schema before the gate can run.
# These reveal BUILD state ("this build ships the routes"), never GATE state —
# they are identical in both arms. Pinned so any change is deliberate.


@pytest.mark.parametrize("path", _MCP_DOC_PATHS)
def test_doc_path_method_tell_is_identical_in_both_arms(path: str) -> None:
    """POST to a GET-only doc path → 405 + Allow, byte-identical across arms."""
    by_arm = {}
    for enabled in (True, False):
        resp = _make_client(oauth_enabled=enabled).post(path)
        by_arm[enabled] = (resp.status_code, resp.headers.get("allow"), resp.content)
    assert by_arm[True] == by_arm[False]
    status, allow, _body = by_arm[True]
    assert status == 405
    assert allow is not None and set(allow.replace(" ", "").split(",")) == {"GET"}


def test_mcp_trailing_slash_redirect_tell_is_identical_in_both_arms() -> None:
    """GET /mcp/ → redirect_slashes 307 to /mcp in both arms (never a 404)."""
    by_arm = {}
    for enabled in (True, False):
        client = _make_client(oauth_enabled=enabled)
        resp = client.get("/mcp/", follow_redirects=False)
        by_arm[enabled] = (resp.status_code, resp.headers.get("location"))
    assert by_arm[True] == by_arm[False]
    status, location = by_arm[True]
    assert status == 307
    assert location is not None and location.endswith("/mcp")


def test_mcp_probe_method_answer_is_uniform_in_the_disabled_arm() -> None:
    """No 405/Allow method tell on /mcp itself: the placeholder registers the
    full common method set, so a disabled deployment answers the same 404 on
    every method instead of leaking an Allow list."""
    client = _make_client(oauth_enabled=False)
    for method in _MCP_METHODS:
        resp = client.request(method, "/mcp")
        assert resp.status_code == 404
        assert "allow" not in resp.headers


@pytest.mark.parametrize("oauth_enabled", [True, False])
def test_doc_paths_listed_in_live_openapi_in_both_arms(oauth_enabled: bool) -> None:
    """The live /openapi.json lists the three doc paths (build-level, like the
    root discovery doc) in both arms; the /mcp placeholder stays schema-hidden."""
    client = _make_client(oauth_enabled=oauth_enabled)
    paths = client.get("/openapi.json").json()["paths"]
    for path in _MCP_DOC_PATHS:
        assert path in paths
    assert "/mcp" not in paths


# --- unserved discovery probe arms fall through to 404 (review F7) ----------


@pytest.mark.parametrize("oauth_enabled", [True, False])
@pytest.mark.parametrize("path", _UNSERVED_PROBE_PATHS)
def test_unserved_discovery_probe_arms_fall_through_404(oauth_enabled: bool, path: str) -> None:
    """Path-appending and OIDC probe variants 404 in both arms, against the
    real auth-surface wiring — the fall-through that lands clients on the
    served RFC 8414 path-insertion doc. A future /mcp/* mount answering
    401/405 on /mcp/.well-known/... sub-paths would break this order."""
    client = _make_client(oauth_enabled=oauth_enabled, full_wiring=True)
    resp = client.get(path)
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
    assert "www-authenticate" not in resp.headers


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
