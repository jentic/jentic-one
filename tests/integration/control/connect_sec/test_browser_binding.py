"""The OAuth connect callback completes only in the browser that started the flow.

Drives the real routers over HTTP with real cookie handling: the begin route
(``POST /credentials/{id}/connect``, or ``:confirm`` for an agent's session)
sets a per-flow cookie on the calling browser, and ``GET
/credentials/oauth/callback`` completes only when it carries that cookie. A
callback without it, or with another flow's cookie, redirects with
``status=error``, writes nothing and leaves the flow open for the right browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from jentic_one.control.repos.oauth_token_repo import OAuthTokenRepository
from jentic_one.control.services.credentials.schemas.provision import ProvisionResult
from jentic_one.control.services.credentials.state import (
    BROWSER_BINDING_COOKIE_PREFIX,
    OAUTH_CALLBACK_PATH,
    decode_state,
    new_browser_binding,
)
from jentic_one.control.services.integrations import identity_echo
from jentic_one.control.services.integrations.flow_handlers.base import (
    DeviceAuthorizationBeginResult,
)
from jentic_one.control.services.integrations.flow_handlers.device_authorization import (
    DeviceAuthorizationHandler,
)
from jentic_one.control.web.connect_binding import MAX_BINDING_COOKIES
from jentic_one.control.web.routers import credentials as credentials_router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import (
    VendorAuthConfig,
    VendorAuthorizationCodeFlowConfig,
    VendorDeviceAuthorizationFlowConfig,
    VendorScopeConfig,
)
from jentic_one.shared.context import Context
from tests.integration.control.connect_sec.support import (
    AGENT_ID,
    KEY_SCHEME,
    OAUTH_SCHEME,
    OWNER,
    RULES,
    build_app,
    connect,
    digest,
    existing_credential,
    import_spec,
    session_row,
)

pytestmark = pytest.mark.integration

_APPROVER_CLIENT_SECRET = "approver-client-secret-" + "C" * 20  # pragma: allowlist secret


def _app(ctx: Context, identity: Identity) -> FastAPI:
    app = build_app(ctx, identity)
    app.include_router(credentials_router.router)
    return app


def _browser(ctx: Context, identity: Identity = OWNER, *, base: str = "https://t") -> AsyncClient:
    """One browser: its own cookie jar against the deployment."""
    return AsyncClient(
        transport=ASGITransport(app=_app(ctx, identity)), base_url=base, follow_redirects=False
    )


def _state_of(authorize_url: str) -> str:
    return parse_qs(urlsplit(authorize_url).query)["state"][0]


def _binding_set_cookie(response: httpx.Response) -> str:
    headers = [
        h
        for h in response.headers.get_list("set-cookie")
        if h.startswith(BROWSER_BINDING_COOKIE_PREFIX)
    ]
    assert len(headers) == 1, response.headers.get_list("set-cookie")
    return headers[0]


def _status(response: httpx.Response) -> str:
    assert response.status_code in (302, 303), response.status_code
    return parse_qs(urlsplit(response.headers["location"]).query)["status"][0]


def _assert_cookie_cleared(response: httpx.Response, cookie_name: str) -> None:
    cleared = [
        h for h in response.headers.get_list("set-cookie") if h.startswith(f"{cookie_name}=")
    ]
    assert len(cleared) == 1, response.headers.get_list("set-cookie")
    lower = cleared[0].lower()
    assert "max-age=0" in lower
    assert f"path={OAUTH_CALLBACK_PATH}" in lower


async def _callback(
    browser: AsyncClient,
    state: str,
    *,
    code: str = "vendor-code",
    cookies: dict[str, str] | None = None,
) -> httpx.Response:
    """The vendor's redirect back, carrying the browser's cookies.

    ``cookies`` replaces the browser's jar with exactly these cookies.
    """
    if cookies is None:
        return await browser.get(OAUTH_CALLBACK_PATH, params={"state": state, "code": code})
    browser.cookies.clear()
    headers = {"cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())} if cookies else {}
    return await browser.get(
        OAUTH_CALLBACK_PATH, params={"state": state, "code": code}, headers=headers
    )


# --- standalone credential connect (the UI's "Connect" / "Reconnect") -------


async def _fresh_grant(ctx: Context, *, state: Any, callback: Any) -> ProvisionResult:
    return ProvisionResult(access_token="fresh-access", scope="read write")


@pytest.fixture()
def vendor(env: Context, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand in for the vendor's token endpoint; records every exchange."""
    exchanged: list[str] = []
    # Rebuild the (lazily cached) registry so it sees ``env``'s direct_oauth2 config.
    monkeypatch.setattr(env, "_providers", None)
    provider = env.providers.get("direct_oauth2")

    async def complete_connect(ctx: Context, *, state: Any, callback: Any) -> ProvisionResult:
        exchanged.append(state.credential_id)
        return await _fresh_grant(ctx, state=state, callback=callback)

    monkeypatch.setattr(provider, "complete_connect", complete_connect)
    return exchanged


