"""PKCE (RFC 7636 S256) on the standalone DirectOAuth2Provider connect flow.

Both the standalone credential connect route (``POST /credentials/{id}/connect``)
and the agent-session confirmation route (``POST /connect-sessions/{id}:confirm``)
emit an S256 ``code_challenge`` on the authorization URL and present the matching
``code_verifier`` during the token exchange. An authorization code issued for one
flow's challenge is bound to that flow and is rejected by a PKCE-enforcing IdP
when presented in another flow on the same shared OAuth app registration.
"""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, parse_qsl, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos.oauth_app_registration_repo import (
    OAuthAppRegistrationRepository,
)
from jentic_one.control.repos.oauth_token_repo import OAuthTokenRepository
from jentic_one.control.services.credentials.state import OAUTH_CALLBACK_PATH
from jentic_one.control.services.integrations import identity_echo
from jentic_one.control.web.routers import credentials as credentials_router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from tests.integration.control.connect_sec.support import (
    AGENT_ID,
    NAME,
    OAUTH_SCHEME,
    OTHER_OWNER,
    OTHER_OWNER_ID,
    OWNER,
    OWNER_ID,
    RULES,
    VENDOR,
    build_app,
    connect,
    digest,
    import_spec,
    revision,
)

pytestmark = pytest.mark.integration


def _s256_challenge(verifier: str) -> str:
    digest_bytes = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest_bytes).rstrip(b"=").decode("ascii")


class _PkceEnforcingIdP:
    """Fake IdP that enforces RFC 7636 S256 on every authorization code."""

    def __init__(self) -> None:
        self._challenges_by_code: dict[str, tuple[str, str]] = {}
        self._counter = 0
        self.token_requests: list[dict[str, str]] = []

    def issue_code_from_authorize_url(self, authorize_url: str, *, account_token: str) -> str:
        query = parse_qs(urlsplit(authorize_url).query)
        challenge_list = query.get("code_challenge")
        method_list = query.get("code_challenge_method")
        if not challenge_list or method_list != ["S256"]:
            self._counter += 1
            code = f"code-unprotected-{self._counter}"
            self._challenges_by_code[code] = ("", account_token)
            return code
        self._counter += 1
        code = f"code-pkce-{self._counter}"
        self._challenges_by_code[code] = (challenge_list[0], account_token)
        return code

    def handle_token_request(self, request: httpx.Request) -> httpx.Response:
        form = dict(parse_qsl(request.content.decode("utf-8")))
        self.token_requests.append(form)
        code = form.get("code", "")
        entry = self._challenges_by_code.get(code)
        if entry is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        expected_challenge, account_token = entry
        verifier = form.get("code_verifier", "")
        if (
            not expected_challenge
            or not verifier
            or _s256_challenge(verifier) != expected_challenge
        ):
            return httpx.Response(
                400,
                json={"error": "invalid_grant", "error_description": "PKCE verification failed"},
            )
        # Consume one-shot authorization code on valid exchange.
        del self._challenges_by_code[code]
        return httpx.Response(
            200,
            json={
                "access_token": account_token,
                "refresh_token": f"rt-{account_token}",
                "expires_in": 3600,
                "scope": "read",
            },
        )


def _app(ctx: Context, identity: Identity) -> FastAPI:
    app = build_app(ctx, identity)
    app.include_router(credentials_router.router)
    return app


def _browser(ctx: Context, identity: Identity) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=_app(ctx, identity)),
        base_url="https://t",
        follow_redirects=False,
    )


def _status(response: httpx.Response) -> str:
    assert response.status_code in (302, 303), response.status_code
    return parse_qs(urlsplit(response.headers["location"]).query)["status"][0]


async def _shared_registration(ctx: Context) -> str:
    async with ctx.control_db.transaction() as session:
        reg = await OAuthAppRegistrationRepository.create_authorization_code(
            session,
            name="Shared Vault OAuth App",
            api_vendor=VENDOR,
            client_id="shared-vault-client",
            encrypted_client_secret=ctx.encryption.encrypt("shared-vault-secret"),
            authorize_url="https://auth.vault.example/authorize",
            token_url="https://auth.vault.example/token",
            catalog_api_id=f"{VENDOR}/{NAME}",
            display_name="Vault",
            default_scopes=["read"],
            created_by=OWNER_ID,
        )
        return reg.id


async def _pending_registration_credential(
    ctx: Context, *, owner_id: str, registration_id: str, name: str
) -> str:
    credential_id = generate_ksuid("cred")
    async with ctx.control_db.transaction() as session:
        session.add(
            Credential(
                id=credential_id,
                type="OAUTH2_AUTHORIZATION_CODE",
                provider="direct_oauth2",
                name=name,
                api_vendor=VENDOR,
                api_name=NAME,
                oauth_app_registration_id=registration_id,
                created_by=owner_id,
                state="pending",
            )
        )
    return credential_id


