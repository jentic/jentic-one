"""Integration test: the RFC 8252 §7.1 private-use redirect flow, end to end.

Issue #1245 (found in v0.38.0 E2E T7): Cursor's MCP OAuth integration
registers via anonymous DCR with ``redirect_uris:
["cursor://anysphere.cursor-mcp/oauth/callback"]`` and was hard-blocked by the
https-only redirect validator. This suite runs the whole loosened path against
the real routers and database — DCR register → admin approve → /authorize →
consent → /oauth/token — with a ``cursor://`` redirect, and pins the two
boundaries that must NOT loosen: the admin client-creation door stays
https-only, and PKCE (S256) stays mandatory regardless of redirect scheme.

Only the IdP round-trip is replaced, by the established consent-web pattern:
the consent handle the callback would have written is seeded directly.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import AsyncGenerator, Generator
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.repos import ExternalIdentityRepository
from jentic_one.admin.services.errors import InvalidInputError
from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import authorize, oauth, oauth_client_registration
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.events import EventType
from jentic_one.shared.models.oauth_clients import OAuthClientApprovalStatus
from jentic_one.shared.state.backend import MemoryStateBackend
from tests.integration.auth.seeds import CODE_VERIFIER, code_challenge, seed_agent, seed_user

pytestmark = pytest.mark.integration

_ADMIN = Identity(sub="usr_native_admin", email="native-admin@test.local")
_CURSOR_REDIRECT = "cursor://anysphere.cursor-mcp/oauth/callback"
_IDP_AUTHORIZE = "https://idp.test.local/authorize"
_DCR_ACTOR = "dcr"


@pytest.fixture()
def native_flow_ctx(integration_context: Context) -> Generator[Context, None, None]:
    """Integration context with the DCR door open, the approval queue on, and
    a (never-contacted) IdP configured so GET /authorize can build its hop.

    All mutated config is restored — AppConfig is shared session state.
    """
    oauth_cfg = integration_context.config.server.mcp.oauth
    idp_cfg = integration_context.config.auth.idp
    prior = (
        oauth_cfg.enabled,
        oauth_cfg.auto_approve_clients,
        idp_cfg.enabled,
        idp_cfg.authorization_endpoint,
        idp_cfg.client_id,
    )
    oauth_cfg.enabled = True
    oauth_cfg.auto_approve_clients = False
    idp_cfg.enabled = True
    idp_cfg.authorization_endpoint = _IDP_AUTHORIZE
    idp_cfg.client_id = "upstream-idp-client"
    yield integration_context
    (
        oauth_cfg.enabled,
        oauth_cfg.auto_approve_clients,
        idp_cfg.enabled,
        idp_cfg.authorization_endpoint,
        idp_cfg.client_id,
    ) = prior


@pytest.fixture()
async def clean_dcr_clients(integration_context: Context) -> AsyncGenerator[None, None]:
    """Remove DCR-registered client rows plus their audit entries and events.

    ``clean_grants`` (the shared conftest fixture) only removes clients seeded
    with ``created_by=SEED_MARKER``; the DCR front door stamps
    ``created_by='dcr'``, so those rows need their own cleanup.
    """

    async def _clean() -> None:
        async with integration_context.admin_db.transaction() as session:
            result = await session.execute(
                select(OAuthClient.id).where(OAuthClient.created_by == _DCR_ACTOR)
            )
            ids = [row[0] for row in result.all()]
            if ids:
                await session.execute(delete(AuditEntry).where(AuditEntry.target_id.in_(ids)))
            await session.execute(
                delete(Event).where(
                    Event.type.in_(
                        [EventType.OAUTH_CLIENT_REGISTERED, EventType.OAUTH_CLIENT_APPROVED]
                    )
                )
            )
            await session.execute(delete(OAuthClient).where(OAuthClient.created_by == _DCR_ACTOR))

    await _clean()
    yield
    await _clean()


def _make_app(ctx: Context) -> FastAPI:
    """DCR + authorize + token routers wired to the REAL context."""
    app = FastAPI()
    app.include_router(oauth_client_registration.router)
    app.include_router(authorize.router)
    app.include_router(oauth.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.state.ctx = ctx
    app.state.auth_state_backend = MemoryStateBackend()
    return app


def _web_client(app: FastAPI) -> AsyncClient:
    # httpx+ASGITransport (not TestClient) so the handlers run on the same
    # event loop as the session-scoped DB engines.
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


async def _register_cursor_client(client: AsyncClient) -> str:
    """Anonymous DCR with Cursor's real-world metadata; returns the client_id."""
    resp = await client.post(
        "/oauth-clients",
        json={
            "client_name": "Cursor",
            "redirect_uris": [_CURSOR_REDIRECT],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "software_id": "anysphere.cursor-mcp",
            "application_type": "native",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["redirect_uris"] == [_CURSOR_REDIRECT]
    return str(body["client_id"])


async def _approve_client(ctx: Context, client_id: str) -> None:
    async with ctx.admin_db.session() as session:
        row = (
            await session.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
        ).scalar_one()
        assert row.approval_status == OAuthClientApprovalStatus.PENDING.value
        row_id = row.id
    await OAuthClientService(ctx).approve(row_id, identity=_ADMIN)


async def _seed_consent_handle(
    app: FastAPI,
    *,
    client_id: str,
    external_subject: str,
    email: str,
    state: str,
    scope: str = "openid apis:read",
) -> str:
    """Write the consent handle the IdP callback would have stored."""
    handle = secrets.token_urlsafe(16)
    payload = json.dumps(
        {
            "claims": {
                "external_subject": external_subject,
                "email": email,
                "email_verified": True,
                "first_name": "Native",
                "last_name": "Flow",
            },
            "redirect_uri": _CURSOR_REDIRECT,
            "original_state": state,
            "client_id": client_id,
            "code_challenge": code_challenge(CODE_VERIFIER),
            "scope": scope,
            "nonce": None,
            "client_name": "Cursor",
            "client_description": None,
            "user_email": email,
            "iat": int(time.time()),
        }
    ).encode()
    await app.state.auth_state_backend.set(f"consent-handle:{handle}", payload, ttl_s=300.0)
    return handle


async def _link_external_identity(ctx: Context, *, user_id: str, external_subject: str) -> None:
    async with ctx.admin_db.session() as session:
        await ExternalIdentityRepository.create(
            session,
            provider=ctx.config.auth.idp.provider,
            external_subject=external_subject,
            user_id=user_id,
            email=f"{user_id}@grants.test",
            created_by=user_id,
        )
        await session.commit()


async def _mint_code_via_consent(
    app: FastAPI,
    client: AsyncClient,
    *,
    client_id: str,
    external_subject: str,
    email: str,
    agent_id: str,
    state: str,
) -> str:
    """Consent-approve with a cursor:// redirect; returns the minted code."""
    handle = await _seed_consent_handle(
        app,
        client_id=client_id,
        external_subject=external_subject,
        email=email,
        state=state,
    )
    resp = await client.post(
        "/oauth/consent",
        data={"consent_token": handle, "action": "approve", "agent_id": agent_id},
    )
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    # The 302 target IS the private-use redirect, carrying code + state.
    assert location.startswith(f"{_CURSOR_REDIRECT}?")
    query = parse_qs(urlsplit(location).query)
    assert query["state"] == [state]
    assert query["code"], location
    return query["code"][0]


async def test_full_dcr_to_token_flow_with_private_use_redirect(
    native_flow_ctx: Context, clean_grants: None, clean_dcr_clients: None
) -> None:
    """DCR register → approve → /authorize → consent → /oauth/token, all with
    Cursor's ``cursor://`` redirect (RFC 8252 §7.1)."""
    ctx = native_flow_ctx
    owner_id = await seed_user(ctx, "usr_native_owner")
    agent_id = await seed_agent(
        ctx, owner_id=owner_id, scopes=["apis:read"], name="native-flow-agent"
    )
    await _link_external_identity(ctx, user_id=owner_id, external_subject="ext-native-owner")

    app = _make_app(ctx)
    async with _web_client(app) as client:
        # 1 — anonymous DCR accepts the private-use redirect (was: 400).
        client_id = await _register_cursor_client(client)

        # 2 — the row lands pending; the admin approve verb activates it.
        await _approve_client(ctx, client_id)

        # 3 — /authorize accepts the exact-match cursor:// redirect and hops
        # to the IdP (the redirect target passed validation before the hop).
        authorize_params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": _CURSOR_REDIRECT,
            "code_challenge": code_challenge(CODE_VERIFIER),
            "code_challenge_method": "S256",
            "scope": "apis:read",
            "state": "native-state-1",
        }
        resp = await client.get("/authorize", params=authorize_params)
        assert resp.status_code == 302
        assert resp.headers["location"].startswith(f"{_IDP_AUTHORIZE}?")

        # 3b — exact-string matching has no normalization loopholes: a
        # variant of the registered URI is bounced before any redirect.
        for attack in (
            "cursor://anysphere.cursor-mcp/oauth/callback/",
            "cursor://anysphere.cursor-mcp/oauth/callback?extra=1",
            "CURSOR://anysphere.cursor-mcp/oauth/callback",
            "cursor://anysphere.cursor-mcp.evil.example/oauth/callback",
        ):
            resp = await client.get(
                "/authorize", params={**authorize_params, "redirect_uri": attack}
            )
            assert resp.status_code == 302
            assert resp.headers["location"] == "/error?error=invalid_redirect_uri", attack

        # 4 — consent approve 302s to the private-use redirect with code+state.
        code = await _mint_code_via_consent(
            app,
            client,
            client_id=client_id,
            external_subject="ext-native-owner",
            email=f"{owner_id}@grants.test",
            agent_id=agent_id,
            state="native-state-1",
        )

        # 5 — the public-client (secret-less) token exchange succeeds with the
        # PKCE verifier; the grant channel mints no id_token (D11).
        resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": CODE_VERIFIER,
                "redirect_uri": _CURSOR_REDIRECT,
                "client_id": client_id,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["access_token"]
        assert body["refresh_token"]
        assert body.get("id_token") is None


async def test_pkce_stays_mandatory_for_private_use_redirect_clients(
    native_flow_ctx: Context, clean_grants: None, clean_dcr_clients: None
) -> None:
    """Pin: loosening the redirect scheme never loosens PKCE (the compensating
    control) — S256-only at /authorize, verifier checked at /oauth/token."""
    ctx = native_flow_ctx
    owner_id = await seed_user(ctx, "usr_native_pkce")
    agent_id = await seed_agent(
        ctx, owner_id=owner_id, scopes=["apis:read"], name="native-pkce-agent"
    )
    await _link_external_identity(ctx, user_id=owner_id, external_subject="ext-native-pkce")

    app = _make_app(ctx)
    async with _web_client(app) as client:
        client_id = await _register_cursor_client(client)
        await _approve_client(ctx, client_id)

        # /authorize rejects a non-S256 challenge method outright.
        resp = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": _CURSOR_REDIRECT,
                "code_challenge": code_challenge(CODE_VERIFIER),
                "code_challenge_method": "plain",
                "scope": "apis:read",
                "state": "pkce-state",
            },
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert location.startswith(f"{_CURSOR_REDIRECT}?")
        assert "error=invalid_request" in location
        assert "only+S256+is+supported" in location or "only%20S256%20is%20supported" in location

        # A wrong PKCE verifier fails the exchange…
        code = await _mint_code_via_consent(
            app,
            client,
            client_id=client_id,
            external_subject="ext-native-pkce",
            email=f"{owner_id}@grants.test",
            agent_id=agent_id,
            state="pkce-state",
        )
        resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": "wrong-verifier-wrong-verifier-wrong-verifier",
                "redirect_uri": _CURSOR_REDIRECT,
                "client_id": client_id,
            },
        )
        assert resp.status_code == 400, resp.text

        # …and a missing verifier never reaches the exchange at all.
        resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _CURSOR_REDIRECT,
                "client_id": client_id,
            },
        )
        assert resp.status_code == 400, resp.text