async def _granted(ctx: Context, credential_id: str) -> str | None:
    async with ctx.control_db.session() as session:
        token = await OAuthTokenRepository.get_by_credential(session, credential_id)
    return token.scope if token is not None else None


async def _begin(browser: AsyncClient, credential_id: str) -> tuple[str, str]:
    """Start a connect in ``browser``; return ``(state, cookie_name)``."""
    started = await browser.post(f"/credentials/{credential_id}/connect", json={"scopes": ["read"]})
    assert started.status_code == 200, started.text
    set_cookie = _binding_set_cookie(started)
    return started.json()["state"], set_cookie.split("=", 1)[0]


async def test_begin_sets_a_scoped_httponly_cookie_bound_into_the_state(
    env: Context, vendor: list[str]
) -> None:
    credential_id = await existing_credential(env, oauth_scope="read")
    async with _browser(env) as browser:
        started = await browser.post(f"/credentials/{credential_id}/connect", json={})
    set_cookie = _binding_set_cookie(started)
    name, rest = set_cookie.split("=", 1)
    value = rest.split(";", 1)[0]
    attrs = {a.strip().lower() for a in rest.split(";")[1:]}
    assert "httponly" in attrs
    assert "secure" in attrs
    assert "samesite=lax" in attrs
    assert f"path={OAUTH_CALLBACK_PATH}" in attrs
    ttl = env.config.credentials.connect.state_ttl_seconds
    assert f"max-age={ttl}" in attrs
    # Only the cookie's hash is signed into the state; the value is not in it.
    state = decode_state(
        env.config.credentials.connect.state_secret.get_secret_value(), started.json()["state"]
    )
    assert state.browser_binding_hash is not None
    assert name == f"{BROWSER_BINDING_COOKIE_PREFIX}{state.browser_binding_hash[:16]}"
    assert value not in started.json()["state"]
    assert value not in started.json()["authorize_url"]


