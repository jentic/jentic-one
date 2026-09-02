"""Gate/auth/Origin contract for the mounted ``/mcp`` app (phase-3 items 1+3).

Pins the four ``server.mcp.enabled`` x ``server.mcp.oauth.enabled`` arms:

====================  =====================  =======================================
``mcp.enabled``       ``mcp.oauth.enabled``  ``/mcp`` answers
====================  =====================  =======================================
off (default)         off (default)          the framework's plain 404 (as before 3a-4)
off                   on                     the 3a-4 discovery challenge, verbatim
on                    off                    401 bare ``Bearer`` without a credential
on                    on                     401 + ``resource_metadata`` pointer
====================  =====================  =======================================

plus the mount-owned request checks on the enabled arms: strict ``Origin``
validation (403 on spoof), the pre-auth whitelist (``tools/list`` without a
credential), bearer resolution through the app-state ``verify_token`` (the
same superset verifier the REST routes use — including 3a-3 grant-channel
bearers), and the F7 sub-path fall-through (``/mcp/.well-known/…`` keeps
answering the plain 404 in every arm).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import MagicMock

import opentelemetry.instrumentation.fastapi as otel_fastapi
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from jentic_one.auth.web.routers.discovery import _MCP_PRM_PATH
from jentic_one.mcp.app import MCP_PRM_PATH
from jentic_one.mcp.installer import install_mcp_mount, mcp_lifespan
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.actors import Origin

_BASE = "https://auth.example.com"
_CHALLENGE = f'Bearer resource_metadata="{_BASE}/.well-known/oauth-protected-resource/mcp"'

#: the method set the 3a-4 placeholder pinned (review F3/F4): the challenge
#: precedes method semantics, and the disabled arm answers a uniform 404.
_MCP_METHODS = ("GET", "POST", "DELETE", "HEAD", "OPTIONS", "PUT", "PATCH")

_GOOD_BEARER = "at_good"
_GOOD_IDENTITY = Identity(
    sub="agnt_1",
    permissions=["apis:read", "capabilities:read", "jobs:read"],
    actor_type=ActorType.AGENT,
)

#: the MCP-required Accept pair (the SDK transport enforces it on POST).
_ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc(method: str, params: dict[str, Any] | None = None, id_: int | None = 1) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    if id_ is not None:
        body["id"] = id_
    return body


def make_client(
    *,
    mcp_enabled: bool = True,
    oauth_enabled: bool = False,
    canonical_base_url: str = _BASE,
) -> TestClient:
    """A minimal control-plane-shaped app carrying only the mount + verifier."""
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(canonical_base_url=canonical_base_url)
    server = ServerConfig()
    server.mcp.enabled = mcp_enabled
    server.mcp.oauth.enabled = oauth_enabled
    ctx.config.server = server
    ctx.instance_id = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with mcp_lifespan(app, ctx):
            yield

    app = FastAPI(lifespan=lifespan)
    app.state.ctx = ctx
    install_mcp_mount(app, ctx)

    async def verify_token(credential: str, request: Request) -> Identity:
        if credential == _GOOD_BEARER:
            return _GOOD_IDENTITY.model_copy(deep=True)
        raise ValueError("invalid credential")

    app.state.verify_token = verify_token
    return TestClient(app)


# --- single-sourcing pin -----------------------------------------------------


def test_prm_path_stays_in_lockstep_with_the_discovery_router() -> None:
    """The mount's challenge pointer and the served PRM document path are the
    same string — a drift would 401 clients into a 404 discovery document."""
    assert MCP_PRM_PATH == _MCP_PRM_PATH


# --- arm 1: both gates off — indistinguishable from not-shipped --------------


@pytest.mark.parametrize("method", _MCP_METHODS)
def test_all_off_is_plain_404_on_every_method(method: str) -> None:
    with make_client(mcp_enabled=False, oauth_enabled=False) as client:
        resp = client.request(method, "/mcp")
        assert resp.status_code == 404
        assert "www-authenticate" not in resp.headers
        assert "allow" not in resp.headers
        if method != "HEAD":
            assert resp.json() == {"detail": "Not Found"}


# --- arm 2: oauth on, endpoint off — the 3a-4 placeholder contract, verbatim -


@pytest.mark.parametrize("method", _MCP_METHODS)
def test_oauth_only_arm_answers_the_discovery_challenge(method: str) -> None:
    """The endpoint being off must not break the RFC 9728 chain the 3a-4
    placeholder served: any probe answers 401 + the resource_metadata pointer."""
    with make_client(mcp_enabled=False, oauth_enabled=True) as client:
        resp = client.request(method, "/mcp")
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == _CHALLENGE
        if method == "HEAD":
            # RFC 9110 §9.3.2: same status and headers as GET, no body.
            assert resp.content == b""
        else:
            assert resp.json() == {"detail": "Unauthorized"}


def test_oauth_only_arm_falls_back_to_request_host_when_canonical_empty() -> None:
    with make_client(mcp_enabled=False, oauth_enabled=True, canonical_base_url="") as client:
        resp = client.get("/mcp")
        assert resp.headers["www-authenticate"] == (
            'Bearer resource_metadata="http://testserver/.well-known/oauth-protected-resource/mcp"'
        )


# --- arms 3+4: endpoint on — auth required, challenge shape per oauth arm ----


@pytest.mark.parametrize("method", ("GET", "DELETE", "PUT", "PATCH", "HEAD", "OPTIONS"))
def test_enabled_credential_less_non_post_is_challenged(method: str) -> None:
    """Only POSTed pre-auth JSON-RPC methods pass without a credential."""
    with make_client(oauth_enabled=True) as client:
        resp = client.request(method, "/mcp")
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == _CHALLENGE


def test_enabled_with_oauth_off_challenges_with_bare_bearer() -> None:
    """No discovery surface → nothing to point at: the challenge is scheme-only
    (the PRM path would 404, so advertising it would break spec-following
    clients mid-chain)."""
    with make_client(oauth_enabled=False) as client:
        resp = client.get("/mcp")
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == "Bearer"


def test_enabled_invalid_bearer_is_challenged_not_500() -> None:
    with make_client(oauth_enabled=True) as client:
        resp = client.post(
            "/mcp",
            json=_rpc("tools/call", {"name": "whoami", "arguments": {}}),
            headers={**_ACCEPT, "Authorization": "Bearer at_expired"},
        )
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == _CHALLENGE


def test_enabled_credential_less_tools_call_is_challenged() -> None:
    """tools/call is NOT on the pre-auth whitelist."""
    with make_client(oauth_enabled=True) as client:
        resp = client.post(
            "/mcp",
            json=_rpc("tools/call", {"name": "whoami", "arguments": {}}),
            headers=_ACCEPT,
        )
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == _CHALLENGE


def test_enabled_credential_less_tools_list_is_served() -> None:
    """§3.3 pre-auth surface discovery: tools/list needs no credential, and
    the tool list is connection-independent (stateless — no initialize)."""
    with make_client() as client:
        resp = client.post("/mcp", json=_rpc("tools/list"), headers=_ACCEPT)
        assert resp.status_code == 200
        result = resp.json()["result"]
        names = [tool["name"] for tool in result["tools"]]
        assert "execute" in names
        assert "search_apis" in names


def test_enabled_authenticated_tools_list_is_the_same_list() -> None:
    with make_client() as client:
        anon = client.post("/mcp", json=_rpc("tools/list"), headers=_ACCEPT)
        authed = client.post(
            "/mcp",
            json=_rpc("tools/list"),
            headers={**_ACCEPT, "Authorization": f"Bearer {_GOOD_BEARER}"},
        )
        assert authed.status_code == 200
        assert anon.json()["result"]["tools"] == authed.json()["result"]["tools"]


def test_stateless_response_carries_no_session_id() -> None:
    """Spec 2026-07-28: no ``Mcp-Session-Id`` anywhere in stateless mode."""
    with make_client() as client:
        resp = client.post("/mcp", json=_rpc("tools/list"), headers=_ACCEPT)
        assert "mcp-session-id" not in resp.headers


# --- strict Origin validation (spec DNS-rebinding rule) -----------------------


def test_spoofed_origin_is_403() -> None:
    with make_client() as client:
        resp = client.post(
            "/mcp",
            json=_rpc("tools/list"),
            headers={**_ACCEPT, "Origin": "https://evil.example.net"},
        )
        assert resp.status_code == 403
        assert resp.json() == {"detail": "Origin not allowed"}


@pytest.mark.parametrize("origin", ["null", "not a url", "https://"])
def test_malformed_origin_is_403(origin: str) -> None:
    with make_client() as client:
        resp = client.post("/mcp", json=_rpc("tools/list"), headers={**_ACCEPT, "Origin": origin})
        assert resp.status_code == 403


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:6274",  # loopback (Inspector et al.)
        "http://127.0.0.1:8000",
        "https://auth.example.com",  # the canonical host
        "http://testserver",  # the request's own Host
    ],
)
def test_own_host_and_loopback_origins_pass(origin: str) -> None:
    with make_client() as client:
        resp = client.post("/mcp", json=_rpc("tools/list"), headers={**_ACCEPT, "Origin": origin})
        assert resp.status_code == 200


def test_origin_check_precedes_auth() -> None:
    """A rebinding page must not learn whether a stolen bearer is live."""
    with make_client() as client:
        resp = client.post(
            "/mcp",
            json=_rpc("tools/list"),
            headers={
                **_ACCEPT,
                "Origin": "https://evil.example.net",
                "Authorization": f"Bearer {_GOOD_BEARER}",
            },
        )
        assert resp.status_code == 403


# --- sub-path fall-through (review F7) ----------------------------------------

_UNSERVED_SUBPATHS = (
    "/mcp/.well-known/openid-configuration",
    "/mcp/.well-known/oauth-authorization-server",
    "/mcp/anything",
)


@pytest.mark.parametrize("mcp_enabled", [True, False])
@pytest.mark.parametrize("oauth_enabled", [True, False])
@pytest.mark.parametrize("path", _UNSERVED_SUBPATHS)
def test_subpaths_answer_the_plain_404_in_every_arm(
    mcp_enabled: bool, oauth_enabled: bool, path: str
) -> None:
    """Discovery probe variants under /mcp keep falling through to 404 so
    clients land on the served RFC 8414 path-insertion documents."""
    with make_client(mcp_enabled=mcp_enabled, oauth_enabled=oauth_enabled) as client:
        resp = client.get(path)
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Not Found"}
        assert "www-authenticate" not in resp.headers


@pytest.mark.parametrize("mcp_enabled", [True, False])
def test_trailing_slash_redirect_tell_is_preserved(mcp_enabled: bool) -> None:
    """`/mcp/` keeps the placeholder-era redirect_slashes 307 to `/mcp` in
    every arm — the mount registers the exact path, so Starlette's slash
    handling is unchanged (the 3a-4 tests pinned this tell as build-level)."""
    with make_client(mcp_enabled=mcp_enabled, oauth_enabled=True) as client:
        resp = client.get("/mcp/", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"].endswith("/mcp")


# --- grant-channel bearer resolution (3a-3) -----------------------------------


def test_grant_channel_bearer_resolves_through_verify_token() -> None:
    """A grant-channel access token (actor=agent + oauth_grant_id) is just
    another bearer to the mount: whatever verify_token resolves is the caller.
    The call reaches the tool layer authenticated — whoami echoes the actor."""
    grant_identity = Identity(
        sub="agnt_grant",
        permissions=["apis:read"],
        actor_type=ActorType.AGENT,
        oauth_client_id="oc_1",
        oauth_grant_id="ogr_1",
    )
    seen: dict[str, Any] = {}

    with make_client() as client:

        async def verify_token(credential: str, request: Request) -> Identity:
            seen["credential"] = credential
            if credential == "at_grant_channel":
                return grant_identity.model_copy(deep=True)
            raise ValueError("invalid credential")

        cast(FastAPI, client.app).state.verify_token = verify_token

        resp = client.post(
            "/mcp",
            json=_rpc("tools/call", {"name": "get_execution_result", "arguments": {}}),
            headers={**_ACCEPT, "Authorization": "Bearer at_grant_channel"},
        )
        assert resp.status_code == 200
        assert seen["credential"] == "at_grant_channel"
        # Malformed args are an invalid-params protocol error — proving the
        # call passed auth and reached the tool dispatcher.
        body = resp.json()
        assert body.get("error", {}).get("code") == -32602


def test_resolved_identity_is_stamped_origin_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Item 6 note: the mount bypasses derive_origin, so it stamps
    ``identity.origin = Origin.MCP`` itself before the tool layer sees it."""
    captured: dict[str, Any] = {}

    async def spy_dispatch(env: Any, name: str, arguments: Any) -> Any:
        captured["origin"] = env.identity.origin
        raise AssertionError("stop here")

    with make_client() as client:
        monkeypatch.setattr("jentic_one.mcp.app.dispatch_tool_call", spy_dispatch)
        client.post(
            "/mcp",
            json=_rpc("tools/call", {"name": "whoami", "arguments": {}}),
            headers={**_ACCEPT, "Authorization": f"Bearer {_GOOD_BEARER}"},
        )
        assert captured["origin"] == Origin.MCP


# --- batch pre-auth sniff ------------------------------------------------------


def test_batch_with_non_preauth_method_requires_credential() -> None:
    """A mixed batch cannot smuggle tools/call behind a pre-auth method."""
    with make_client(oauth_enabled=True) as client:
        batch = [
            _rpc("tools/list", id_=1),
            _rpc("tools/call", {"name": "whoami", "arguments": {}}, id_=2),
        ]
        resp = client.post("/mcp", content=json.dumps(batch), headers=_ACCEPT)
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == _CHALLENGE


# --- observability (phase-3 item 1: traces attach to the mount) ----------------


def test_otel_route_details_resolve_the_mcp_route() -> None:
    """The instrumentation's span-naming lookup resolves ``/mcp`` — the same
    upstream ``_get_route_details`` walk the /metrics mount relies on (the old
    local guard is gone; ``test_otel_route_guard.py`` pins the upstream fix).
    The installer registers a plain exact-path Route, which the flattened
    route walk resolves like any other path."""
    with make_client() as client:
        scope = {"type": "http", "method": "POST", "path": "/mcp", "app": client.app}
        get_details: Any = otel_fastapi._get_route_details
        details = get_details(scope)
        route = details[0] if isinstance(details, tuple) else details
        assert route == "/mcp"
