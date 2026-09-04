"""Web-level integration tests for the consent ownership predicate.

Coverage gap closed here: every unit-level consent web test mocks ``list_consentable_agents``
and the service-level integration suite enters below the predicate, so the
actual query that stops cross-user / pending / disabled agent binding — and
the ``resolve_existing_user_id`` ``email_verified`` guard — had no coverage
against real data. These tests run the real /oauth/consent GET + POST
handlers with real services against the integration database (no service or
repository mocking): only the IdP round-trip is replaced by seeding the
consent handle the callback would have written.
"""

from __future__ import annotations

import json
import secrets
import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from jentic_one.admin.core.schema.external_identities import ExternalIdentity
from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.repos import ExternalIdentityRepository
from jentic_one.auth.web.routers import authorize
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus
from jentic_one.shared.state.backend import MemoryStateBackend
from tests.integration.auth.seeds import (
    CLIENT_ID,
    REDIRECT_URI,
    seed_agent,
    seed_client,
    seed_user,
)

pytestmark = pytest.mark.integration


def _make_app(ctx: Context) -> FastAPI:
    """The authorize router wired to the REAL context — no mocked services."""
    app = FastAPI()
    app.include_router(authorize.router)
    app.state.ctx = ctx
    app.state.auth_state_backend = MemoryStateBackend()
    return app


def _web_client(app: FastAPI) -> AsyncClient:
    # httpx+ASGITransport (not TestClient) so the handlers run on the same
    # event loop as the session-scoped DB engines.
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