async def test_code_minted_for_one_flow_cannot_complete_another_flow_on_the_same_registration(
    env: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env, "_providers", None)
    reg_id = await _shared_registration(env)
    cred_u1 = await _pending_registration_credential(
        env, owner_id=OWNER_ID, registration_id=reg_id, name="U1 Vault"
    )
    cred_u2 = await _pending_registration_credential(
        env, owner_id=OTHER_OWNER_ID, registration_id=reg_id, name="U2 Vault"
    )

    idp = _PkceEnforcingIdP()
    monkeypatch.setattr(
        "jentic_one.shared.http.idp_client.build_always_pinned_transport",
        lambda _egress: httpx.MockTransport(idp.handle_token_request),
    )

    async with _browser(env, OWNER) as browser_u1, _browser(env, OTHER_OWNER) as browser_u2:
        begin_u1 = await browser_u1.post(
            f"/credentials/{cred_u1}/connect", json={"scopes": ["read"]}
        )
        assert begin_u1.status_code == 200, begin_u1.text
        data_u1 = begin_u1.json()

        begin_u2 = await browser_u2.post(
            f"/credentials/{cred_u2}/connect", json={"scopes": ["read"]}
        )
        assert begin_u2.status_code == 200, begin_u2.text
        data_u2 = begin_u2.json()

        code_u1 = idp.issue_code_from_authorize_url(data_u1["authorize_url"], account_token="at-u1")

        # U2 attempts to complete U2's own callback using U2's state + cookie,
        # but with U1's authorization code.
        callback_u2 = await browser_u2.get(
            OAUTH_CALLBACK_PATH, params={"state": data_u2["state"], "code": code_u1}
        )
        assert _status(callback_u2) == "error"

        async with env.control_db.session() as session:
            assert await OAuthTokenRepository.get_by_credential(session, cred_u2) is None

        # U1's own flow still completes with U1's authorization code.
        callback_u1 = await browser_u1.get(
            OAUTH_CALLBACK_PATH, params={"state": data_u1["state"], "code": code_u1}
        )
        assert _status(callback_u1) == "ok"

        async with env.control_db.session() as session:
            token_u1 = await OAuthTokenRepository.get_by_credential(session, cred_u1)
            assert token_u1 is not None
            assert env.encryption.decrypt(token_u1.encrypted_access_token) == "at-u1"


async def test_standalone_and_session_paths_send_pkce_to_the_same_idp(
    env: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env, "_providers", None)
    await import_spec(env, OAUTH_SCHEME)
    reg_id = await _shared_registration(env)
    cred_standalone = await _pending_registration_credential(
        env, owner_id=OWNER_ID, registration_id=reg_id, name="Standalone Vault"
    )

    idp = _PkceEnforcingIdP()
    monkeypatch.setattr(
        "jentic_one.shared.http.idp_client.build_always_pinned_transport",
        lambda _egress: httpx.MockTransport(idp.handle_token_request),
    )

    async def _fake_echo(*_args: object, **_kwargs: object) -> identity_echo.IdentityEchoResult:
        return identity_echo.IdentityEchoResult(display="owner", raw={})

    monkeypatch.setattr(identity_echo, "echo_identity", _fake_echo)

    # 1. Standalone path
    async with _browser(env, OWNER) as browser:
        started = await browser.post(
            f"/credentials/{cred_standalone}/connect", json={"scopes": ["read"]}
        )
        assert started.status_code == 200, started.text
        standalone_url = started.json()["authorize_url"]
        standalone_query = parse_qs(urlsplit(standalone_url).query)
        assert standalone_query.get("code_challenge_method") == ["S256"]
        assert standalone_query.get("code_challenge")

        code_standalone = idp.issue_code_from_authorize_url(
            standalone_url, account_token="at-standalone"
        )
        cb_standalone = await browser.get(
            OAUTH_CALLBACK_PATH,
            params={"state": started.json()["state"], "code": code_standalone},
        )
        assert _status(cb_standalone) == "ok"

    # 2. Connect-session path against the same shared registration
    created = await connect(env, requested_scopes=["read"])
    async with _browser(env, OWNER) as browser:
        confirmed = await browser.post(
            f"/connect-sessions/{created.session_id}:confirm",
            json={
                "kind": "oauth",
                "confirmed_scopes": ["read"],
                "permission_rules": RULES,
                "expected_agent_id": AGENT_ID,
                "digest": await digest(env, created.session_id),
                "revision_id": await revision(env, created.session_id),
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        session_url = confirmed.json()["authorize_url"]
        session_query = parse_qs(urlsplit(session_url).query)
        assert session_query.get("code_challenge_method") == ["S256"]
        assert session_query.get("code_challenge")
        session_state = session_query["state"][-1]

        code_session = idp.issue_code_from_authorize_url(session_url, account_token="at-session")
        cb_session = await browser.get(
            OAUTH_CALLBACK_PATH,
            params={"state": session_state, "code": code_session},
        )
        assert _status(cb_session) == "ok"

    assert len(idp.token_requests) == 2
    assert all("code_verifier" in req for req in idp.token_requests)
