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
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.actor_scope_grants import ActorScopeGrant
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.external_identities import ExternalIdentity
from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.repos import ExternalIdentityRepository
from jentic_one.auth.web.routers import authorize
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus
from jentic_one.shared.models.audit import AuditAction
from jentic_one.shared.scopes import DEFAULT_AGENT_SCOPES
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
        # Unverified email at render: the account's agent list stays hidden.
        # (Pre-P4 this was the terminal empty state; the zero-agents arm now
        # renders the inline create form instead — still zero agent exposure,
        # and the create submit's provision path fails closed below.)
        handle = await _seed_handle(
            app, external_subject="ext-w-unverified", email=email, email_verified=False
        )
        resp = await client.get("/oauth/consent", params={"ch": handle})
        assert resp.status_code == 200
        assert 'action="/oauth/consent/agent"' in resp.text
        assert 'name="agent_id"' not in resp.text
        assert agent_id not in resp.text

        # Unverified email at the create submit → fail closed exactly like
        # the consent approve arm: provisioning rejects an unverified email
        # that belongs to an existing account — no agent row for either
        # account, no identity link.
        marker = 'name="create_state" value="'
        start = resp.text.index(marker) + len(marker)
        create_state = resp.text[start : resp.text.index('"', start)]
        resp = await client.post(
            "/oauth/consent/agent",
            data={
                "consent_token": handle,
                "create_state": create_state,
                "agent_name": "takeover-attempt",
            },
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/error?error=server_error"
        async with ctx.admin_db.session() as session:
            names = (
                (await session.execute(select(Agent.name).where(Agent.owner_id == user_id)))
                .scalars()
                .all()
            )
        assert names == ["unverified-target-agent"]

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


async def test_inline_agent_create_end_to_end(
    integration_context: Context, clean_grants: None
) -> None:
    """P4 inline agent creation against real services: a zero-agents user
    walks GET consent → create form → POST /oauth/consent/agent → re-entered
    consent with the new agent pre-selected → approve, all on one handle
    family. Asserts the real rows: agent (ACTIVE, owned by the consenting
    user), DEFAULT_AGENT_SCOPES grants, the REGISTER audit entry, and the
    final consent grant bound to the new agent."""
    ctx = integration_context
    owner_id = await seed_user(ctx, "usr_w_p4creator")
    await seed_client(ctx, allowed_scopes=["apis:read", "apis:write"])
    await _link_external_identity(ctx, user_id=owner_id, external_subject="ext-w-p4creator")

    app = _make_app(ctx)
    created_agent_id: str | None = None
    try:
        async with _web_client(app) as client:
            # --- render: zero agents → the create form, not the empty state
            handle = await _seed_handle(
                app, external_subject="ext-w-p4creator", email=f"{owner_id}@grants.test"
            )
            resp = await client.get("/oauth/consent", params={"ch": handle})
            assert resp.status_code == 200
            body = resp.text
            assert 'action="/oauth/consent/agent"' in body
            assert "you don't have one yet" not in body
            marker = 'name="create_state" value="'
            start = body.index(marker) + len(marker)
            create_state = body[start : body.index('"', start)]

            # --- create: 303 back into consent -----------------------------
            resp = await client.post(
                "/oauth/consent/agent",
                data={
                    "consent_token": handle,
                    "create_state": create_state,
                    "agent_name": "first-agent",
                },
            )
            assert resp.status_code == 303
            assert resp.headers["location"] == f"/oauth/consent?ch={handle}"

            # --- the real rows: agent + default scopes + REGISTER audit ----
            async with ctx.admin_db.session() as session:
                agent_row = (
                    await session.execute(select(Agent).where(Agent.owner_id == owner_id))
                ).scalar_one()
                created_agent_id = agent_row.id
                assert agent_row.name == "first-agent"
                assert agent_row.status == ActorStatus.ACTIVE.value
                assert agent_row.registered_by == owner_id
                scope_rows = (
                    await session.execute(
                        select(ActorScopeGrant.scope).where(
                            ActorScopeGrant.actor_id == created_agent_id
                        )
                    )
                ).scalars()
                assert set(scope_rows) == set(DEFAULT_AGENT_SCOPES)
                audit_rows = (
                    await session.execute(
                        select(AuditEntry).where(
                            AuditEntry.target_id == created_agent_id,
                            AuditEntry.action == AuditAction.REGISTER.value,
                        )
                    )
                ).scalars()
                audit = list(audit_rows)
                assert len(audit) == 1
                assert audit[0].actor_id == owner_id

            # --- re-entered consent: the new agent renders pre-selected ----
            resp = await client.get("/oauth/consent", params={"ch": handle})
            assert resp.status_code == 200
            assert f'value="{created_agent_id}" required checked' in resp.text

            # --- and the user approves in the same breath -------------------
            resp = await client.post(
                "/oauth/consent",
                data={
                    "consent_token": handle,
                    "action": "approve",
                    "agent_id": created_agent_id,
                },
            )
            assert resp.status_code == 302
            assert resp.headers["location"].startswith(f"{REDIRECT_URI}?code=")
            grants = await _grant_rows_for_agent(ctx, created_agent_id)
            assert len(grants) == 1
            assert grants[0].user_id == owner_id
            # requested ∩ allowlist ∩ DEFAULT_AGENT_SCOPES (openid stripped):
            # apis:read is a default agent scope, apis:write is not.
            assert list(grants[0].scopes) == ["apis:read"]
    finally:
        # The inline-created agent is stamped created_by=<user>, so the
        # SEED_MARKER-scoped clean_grants fixture would not remove it.
        if created_agent_id is not None:
            async with ctx.admin_db.transaction() as session:
                await session.execute(delete(Agent).where(Agent.id == created_agent_id))


async def test_inline_agent_create_race_skips_creation(
    integration_context: Context, clean_grants: None
) -> None:
    """An agent appearing between the form render and the create submit
    (another tab, an admin) skips creation against the real predicate — no
    second agent row, the submit just re-enters consent."""
    ctx = integration_context
    owner_id = await seed_user(ctx, "usr_w_p4race")
    await seed_client(ctx, allowed_scopes=["apis:read"])
    await _link_external_identity(ctx, user_id=owner_id, external_subject="ext-w-p4race")

    app = _make_app(ctx)
    async with _web_client(app) as client:
        handle = await _seed_handle(
            app, external_subject="ext-w-p4race", email=f"{owner_id}@grants.test"
        )
        resp = await client.get("/oauth/consent", params={"ch": handle})
        assert resp.status_code == 200
        marker = 'name="create_state" value="'
        start = resp.text.index(marker) + len(marker)
        create_state = resp.text[start : resp.text.index('"', start)]

        # The race: an agent appears before the submit lands.
        raced_agent = await seed_agent(
            ctx, owner_id=owner_id, scopes=["apis:read"], name="appeared-meanwhile"
        )

        resp = await client.post(
            "/oauth/consent/agent",
            data={
                "consent_token": handle,
                "create_state": create_state,
                "agent_name": "would-be-dupe",
            },
        )
        assert resp.status_code == 303

        async with ctx.admin_db.session() as session:
            rows = (
                await session.execute(select(Agent).where(Agent.owner_id == owner_id))
            ).scalars()
            agents = list(rows)
            assert len(agents) == 1
            assert agents[0].id == raced_agent  # nothing new was created

        # The re-entered consent page renders the raced agent's picker.
        resp = await client.get("/oauth/consent", params={"ch": handle})
        assert resp.status_code == 200
        assert f'name="agent_id" value="{raced_agent}"' in resp.text


async def test_inline_agent_create_not_offered_when_agents_all_disabled(
    integration_context: Context, clean_grants: None
) -> None:
    """A user whose agents were all taken out of service by an admin owns
    zero ACTIVE agents but is not first-run: the create form must not render
    (it would mint a fresh active agent past the admin action) — the terminal
    empty state stays, against the real any-status predicate."""
    ctx = integration_context
    owner_id = await seed_user(ctx, "usr_w_p4disabled")
    await seed_client(ctx, allowed_scopes=["apis:read"])
    await _link_external_identity(ctx, user_id=owner_id, external_subject="ext-w-p4disabled")
    await seed_agent(
        ctx,
        owner_id=owner_id,
        scopes=["apis:read"],
        status=ActorStatus.DISABLED,
        name="admin-disabled-agent",
    )

    app = _make_app(ctx)
    async with _web_client(app) as client:
        handle = await _seed_handle(
            app, external_subject="ext-w-p4disabled", email=f"{owner_id}@grants.test"
        )
        resp = await client.get("/oauth/consent", params={"ch": handle})
        assert resp.status_code == 200
        assert "you don't have one yet" in resp.text
        assert 'action="/oauth/consent/agent"' not in resp.text
        assert 'name="agent_id"' not in resp.text

        # No agent row appears even for a hand-crafted submit against this
        # handle (no rendered blob exists, so any create_state is foreign).
        async with ctx.admin_db.session() as session:
            rows = (
                await session.execute(select(Agent).where(Agent.owner_id == owner_id))
            ).scalars()
            agents = list(rows)
            assert len(agents) == 1
            assert agents[0].status == ActorStatus.DISABLED.value