async def _seed_handle(
    app: FastAPI,
    *,
    external_subject: str,
    email: str,
    email_verified: bool = True,
    scope: str = "openid apis:read apis:write",
) -> str:
    """Write the consent handle the oauth_callback would have stored."""
    handle = secrets.token_urlsafe(16)
    payload = json.dumps(
        {
            "claims": {
                "external_subject": external_subject,
                "email": email,
                "email_verified": email_verified,
                "first_name": "Grant",
                "last_name": "Test",
            },
            "redirect_uri": REDIRECT_URI,
            "original_state": "xyz",
            "client_id": CLIENT_ID,
            "code_challenge": "challenge",
            "scope": scope,
            "nonce": None,
            "client_name": "Grant Channel App",
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


async def _grant_rows_for_agent(ctx: Context, agent_id: str) -> list[OAuthClientGrant]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(OAuthClientGrant).where(OAuthClientGrant.agent_id == agent_id)
        )
        return list(result.scalars().all())


async def test_ownership_predicate_at_render_and_submit(
    integration_context: Context, clean_grants: None
) -> None:
    """Only the consenting user's OWN ACTIVE agents are pickable at
    render and accepted at submit — asserted against the real query, with a
    second user's active agent and the user's own pending/disabled agents
    seeded in the database."""
    ctx = integration_context
    victim_id = await seed_user(ctx, "usr_w_victim")
    victim_agent = await seed_agent(
        ctx, owner_id=victim_id, scopes=["apis:read"], name="victim-active-agent"
    )
    owner_id = await seed_user(ctx, "usr_w_owner")
    mine_active = await seed_agent(
        ctx, owner_id=owner_id, scopes=["apis:read"], name="owner-active-agent"
    )
    mine_pending = await seed_agent(
        ctx,
        owner_id=owner_id,
        scopes=["apis:read"],
        status=ActorStatus.PENDING,
        name="owner-pending-agent",
    )
    mine_disabled = await seed_agent(
        ctx,
        owner_id=owner_id,
        scopes=["apis:read"],
        status=ActorStatus.DISABLED,
        name="owner-disabled-agent",
    )
    await seed_client(ctx, allowed_scopes=["apis:read", "apis:write"])
    await _link_external_identity(ctx, user_id=owner_id, external_subject="ext-w-owner")

    app = _make_app(ctx)
    async with _web_client(app) as client:
        # --- render: the picker lists exactly the owner's active agent -----
        handle = await _seed_handle(
            app, external_subject="ext-w-owner", email=f"{owner_id}@grants.test"
        )
        resp = await client.get("/oauth/consent", params={"ch": handle})
        assert resp.status_code == 200
        body = resp.text
        assert f'name="agent_id" value="{mine_active}"' in body
        for excluded in (victim_agent, mine_pending, mine_disabled):
            assert excluded not in body

        # --- submit: the same predicate rejects every non-bindable agent ---
        for bad_agent in (victim_agent, mine_pending, mine_disabled):
            handle = await _seed_handle(
                app, external_subject="ext-w-owner", email=f"{owner_id}@grants.test"
            )
            resp = await client.post(
                "/oauth/consent",
                data={"consent_token": handle, "action": "approve", "agent_id": bad_agent},
            )
            assert resp.status_code == 302
            assert resp.headers["location"] == "/error?error=invalid_agent_selection"
            assert await _grant_rows_for_agent(ctx, bad_agent) == []

        # --- and accepts the owner's own active agent -----------------------
        handle = await _seed_handle(
            app, external_subject="ext-w-owner", email=f"{owner_id}@grants.test"
        )
        resp = await client.post(
            "/oauth/consent",
            data={"consent_token": handle, "action": "approve", "agent_id": mine_active},
        )
        assert resp.status_code == 302
        assert resp.headers["location"].startswith(f"{REDIRECT_URI}?code=")
        grants = await _grant_rows_for_agent(ctx, mine_active)
        assert len(grants) == 1
        assert grants[0].user_id == owner_id
        assert grants[0].oauth_client_id == CLIENT_ID
        assert list(grants[0].scopes) == ["apis:read"]


async def test_unverified_email_never_exposes_another_accounts_agents(
    integration_context: Context, clean_grants: None
) -> None:
    """``resolve_existing_user_id`` email arm: claims with
    an unlinked subject resolve by email ONLY when the IdP asserts
    ``email_verified`` — an unverified email must not expose the matching
    account's agent list at render, and submit fails closed (the provision
    path rejects an unverified email that belongs to an existing account)."""
    ctx = integration_context
    user_id = await seed_user(ctx, "usr_w_unverified")
    agent_id = await seed_agent(
        ctx, owner_id=user_id, scopes=["apis:read"], name="unverified-target-agent"
    )
    await seed_client(ctx, allowed_scopes=["apis:read", "apis:write"])
    email = f"{user_id}@grants.test"

    app = _make_app(ctx)
    async with _web_client(app) as client:
        # Unverified email at render → empty state, the agent list stays hidden.
        handle = await _seed_handle(
            app, external_subject="ext-w-unverified", email=email, email_verified=False
        )
        resp = await client.get("/oauth/consent", params={"ch": handle})
        assert resp.status_code == 200
        assert "you don't have one yet" in resp.text
        assert agent_id not in resp.text

        # Unverified email at submit → fail closed: no grant, no identity link.
        handle = await _seed_handle(
            app, external_subject="ext-w-unverified", email=email, email_verified=False
        )
        resp = await client.post(
            "/oauth/consent",
            data={"consent_token": handle, "action": "approve", "agent_id": agent_id},
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/error?error=server_error"
        assert await _grant_rows_for_agent(ctx, agent_id) == []
        async with ctx.admin_db.session() as session:
            links = (await session.execute(select(ExternalIdentity))).scalars().all()
        assert links == []

        # Contrast arm: the SAME claims with email_verified=True resolve the
        # account and render its picker.
        handle = await _seed_handle(
            app, external_subject="ext-w-unverified", email=email, email_verified=True
        )
        resp = await client.get("/oauth/consent", params={"ch": handle})
        assert resp.status_code == 200
        assert f'name="agent_id" value="{agent_id}"' in resp.text


async def test_consent_handle_replay_rejected_on_agent_variant(
    integration_context: Context, clean_grants: None
) -> None:
    """Review F-5: replaying a consumed consent handle on the agent-picker
    variant mints nothing — exactly one grant row survives a double submit."""
    ctx = integration_context
    owner_id = await seed_user(ctx, "usr_w_replay")
    agent_id = await seed_agent(ctx, owner_id=owner_id, scopes=["apis:read"], name="replay-agent")
    await seed_client(ctx, allowed_scopes=["apis:read", "apis:write"])
    await _link_external_identity(ctx, user_id=owner_id, external_subject="ext-w-replay")

    app = _make_app(ctx)
    async with _web_client(app) as client:
        handle = await _seed_handle(
            app, external_subject="ext-w-replay", email=f"{owner_id}@grants.test"
        )
        form = {"consent_token": handle, "action": "approve", "agent_id": agent_id}

        first = await client.post("/oauth/consent", data=form)
        assert first.status_code == 302
        assert first.headers["location"].startswith(f"{REDIRECT_URI}?code=")

        replay = await client.post("/oauth/consent", data=form)
        assert replay.status_code == 302
        assert replay.headers["location"] == "/error?error=invalid_consent"

        assert len(await _grant_rows_for_agent(ctx, agent_id)) == 1
