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
from jentic_one.mcp.app import CLIENT_INFO_META_KEY, MCP_PRM_PATH, request_client_info
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


def test_credential_less_resources_read_is_challenged() -> None:
    """resources/read stays OFF the pre-auth whitelist until resources are
    actually served — a future registration must not land pre-auth by default
    (the listings stay whitelisted; they are empty and connection-independent)."""
    with make_client(oauth_enabled=True) as client:
        read = client.post(
            "/mcp",
            json=_rpc("resources/read", {"uri": "skill://jentic"}),
            headers=_ACCEPT,
        )
        assert read.status_code == 401
        assert read.headers["www-authenticate"] == _CHALLENGE
        listing = client.post("/mcp", json=_rpc("resources/list"), headers=_ACCEPT)
        assert listing.status_code == 200


def test_authenticated_get_is_405_and_never_reaches_the_sse_arm() -> None:
    """MINOR-3 regression: in stateless mode the SDK's GET-as-SSE arm opens a
    stream that never carries a message and never ends — the gate must answer
    405 before the transport sees the request (a hang here would block this
    test forever)."""
    with make_client() as client:
        resp = client.get(
            "/mcp",
            headers={
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {_GOOD_BEARER}",
            },
        )
        assert resp.status_code == 405
        assert resp.headers["allow"] == "POST"
        assert resp.json() == {"detail": "Method Not Allowed"}


def test_credential_less_get_stays_401_not_405() -> None:
    """Auth precedes method semantics: an unauthenticated GET keeps answering
    the 401 challenge (the 3a-4 contract), never a method tell."""
    with make_client(oauth_enabled=True) as client:
        resp = client.get("/mcp", headers={"Accept": "text/event-stream"})
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == _CHALLENGE
        assert "allow" not in resp.headers


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


def test_rebinding_shaped_origin_matching_own_host_is_403() -> None:
    """THE DNS-rebinding shape: the browser resolved the attacker's name to
    this daemon's IP, so Origin and Host arrive as a self-consistent pair —
    the request's own Host header must never vouch for an Origin."""
    with make_client() as client:
        for origin, host in (
            ("http://attacker.example", "attacker.example"),
            ("http://attacker.example:80", "attacker.example"),
            ("http://attacker.example", "attacker.example:9999"),
            ("http://testserver", "testserver"),  # pinned as a PASS before this fix
        ):
            resp = client.post(
                "/mcp",
                json=_rpc("tools/list"),
                headers={**_ACCEPT, "Origin": origin, "Host": host},
            )
            assert resp.status_code == 403, (origin, host)


def test_loopback_prefixed_dns_name_origin_is_403() -> None:
    """``127.0.0.1.evil.example`` is a resolvable public name, not loopback —
    the loopback arm parses the host as an IP, never prefix-matches it."""
    with make_client() as client:
        resp = client.post(
            "/mcp",
            json=_rpc("tools/list"),
            headers={**_ACCEPT, "Origin": "http://127.0.0.1.evil.example"},
        )
        assert resp.status_code == 403


def test_absent_origin_passes() -> None:
    """Non-browser clients (every real MCP client today) send no Origin."""
    with make_client() as client:
        resp = client.post("/mcp", json=_rpc("tools/list"), headers=_ACCEPT)
        assert resp.status_code == 200


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
        "http://[::1]:6274",
        "https://auth.example.com",  # the config-derived canonical origin
        "https://auth.example.com:443",  # default-port normalization
    ],
)
def test_canonical_and_loopback_origins_pass(origin: str) -> None:
    with make_client() as client:
        resp = client.post("/mcp", json=_rpc("tools/list"), headers={**_ACCEPT, "Origin": origin})
        assert resp.status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "http://auth.example.com",  # scheme mismatch = a different web origin
        "https://auth.example.com:8443",  # port mismatch
        "https://sub.auth.example.com",
    ],
)
def test_near_canonical_origins_are_403(origin: str) -> None:
    with make_client() as client:
        resp = client.post("/mcp", json=_rpc("tools/list"), headers={**_ACCEPT, "Origin": origin})
        assert resp.status_code == 403


