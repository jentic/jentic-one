"""Integration tests: the local-account login form on the /authorize flow (#1276).

Runs the whole gate-on path against the real routers and databases — GET
/authorize → 302 /login → form → POST credentials → code issuance (platform
client) or consent rejoin (registered third-party client) → POST /oauth/token
with the PKCE verifier — plus the shared-lockout pin: failed form submits
increment the same ``AuthService.authenticate`` counter as ``POST /auth/login``.

Only config is mutated (gate on, IdP off, one extra platform client), and it
is restored — AppConfig is shared session state.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.repos import OAuthClientRepository, UserSecretRepository
from jentic_one.admin.services._support.passwords import hash_password
from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import authorize, local_login, oauth
from jentic_one.shared.config import PlatformClientConfig, SigningKeyConfig
from jentic_one.shared.context import Context
from jentic_one.shared.state.backend import MemoryStateBackend
from tests.integration.auth.seeds import (
    CODE_VERIFIER,
    SEED_MARKER,
    code_challenge,
    seed_agent,
    seed_user,
)

pytestmark = pytest.mark.integration

_PLATFORM_CLIENT_ID = "local-login-platform"
_PLATFORM_REDIRECT = "https://platform.test.local/cb"
_THIRD_PARTY_CLIENT_ID = "oc_local_login_test"
_THIRD_PARTY_REDIRECT = "https://thirdparty.test.local/cb"
_PASSWORD = "correct horse battery staple"


@pytest.fixture()
def local_login_ctx(integration_context: Context) -> Generator[Context, None, None]:
    """Integration context with the local-login gate ON and the IdP OFF."""
    auth_cfg = integration_context.config.auth
    prior_enabled = auth_cfg.local_login.enabled
    prior_idp_enabled = auth_cfg.idp.enabled
    prior_signing = auth_cfg.id_signing
    auth_cfg.local_login.enabled = True
    auth_cfg.idp.enabled = False
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    ).decode()
    auth_cfg.id_signing = [
        SigningKeyConfig(kid="local-login-test-key", private_key_pem=pem)  # type: ignore[arg-type]
    ]
    auth_cfg.platform_clients.append(
        PlatformClientConfig(client_id=_PLATFORM_CLIENT_ID, redirect_uris=[_PLATFORM_REDIRECT])
    )
    yield integration_context
    auth_cfg.local_login.enabled = prior_enabled
    auth_cfg.idp.enabled = prior_idp_enabled
    auth_cfg.id_signing = prior_signing
    auth_cfg.platform_clients = [
        pc for pc in auth_cfg.platform_clients if pc.client_id != _PLATFORM_CLIENT_ID
    ]


@pytest.fixture()
async def clean_user_secrets(integration_context: Context) -> AsyncGenerator[None, None]:
    """Remove password rows seeded by this suite (clean_grants only covers users)."""

    async def _clean() -> None:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(delete(UserSecret).where(UserSecret.created_by == SEED_MARKER))

    await _clean()
    yield
    await _clean()


def _make_app(ctx: Context) -> FastAPI:
    """Authorize + login + token routers wired to the REAL context."""
    app = FastAPI()
    app.include_router(authorize.router)
    app.include_router(local_login.router)
    app.include_router(oauth.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.state.ctx = ctx
    app.state.auth_state_backend = MemoryStateBackend()
    return app


def _web_client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


async def _seed_password_user(ctx: Context, user_id: str) -> tuple[str, str]:
    """Seed a user with a password; returns (user_id, email)."""
    uid = await seed_user(ctx, user_id)
    async with ctx.admin_db.session() as session:
        await UserSecretRepository.set_password_hash(
            session, uid, password_hash=hash_password(_PASSWORD), created_by=SEED_MARKER
        )
        await session.commit()
    return uid, f"{uid}@grants.test"


async def _seed_third_party_client(ctx: Context) -> str:
    async with ctx.admin_db.session() as session:
        client = await OAuthClientRepository.create(
            session,
            client_id=_THIRD_PARTY_CLIENT_ID,
            name="Local Login Third Party",
            redirect_uris=[_THIRD_PARTY_REDIRECT],
            client_secret_hash=None,
            token_endpoint_auth_method="none",
            created_by=SEED_MARKER,
        )
        await session.commit()
        return client.client_id


def _form_fields(html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name in ("ls", "csrf"):
        marker = f'name="{name}" value="'
        start = html.index(marker) + len(marker)
        fields[name] = html[start : html.index('"', start)]
    return fields


async def _walk_to_login_form(
    client: AsyncClient, *, client_id: str, redirect_uri: str, scope: str = "apis:read"
) -> dict[str, str]:
    """GET /authorize (no IdP, gate on) → follow to the form → hidden fields."""
    resp = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge(CODE_VERIFIER),
            "code_challenge_method": "S256",
            "scope": scope,
            "state": "local-state-1",
        },
    )
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    assert location.startswith("/login?ls="), location

    form = await client.get(location)
    assert form.status_code == 200, form.text
    return _form_fields(form.text)


async def test_full_platform_round_trip_to_token(
    local_login_ctx: Context, clean_grants: None, clean_user_secrets: None
) -> None:
    """authorize → login form → credentials → code → PKCE token exchange,
    with zero IdP configured and no JWT minted along the way."""
    ctx = local_login_ctx
    _user_id, email = await _seed_password_user(ctx, "usr_local_login_platform")

    app = _make_app(ctx)
    async with _web_client(app) as client:
        fields = await _walk_to_login_form(
            client, client_id=_PLATFORM_CLIENT_ID, redirect_uri=_PLATFORM_REDIRECT
        )

        resp = await client.post("/login", data={"email": email, "password": _PASSWORD, **fields})
        assert resp.status_code == 302, resp.text
        location = resp.headers["location"]
        assert location.startswith(f"{_PLATFORM_REDIRECT}?"), location
        query = parse_qs(urlsplit(location).query)
        assert query["state"] == ["local-state-1"]
        code = query["code"][0]

        token = await client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": CODE_VERIFIER,
                "redirect_uri": _PLATFORM_REDIRECT,
                "client_id": _PLATFORM_CLIENT_ID,
            },
        )
        assert token.status_code == 200, token.text
        body = token.json()
        assert body["access_token"]
        assert body["refresh_token"]


async def test_third_party_rejoins_consent_then_token(
    local_login_ctx: Context, clean_grants: None, clean_user_secrets: None
) -> None:
    """Registered third-party client: login rejoins at the consent screen —
    the same consent handle/approve/deny surface the IdP path uses."""
    ctx = local_login_ctx
    _user_id, email = await _seed_password_user(ctx, "usr_local_login_3p")
    await _seed_third_party_client(ctx)

    app = _make_app(ctx)
    async with _web_client(app) as client:
        fields = await _walk_to_login_form(
            client, client_id=_THIRD_PARTY_CLIENT_ID, redirect_uri=_THIRD_PARTY_REDIRECT
        )

        resp = await client.post("/login", data={"email": email, "password": _PASSWORD, **fields})
        assert resp.status_code == 302, resp.text
        consent_url = resp.headers["location"]
        assert consent_url.startswith("/oauth/consent?ch="), consent_url
        handle = parse_qs(urlsplit(consent_url).query)["ch"][0]

        # The consent page renders for the locally-authenticated user.
        page = await client.get(consent_url)
        assert page.status_code == 200, page.text
        assert email in page.text

        # Approve → code lands on the third-party redirect; exchange succeeds.
        approve = await client.post(
            "/oauth/consent", data={"consent_token": handle, "action": "approve"}
        )
        assert approve.status_code == 302, approve.text
        location = approve.headers["location"]
        assert location.startswith(f"{_THIRD_PARTY_REDIRECT}?"), location
        code = parse_qs(urlsplit(location).query)["code"][0]

        token = await client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": CODE_VERIFIER,
                "redirect_uri": _THIRD_PARTY_REDIRECT,
                "client_id": _THIRD_PARTY_CLIENT_ID,
            },
        )
        assert token.status_code == 200, token.text


async def test_third_party_deny_bounces_without_code(
    local_login_ctx: Context, clean_grants: None, clean_user_secrets: None
) -> None:
    """Deny on the rejoined consent screen → access_denied, no code minted."""
    ctx = local_login_ctx
    _, email = await _seed_password_user(ctx, "usr_local_login_deny")
    await _seed_third_party_client(ctx)

    app = _make_app(ctx)
    async with _web_client(app) as client:
        fields = await _walk_to_login_form(
            client, client_id=_THIRD_PARTY_CLIENT_ID, redirect_uri=_THIRD_PARTY_REDIRECT
        )
        resp = await client.post("/login", data={"email": email, "password": _PASSWORD, **fields})
        handle = parse_qs(urlsplit(resp.headers["location"]).query)["ch"][0]

        deny = await client.post("/oauth/consent", data={"consent_token": handle, "action": "deny"})
        assert deny.status_code == 302
        query = parse_qs(urlsplit(deny.headers["location"]).query)
        assert query["error"] == ["access_denied"]
        assert "code" not in query


async def test_lockout_is_shared_with_password_login(
    local_login_ctx: Context, clean_grants: None, clean_user_secrets: None
) -> None:
    """Form submits drive the same failed-login counter and lockout as
    ``POST /auth/login`` — and every failure renders one generic message."""
    ctx = local_login_ctx
    threshold = ctx.config.admin.auth.failed_login_lockout_threshold
    _, email = await _seed_password_user(ctx, "usr_local_login_lockout")

    app = _make_app(ctx)
    async with _web_client(app) as client:
        for _ in range(threshold):
            fields = await _walk_to_login_form(
                client, client_id=_PLATFORM_CLIENT_ID, redirect_uri=_PLATFORM_REDIRECT
            )
            resp = await client.post(
                "/login", data={"email": email, "password": "wrong-password", **fields}
            )
            assert resp.status_code == 200
            assert "Invalid email or password." in resp.text

        # Locked now: even the CORRECT password renders the same generic
        # failure (lockout is indistinguishable from a bad password).
        fields = await _walk_to_login_form(
            client, client_id=_PLATFORM_CLIENT_ID, redirect_uri=_PLATFORM_REDIRECT
        )
        resp = await client.post("/login", data={"email": email, "password": _PASSWORD, **fields})
        assert resp.status_code == 200
        assert "Invalid email or password." in resp.text


async def test_agent_model_client_gets_picker_and_grant(
    local_login_ctx: Context, clean_grants: None, clean_user_secrets: None
) -> None:
    """consent_model='agent' third-party client: the locally-authenticated
    user gets the agent picker (no IdP claims to resolve) and the approve
    path mints a grant-stamped code."""
    ctx = local_login_ctx
    user_id, email = await _seed_password_user(ctx, "usr_local_login_agent")
    agent_id = await seed_agent(
        ctx, owner_id=user_id, scopes=["apis:read"], name="local-login-agent"
    )
    async with ctx.admin_db.session() as session:
        await OAuthClientRepository.create(
            session,
            client_id=_THIRD_PARTY_CLIENT_ID,
            name="Local Login Agent App",
            redirect_uris=[_THIRD_PARTY_REDIRECT],
            client_secret_hash=None,
            token_endpoint_auth_method="none",
            consent_model="agent",
            created_by=SEED_MARKER,
        )
        await session.commit()

    app = _make_app(ctx)
    async with _web_client(app) as client:
        fields = await _walk_to_login_form(
            client, client_id=_THIRD_PARTY_CLIENT_ID, redirect_uri=_THIRD_PARTY_REDIRECT
        )
        resp = await client.post("/login", data={"email": email, "password": _PASSWORD, **fields})
        assert resp.status_code == 302, resp.text
        consent_url = resp.headers["location"]
        handle = parse_qs(urlsplit(consent_url).query)["ch"][0]

        # The picker renders — user resolution came from local_user_id, not
        # claims (there are none on this handle).
        page = await client.get(consent_url)
        assert page.status_code == 200, page.text
        assert "local-login-agent" in page.text

        approve = await client.post(
            "/oauth/consent",
            data={"consent_token": handle, "action": "approve", "agent_id": agent_id},
        )
        assert approve.status_code == 302, approve.text
        location = approve.headers["location"]
        assert location.startswith(f"{_THIRD_PARTY_REDIRECT}?"), location
        code = parse_qs(urlsplit(location).query)["code"][0]

        token = await client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": CODE_VERIFIER,
                "redirect_uri": _THIRD_PARTY_REDIRECT,
                "client_id": _THIRD_PARTY_CLIENT_ID,
            },
        )
        assert token.status_code == 200, token.text
        # Agent-channel exchange: no OIDC identity (D11).
        assert token.json().get("id_token") is None


async def test_gate_off_form_is_404_and_authorize_unchanged(
    integration_context: Context, clean_grants: None
) -> None:
    """Default config: /login does not exist and /authorize keeps its
    no-IdP server_error contract (when the IdP is off)."""
    ctx = integration_context
    assert ctx.config.auth.local_login.enabled is False
    prior_idp = ctx.config.auth.idp.enabled
    ctx.config.auth.idp.enabled = False
    ctx.config.auth.platform_clients.append(
        PlatformClientConfig(client_id=_PLATFORM_CLIENT_ID, redirect_uris=[_PLATFORM_REDIRECT])
    )
    try:
        app = _make_app(ctx)
        async with _web_client(app) as client:
            resp = await client.get("/login", params={"ls": "anything"})
            assert resp.status_code == 404
            assert resp.json() == {"detail": "Not Found"}

            resp = await client.get(
                "/authorize",
                params={
                    "response_type": "code",
                    "client_id": _PLATFORM_CLIENT_ID,
                    "redirect_uri": _PLATFORM_REDIRECT,
                    "code_challenge": code_challenge(CODE_VERIFIER),
                    "code_challenge_method": "S256",
                    "state": "s1",
                },
            )
            assert resp.status_code == 302
            query = parse_qs(urlsplit(resp.headers["location"]).query)
            assert query["error"] == ["server_error"]
    finally:
        ctx.config.auth.idp.enabled = prior_idp
        ctx.config.auth.platform_clients = [
            pc for pc in ctx.config.auth.platform_clients if pc.client_id != _PLATFORM_CLIENT_ID
        ]
