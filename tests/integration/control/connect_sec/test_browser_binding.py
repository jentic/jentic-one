"""The OAuth connect callback completes only in the browser that started the flow.

Drives the real routers over HTTP with real cookie handling: the begin route
(``POST /credentials/{id}/connect``, or ``:confirm`` for an agent's session)
sets a per-flow cookie on the calling browser, and ``GET
/credentials/oauth/callback`` completes only when it carries that cookie. A
callback without it, or with another flow's cookie, redirects with
``status=error``, writes nothing and leaves the flow open for the right browser.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from jentic_one.control.repos.oauth_token_repo import OAuthTokenRepository
from jentic_one.control.services.credentials.schemas.provision import ProvisionResult
from jentic_one.control.services.credentials.state import (
    BROWSER_BINDING_COOKIE_PREFIX,
    OAUTH_CALLBACK_PATH,
    decode_state,
    new_browser_binding,
)
from jentic_one.control.services.integrations import identity_echo
from jentic_one.control.web.routers import credentials as credentials_router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from tests.integration.control.connect_sec.support import (
    AGENT_ID,
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
    if cookies is not None:
        # Send exactly these cookies, bypassing the jar.
        browser.cookies.clear()
        for name, value in cookies.items():
            browser.cookies.set(name, value, domain="t", path=OAUTH_CALLBACK_PATH)
    return await browser.get(OAUTH_CALLBACK_PATH, params={"state": state, "code": code})


# --- standalone credential connect (the UI's "Connect" / "Reconnect") -------


@pytest.fixture()
def vendor(env: Context, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand in for the vendor's token endpoint; records every exchange."""
    exchanged: list[str] = []
    # Rebuild the (lazily cached) registry so it sees ``env``'s direct_oauth2 config.
    monkeypatch.setattr(env, "_providers", None)
    provider = env.providers.get("direct_oauth2")

    async def complete_connect(ctx: Context, *, state: Any, callback: Any) -> ProvisionResult:
        exchanged.append(state.credential_id)
        return ProvisionResult(access_token="fresh-access", scope="read write")

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


async def test_plain_http_origin_omits_secure(env: Context, vendor: list[str]) -> None:
    """Local installs on http://127.0.0.1: a Secure cookie would be dropped by the browser."""
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


async def _confirm_own_client(
    browser: AsyncClient, ctx: Context, session_id: str
) -> httpx.Response:
    return await browser.post(
        f"/connect-sessions/{session_id}:confirm",
        json={
            "kind": "own_oauth_client",
            "client_id": "approver-client",
            "client_secret": _APPROVER_CLIENT_SECRET,
            "authorize_url": "https://idp.approver.example/authorize",
            "token_url": "https://idp.approver.example/token",
            "permission_rules": RULES,
            "expected_agent_id": AGENT_ID,
            "digest": await digest(ctx, session_id),
            "confirmed_scopes": ["read"],
        },
    )


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


async def test_agent_session_callback_completes_only_in_the_approvers_browser(
    env: Context,
) -> None:
    await import_spec(env, OAUTH_SCHEME)
    created = await connect(env, requested_scopes=["read"])
    vendor = _VendorTokenEndpoint()
    echoed = identity_echo.IdentityEchoResult(display="approver", raw={})
    # Browsers first: the patch below swaps ``httpx.AsyncClient`` for the vendor.
    async with _browser(env) as approver, _browser(env) as other:
        with (
            patch("httpx.AsyncClient", return_value=vendor),
            patch.object(identity_echo, "echo_identity", new=AsyncMock(return_value=echoed)),
        ):
            confirmed = await _confirm_own_client(approver, env, created.session_id)
            assert confirmed.status_code == 200, confirmed.text
            cookie_name = _binding_set_cookie(confirmed).split("=", 1)[0]
            state = _state_of(confirmed.json()["authorize_url"])
            before = await session_row(env, created.session_id)
            assert before is not None

            for sent in ({}, {cookie_name: new_browser_binding().value}):
                refused = await _callback(other, state, cookies=sent)
                assert _status(refused) == "error"
                _assert_cookie_cleared(refused, cookie_name)
            # Also a vendor ``error=`` landing cannot fail the session from elsewhere.
            other.cookies.clear()
            denied = await other.get(
                OAUTH_CALLBACK_PATH, params={"state": state, "error": "access_denied"}
            )
            assert _status(denied) == "error"

            # Nothing exchanged; the session is still live and unchanged.
            assert vendor.calls == 0
            live = await session_row(env, created.session_id)
            assert live is not None
            assert live.state == before.state
            assert await _granted(env, live.credential_id) is None

            completed = await _callback(approver, state)
    assert _status(completed) == "ok"
    _assert_cookie_cleared(completed, cookie_name)
    assert vendor.calls == 1
    done = await session_row(env, created.session_id)
    assert done is not None
    assert done.state == "connected"


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


async def test_non_loopback_host_is_never_redirected(env: Context) -> None:
    await import_spec(env, OAUTH_SCHEME)
    created = await connect(env, requested_scopes=["read"])
    async with _browser(env, base="http://127.0.0.1:8020") as approver:
        confirmed = await _confirm_own_client(approver, env, created.session_id)
        state = _state_of(confirmed.json()["authorize_url"])
    async with _browser(env, base="https://app.example.com") as elsewhere:
        refused = await elsewhere.get(OAUTH_CALLBACK_PATH, params={"state": state, "code": "c"})
    assert _status(refused) == "error"
    live = await session_row(env, created.session_id)
    assert live is not None
    assert live.state != "connected"