def test_without_canonical_base_url_only_loopback_origins_pass() -> None:
    """No configured canonical origin (local dev) → the trusted set is
    loopback only; the request's Host never substitutes for config."""
    with make_client(canonical_base_url="") as client:
        ok = client.post(
            "/mcp", json=_rpc("tools/list"), headers={**_ACCEPT, "Origin": "http://localhost:3000"}
        )
        assert ok.status_code == 200
        denied = client.post(
            "/mcp", json=_rpc("tools/list"), headers={**_ACCEPT, "Origin": "http://testserver"}
        )
        assert denied.status_code == 403


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


# --- windowed session telemetry (phase-3 item 6) -------------------------------


_CLIENT_INFO_PARAMS = {"_meta": {CLIENT_INFO_META_KEY: {"name": "cursor", "version": "0.42"}}}


def test_authenticated_post_schedules_the_windowed_session_emit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every authenticated POST feeds the windowed emit, clientInfo from the
    request's own ``_meta`` (spec 2026-07-28 — no initialize to carry it)."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "jentic_one.mcp.app.schedule_mcp_http_session_emit",
        lambda ctx, **kwargs: calls.append(kwargs),
    )
    with make_client() as client:
        resp = client.post(
            "/mcp",
            json=_rpc("tools/list", _CLIENT_INFO_PARAMS),
            headers={**_ACCEPT, "Authorization": f"Bearer {_GOOD_BEARER}"},
        )
        assert resp.status_code == 200
    assert calls == [
        {
            "client_name": "cursor",
            "client_version": "0.42",
            "actor_id": "agnt_1",
            "actor_type": "agent",
        }
    ]


def test_authenticated_post_without_client_info_schedules_unknown_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clientInfo is a SHOULD — absent degrades to client-unknown, still fed."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "jentic_one.mcp.app.schedule_mcp_http_session_emit",
        lambda ctx, **kwargs: calls.append(kwargs),
    )
    with make_client() as client:
        client.post(
            "/mcp",
            json=_rpc("tools/list"),
            headers={**_ACCEPT, "Authorization": f"Bearer {_GOOD_BEARER}"},
        )
    assert calls and calls[0]["client_name"] is None
    assert calls[0]["client_version"] is None


def test_credential_less_and_rejected_requests_never_schedule_the_emit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No resolved identity → no key to emit under: the pre-auth arm and the
    invalid-credential 401 both skip the telemetry hook entirely."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "jentic_one.mcp.app.schedule_mcp_http_session_emit",
        lambda ctx, **kwargs: calls.append(kwargs),
    )
    with make_client() as client:
        anon = client.post("/mcp", json=_rpc("tools/list", _CLIENT_INFO_PARAMS), headers=_ACCEPT)
        assert anon.status_code == 200
        rejected = client.post(
            "/mcp",
            json=_rpc("tools/list", _CLIENT_INFO_PARAMS),
            headers={**_ACCEPT, "Authorization": "Bearer at_expired"},
        )
        assert rejected.status_code == 401
    assert calls == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # The happy path: params._meta carrying the spec key.
        (
            json.dumps(_rpc("tools/call", _CLIENT_INFO_PARAMS)),
            ("cursor", "0.42"),
        ),
        # Batch: the first request carrying clientInfo wins.
        (
            json.dumps(
                [
                    _rpc("ping", id_=1),
                    _rpc("tools/list", _CLIENT_INFO_PARAMS, id_=2),
                ]
            ),
            ("cursor", "0.42"),
        ),
        # Name without version (version is optional inside clientInfo too).
        (
            json.dumps(_rpc("tools/list", {"_meta": {CLIENT_INFO_META_KEY: {"name": "codex"}}})),
            ("codex", None),
        ),
        # No _meta at all.
        (json.dumps(_rpc("tools/list")), (None, None)),
        # Malformed JSON containing the key bytes — degrades, never raises.
        (f'{{"{CLIENT_INFO_META_KEY}": broken', (None, None)),
        # clientInfo present but not an object.
        (
            json.dumps(_rpc("tools/list", {"_meta": {CLIENT_INFO_META_KEY: "cursor"}})),
            (None, None),
        ),
        # Non-string name/version fields degrade to unknown, not a crash.
        (
            json.dumps(
                _rpc(
                    "tools/list",
                    {"_meta": {CLIENT_INFO_META_KEY: {"name": 7, "version": ["x"]}}},
                )
            ),
            (None, None),
        ),
    ],
)
def test_request_client_info_parsing(body: str, expected: tuple[str | None, str | None]) -> None:
    assert request_client_info(body.encode()) == expected


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