async def test_admin_client_creation_door_still_rejects_private_use_schemes(
    native_flow_ctx: Context, clean_grants: None, clean_dcr_clients: None
) -> None:
    """Pin: the RFC 8252 §7.1 allowance is DCR-only — the admin door keeps the
    strict https-or-loopback-http rule and writes no row for a cursor:// URI."""
    ctx = native_flow_ctx
    with pytest.raises(InvalidInputError, match="redirect_uri must use https or http"):
        await OAuthClientService(ctx).create(
            name="cursor-via-admin",
            redirect_uris=[_CURSOR_REDIRECT],
            identity=_ADMIN,
        )
    async with ctx.admin_db.session() as session:
        rows = (
            (
                await session.execute(
                    select(OAuthClient).where(OAuthClient.name == "cursor-via-admin")
                )
            )
            .scalars()
            .all()
        )
    assert list(rows) == []


async def test_dcr_http_door_rejects_dispatch_schemes_and_control_chars(
    native_flow_ctx: Context, clean_grants: None, clean_dcr_clients: None
) -> None:
    """Pin (review-1246 F1/F2 at the HTTP door): ``intent://`` and raw
    control-char/whitespace redirect URIs are 400s, and no row is written."""
    ctx = native_flow_ctx
    app = _make_app(ctx)
    async with _web_client(app) as client:
        for bad_redirect in (
            "intent://scan/",
            "cursor://h/cb\n",
            "cursor://h/cb\x00",
            " cursor://h/cb",
            "cursor://h/cb#",
        ):
            resp = await client.post(
                "/oauth-clients",
                json={
                    "client_name": "Hostile",
                    "redirect_uris": [bad_redirect],
                    "token_endpoint_auth_method": "none",
                },
            )
            assert resp.status_code == 400, (bad_redirect, resp.text)
    async with ctx.admin_db.session() as session:
        rows = (
            (await session.execute(select(OAuthClient).where(OAuthClient.name == "Hostile")))
            .scalars()
            .all()
        )
    assert list(rows) == []