async def test_plain_http_origin_omits_secure(
    env: Context, vendor: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local installs on http://127.0.0.1: a Secure cookie would be dropped by the browser."""
    monkeypatch.setattr(env.config.server, "public_base_url", None)
    monkeypatch.setattr(env.config.credentials.providers["direct_oauth2"], "redirect_uri", None)
    monkeypatch.setattr(env, "_providers", None)
    monkeypatch.setattr(env.providers.get("direct_oauth2"), "complete_connect", _fresh_grant)
    credential_id = await existing_credential(env, oauth_scope="read")
    async with _browser(env, base="http://127.0.0.1:8020") as browser:
        started = await browser.post(f"/credentials/{credential_id}/connect", json={})
        attrs = {a.strip().lower() for a in _binding_set_cookie(started).split(";")[1:]}
        assert "secure" not in attrs
        completed = await _callback(browser, started.json()["state"])
    assert _status(completed) == "ok"


async def test_matching_cookie_completes_and_is_cleared(env: Context, vendor: list[str]) -> None:
    credential_id = await existing_credential(env, oauth_scope="read")
    async with _browser(env) as browser:
        state, cookie_name = await _begin(browser, credential_id)
        completed = await _callback(browser, state)
    assert _status(completed) == "ok"
    _assert_cookie_cleared(completed, cookie_name)
    assert vendor == [credential_id]
    assert await _granted(env, credential_id) == "read write"


@pytest.mark.parametrize("cookie", ["missing", "mismatched"])
async def test_callback_without_the_starting_browsers_cookie_changes_nothing(
    env: Context, vendor: list[str], cookie: str
) -> None:
    credential_id = await existing_credential(env, oauth_scope="read")
    async with _browser(env) as initiator, _browser(env) as other:
        state, cookie_name = await _begin(initiator, credential_id)
        sent = {} if cookie == "missing" else {cookie_name: new_browser_binding().value}
        refused = await _callback(other, state, cookies=sent)
        assert _status(refused) == "error"
        _assert_cookie_cleared(refused, cookie_name)
        # Nothing exchanged, nothing written.
        assert vendor == []
        assert await _granted(env, credential_id) == "read"

        # The nonce was not consumed: the initiator's own browser still finishes.
        completed = await _callback(initiator, state)
    assert _status(completed) == "ok"
    assert await _granted(env, credential_id) == "read write"


async def test_another_flows_cookie_does_not_complete_this_flow(
    env: Context, vendor: list[str]
) -> None:
    """A browser holding its own (valid) binding for flow B cannot finish flow A."""
    first = await existing_credential(env, oauth_scope="read", name="First")
    second = await existing_credential(env, oauth_scope="read", name="Second")
    async with _browser(env) as initiator, _browser(env) as other:
        state_a, _ = await _begin(initiator, first)
        await _begin(other, second)
        refused = await _callback(other, state_a)
    assert _status(refused) == "error"
    assert vendor == []


# --- agent-initiated session: the approver's browser is bound ----------------

_VENDOR_ENTRY = VendorAuthConfig(
    vendor="vault.example/api",
    display_name="Vault",
    flows=[
        VendorAuthorizationCodeFlowConfig(
            client_id="vault-client",
            client_secret=SecretStr("vault-client-secret"),  # pragma: allowlist secret
            authorize_url="https://auth.vault.example/authorize",
            token_url="https://auth.vault.example/token",
        )
    ],
    scopes=[VendorScopeConfig(name="read", classification="read", default=True)],
)


def _own_client_body() -> dict[str, Any]:
    return {
        "kind": "own_oauth_client",
        "client_id": "approver-client",
        "client_secret": _APPROVER_CLIENT_SECRET,
        "authorize_url": "https://idp.approver.example/authorize",
        "token_url": "https://idp.approver.example/token",
        "confirmed_scopes": ["read"],
    }


async def _confirm(
    browser: AsyncClient,
    ctx: Context,
    session_id: str,
    body: dict[str, Any],
    *,
    agent_id: str = AGENT_ID,
) -> httpx.Response:
    return await browser.post(
        f"/connect-sessions/{session_id}:confirm",
        json={
            "permission_rules": RULES,
            "expected_agent_id": agent_id,
            "digest": await digest(ctx, session_id),
            **body,
        },
    )


async def _confirm_own_client(
    browser: AsyncClient, ctx: Context, session_id: str
) -> httpx.Response:
    return await _confirm(browser, ctx, session_id, _own_client_body())


class _VendorTokenEndpoint:
    """Stand-in for the vendor's token endpoint during the session-flow exchange."""

    def __init__(self) -> None:
        self.calls = 0

    async def __aenter__(self) -> _VendorTokenEndpoint:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def post(self, *_: object, **__: object) -> httpx.Response:
        self.calls += 1
        return httpx.Response(
            200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
        )


@dataclass
class _Approval:
    """One way an approver's ``:confirm`` hands out an authorization URL."""

    body: dict[str, Any]
    session_id: str
    credential_id: str | None = None

    async def connected(self, ctx: Context, exchanges: int) -> bool:
        """Whether the vendor sign-in has been completed (anything written)."""
        if self.credential_id is not None:  # re-authorize: the wider grant landed
            return await _granted(ctx, self.credential_id) == "read write"
        row = await session_row(ctx, self.session_id)
        assert row is not None
        if exchanges == 0:
            assert await _granted(ctx, row.credential_id) is None
        return row.state == "connected"


async def _approval(ctx: Context, kind: str) -> _Approval:
    """Open an agent's session and the confirm body for ``kind``."""
    await import_spec(ctx, OAUTH_SCHEME)
    if kind == "oauth":
        ctx.config.vendors.entries["vault"] = _VENDOR_ENTRY
        created = await connect(ctx, requested_scopes=["read"])
        return _Approval({"kind": "oauth", "confirmed_scopes": ["read"]}, created.session_id)
    if kind == "own_oauth_client":
        created = await connect(ctx, requested_scopes=["read"])
        return _Approval(_own_client_body(), created.session_id)
    created = await connect(ctx, requested_scopes=["read", "write"])
    narrow = await existing_credential(ctx, oauth_scope="read")
    return _Approval(
        {"kind": "reauthorize", "credential_id": narrow},
        created.session_id,
        credential_id=narrow,
    )


@pytest.mark.parametrize("kind", ["oauth", "own_oauth_client", "reauthorize"])
async def test_agent_session_callback_completes_only_in_the_approvers_browser(
    env: Context, vendor: list[str], kind: str
) -> None:
    approval = await _approval(env, kind)
    token_endpoint = _VendorTokenEndpoint()
    echoed = identity_echo.IdentityEchoResult(display="approver", raw={})
    # Browsers first: the patch below swaps ``httpx.AsyncClient`` for the vendor.
    async with _browser(env) as approver, _browser(env) as other:
        confirmed = await _confirm(approver, env, approval.session_id, approval.body)
        assert confirmed.status_code == 200, confirmed.text
        cookie_name = _binding_set_cookie(confirmed).split("=", 1)[0]
        state = _state_of(confirmed.json()["authorize_url"])
        with (
            patch("httpx.AsyncClient", return_value=token_endpoint),
            patch.object(identity_echo, "echo_identity", new=AsyncMock(return_value=echoed)),
        ):
            for sent in ({}, {cookie_name: new_browser_binding().value}):
                refused = await _callback(other, state, cookies=sent)
                assert _status(refused) == "error"
                _assert_cookie_cleared(refused, cookie_name)
            # Nor can a vendor ``error=`` landing end the flow from elsewhere.
            other.cookies.clear()
            denied = await other.get(
                OAUTH_CALLBACK_PATH, params={"state": state, "error": "access_denied"}
            )
            assert _status(denied) == "error"

            # Nothing exchanged or written; the flow is still open.
            assert (token_endpoint.calls, vendor) == (0, [])
            assert not await approval.connected(env, exchanges=0)

            completed = await _callback(approver, state)
    assert _status(completed) == "ok"
    _assert_cookie_cleared(completed, cookie_name)
    assert token_endpoint.calls + len(vendor) == 1
    assert await approval.connected(env, exchanges=1)


async def test_secret_confirm_sets_no_binding_cookie(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    async with _browser(env) as approver:
        confirmed = await _confirm(
            approver,
            env,
            created.session_id,
            {"kind": "api_key", "key": "vault-key-" + "K" * 20},  # pragma: allowlist secret
        )
    assert confirmed.status_code == 200, confirmed.text
    assert not [
        h
        for h in confirmed.headers.get_list("set-cookie")
        if h.startswith(BROWSER_BINDING_COOKIE_PREFIX)
    ]


async def test_device_flow_confirm_sets_no_binding_cookie(env: Context) -> None:
    await import_spec(env, OAUTH_SCHEME)
    env.config.vendors.entries["vault"] = VendorAuthConfig(
        vendor="vault.example/api",
        display_name="Vault",
        flows=[
            VendorDeviceAuthorizationFlowConfig(
                client_id="vault-device",
                authorization_endpoint="https://auth.vault.example/device",
                token_endpoint="https://auth.vault.example/token",
            )
        ],
        scopes=[VendorScopeConfig(name="read", classification="read", default=True)],
    )
    created = await connect(env, requested_scopes=["read"])
    challenge = DeviceAuthorizationBeginResult(
        user_code="ABCD-EFGH", verification_uri="https://auth.vault.example/activate"
    )
    async with _browser(env) as approver:
        with patch.object(
            DeviceAuthorizationHandler, "begin", new=AsyncMock(return_value=challenge)
        ):
            confirmed = await _confirm(
                approver, env, created.session_id, {"kind": "oauth", "confirmed_scopes": ["read"]}
            )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["user_code"] == "ABCD-EFGH"
    assert not [
        h
        for h in confirmed.headers.get_list("set-cookie")
        if h.startswith(BROWSER_BINDING_COOKIE_PREFIX)
    ]


async def test_agent_session_callback_follows_the_approvers_loopback_alias(env: Context) -> None:
    """A local deploy reached on 127.0.0.1 while the redirect URI names localhost.

    The cookie lives on the host the approver confirmed on, so the callback hops
    there once (same port, path and query) instead of failing.
    """
    await import_spec(env, OAUTH_SCHEME)
    created = await connect(env, requested_scopes=["read"])
    async with _browser(env, base="http://127.0.0.1:8020") as approver:
        confirmed = await _confirm_own_client(approver, env, created.session_id)
        assert confirmed.status_code == 200, confirmed.text
        state = _state_of(confirmed.json()["authorize_url"])
    async with _browser(env, base="http://localhost:8020") as same_machine:
        hop = await same_machine.get(OAUTH_CALLBACK_PATH, params={"state": state, "code": "c"})
    assert hop.status_code == 303
    target = urlsplit(hop.headers["location"])
    assert (target.scheme, target.hostname, target.port, target.path) == (
        "http",
        "127.0.0.1",
        8020,
        OAUTH_CALLBACK_PATH,
    )
    assert parse_qs(target.query) == {"state": [state], "code": ["c"]}


@pytest.mark.parametrize(
    ("signed", "current"),
    [
        ("http://127.0.0.1:8020", "https://app.example.com"),
        ("https://app.example.com", "http://127.0.0.1:8020"),
    ],
    ids=["loopback-signed-public-current", "public-signed-loopback-current"],
)
async def test_hop_only_between_loopback_hosts(env: Context, signed: str, current: str) -> None:
    await import_spec(env, OAUTH_SCHEME)
    created = await connect(env, requested_scopes=["read"])
    async with _browser(env, base=signed) as approver:
        confirmed = await _confirm_own_client(approver, env, created.session_id)
        assert confirmed.status_code == 200, confirmed.text
        state = _state_of(confirmed.json()["authorize_url"])
    async with _browser(env, base=current) as elsewhere:
        refused = await elsewhere.get(OAUTH_CALLBACK_PATH, params={"state": state, "code": "c"})
    assert _status(refused) == "error"
    live = await session_row(env, created.session_id)
    assert live is not None
    assert live.state != "connected"


# --- cookie attributes follow the public callback URL ------------------------


def _attrs(response: httpx.Response) -> dict[str, str]:
    parts = _binding_set_cookie(response).split(";")[1:]
    return {k.strip().lower(): v.strip() for k, _, v in (part.partition("=") for part in parts)}


async def test_https_callback_behind_a_plain_http_proxy_hop_is_secure(
    env: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TLS ends at the proxy: the app sees http, the browser lands on https."""
    monkeypatch.setattr(env.config.server, "public_base_url", None)
    monkeypatch.setattr(
        env.config.credentials.providers["direct_oauth2"],
        "redirect_uri",
        "https://app.example.com/credentials/oauth/callback",
    )
    await import_spec(env, OAUTH_SCHEME)
    created = await connect(env, requested_scopes=["read"])
    async with _browser(env, base="http://10.0.0.5:8000") as approver:
        confirmed = await _confirm_own_client(approver, env, created.session_id)
    assert confirmed.status_code == 200, confirmed.text
    assert "secure" in _attrs(confirmed)


async def test_cookie_path_follows_a_base_url_path(
    env: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefixed = "https://app.example.com/jentic/credentials/oauth/callback"
    monkeypatch.setattr(env.config.credentials.providers["direct_oauth2"], "redirect_uri", prefixed)
    await import_spec(env, OAUTH_SCHEME)
    created = await connect(env, requested_scopes=["read"])
    async with _browser(env) as approver:
        confirmed = await _confirm_own_client(approver, env, created.session_id)
        assert confirmed.status_code == 200, confirmed.text
        assert _attrs(confirmed)["path"] == "/jentic/credentials/oauth/callback"
        cookie_name = _binding_set_cookie(confirmed).split("=", 1)[0]
        state = _state_of(confirmed.json()["authorize_url"])
        # The proxy strips the prefix; the clearing cookie keeps the public path.
        refused = await _callback(approver, state, cookies={})
    cleared = next(
        h for h in refused.headers.get_list("set-cookie") if h.startswith(f"{cookie_name}=")
    )
    assert "path=/jentic/credentials/oauth/callback" in cleared.lower()


async def test_callback_expires_a_pile_of_abandoned_binding_cookies(
    env: Context, vendor: list[str]
) -> None:
    credential_id = await existing_credential(env, oauth_scope="read")
    abandoned = {new_browser_binding().cookie_name: "x" for _ in range(MAX_BINDING_COOKIES)}
    async with _browser(env) as browser:
        state, cookie_name = await _begin(browser, credential_id)
        binding = browser.cookies.get(cookie_name)
        assert binding is not None
        completed = await _callback(browser, state, cookies={**abandoned, cookie_name: binding})
    assert _status(completed) == "ok"
    for name in [cookie_name, *abandoned]:
        _assert_cookie_cleared(completed, name)


async def test_a_few_other_flows_cookies_are_left_alone(env: Context, vendor: list[str]) -> None:
    """Concurrent connects in one browser keep their cookies."""
    first = await existing_credential(env, oauth_scope="read", name="First")
    second = await existing_credential(env, oauth_scope="read", name="Second")
    async with _browser(env) as browser:
        state_a, _ = await _begin(browser, first)
        state_b, name_b = await _begin(browser, second)
        done_a = await _callback(browser, state_a)
        assert not [h for h in done_a.headers.get_list("set-cookie") if h.startswith(f"{name_b}=")]
        done_b = await _callback(browser, state_b)
    assert (_status(done_a), _status(done_b)) == ("ok", "ok")
    _assert_cookie_cleared(done_b, name_b)