async def test_mid_flow_redirect_narrowing_invalidates_inflight_code(
    native_flow_ctx: Context, clean_grants: None, clean_dcr_clients: None
) -> None:
    """review-1246 F6: an admin narrowing the client's redirect set inside the
    code TTL invalidates codes minted for the removed URI — the token leg
    re-validates against the client's *live* set, not just the code row."""
    ctx = native_flow_ctx
    owner_id = await seed_user(ctx, "usr_native_narrow")
    agent_id = await seed_agent(
        ctx, owner_id=owner_id, scopes=["apis:read"], name="native-narrow-agent"
    )
    await _link_external_identity(ctx, user_id=owner_id, external_subject="ext-native-narrow")

    app = _make_app(ctx)
    async with _web_client(app) as client:
        client_id = await _register_cursor_client(client)
        await _approve_client(ctx, client_id)

        code = await _mint_code_via_consent(
            app,
            client,
            client_id=client_id,
            external_subject="ext-native-narrow",
            email=f"{owner_id}@grants.test",
            agent_id=agent_id,
            state="narrow-state-1",
        )

        # Narrow the live redirect set out from under the in-flight code.
        # (Simulated at the row level: the admin PATCH door is strict and
        # cannot even re-submit a cursor:// set — see the strict-door pins.)
        async with ctx.admin_db.transaction() as session:
            row = (
                await session.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
            ).scalar_one()
            row.redirect_uris = ["https://replaced.example.com/cb"]

        resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": CODE_VERIFIER,
                "redirect_uri": _CURSOR_REDIRECT,
                "client_id": client_id,
            },
        )
        assert resp.status_code == 400, resp.text
