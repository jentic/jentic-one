"""Integration tests for grant revocation on agent ownership transfer (G10, #1222).

An ``oauth_client_grants`` row keys its ``:revoke`` predicate on the grant's
consenting ``user_id``, but agent ownership is transferable — without the
transfer sweep, the OLD owner's consent would keep authorizing an MCP client
to act as an agent that now belongs to someone else, and the NEW owner could
not revoke it self-serve. Decided policy (2026-09-02): the transfer revokes
every active grant on the agent in the transfer's own transaction, reusing
the manual ``:revoke`` body (row flip + token sweep + audit + event).

Covers the service seam (``AgentService.update_agent``) and the full REST
path (``PATCH /agents/{id}``), plus the fail-safe posture: a failed sweep
rolls the whole transfer back. The consent-vs-transfer race half
is covered too: ``create_grant`` locks the agent row and re-checks ownership
inside the mint transaction, so a consent approval racing a transfer is
refused instead of committing a live grant consented by the old owner.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.repos import AgentRepository
from jentic_one.auth.services.agent_auth_service import AgentAuthService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.errors import (
    AuthServiceError,
    ConsentAgentNotEligibleError,
    InvalidGrantError,
)
from jentic_one.auth.services.oauth_grant_service import (
    AGENT_TRANSFER_REVOCATION_REASON,
    OAuthGrantService,
)
from jentic_one.auth.services.token_service import TokenService
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import agents
from jentic_one.broker.repos.token_resolver import InProcessTokenResolver
from jentic_one.shared.auth.api_key_resolver import ApiKeyResolver
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.events import EventType
from jentic_one.shared.web.deps import resolve_identity
from tests.integration.auth import seeds

pytestmark = pytest.mark.integration

_CLIENT_ID_2 = "oc_grant_transfer_second"


def _admin_identity(admin_id: str) -> Identity:
    return Identity(
        sub=admin_id,
        email="admin@grants.test",
        permissions=["agents:write", "agents:read", "org:admin"],
    )


async def _transfer_revoked_events(ctx: Context, agent_id: str) -> list[Event]:
    """``oauth_grant.revoked`` events for one agent (events survive clean_grants)."""
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(Event).where(Event.type == EventType.OAUTH_GRANT_REVOKED)
        )
        return [e for e in result.scalars().all() if (e.data or {}).get("agent_id") == agent_id]


async def _revoke_audit_rows(ctx: Context, grant_ids: set[str]) -> list[AuditEntry]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(AuditEntry).where(
                AuditEntry.target_type == "oauth_grant",
                AuditEntry.target_id.in_(grant_ids),
                AuditEntry.action == "revoke",
            )
        )
        return list(result.scalars().all())


async def test_transfer_revokes_all_grants_sweeps_tokens_and_spares_siblings(
    integration_context: Context, clean_grants: None
) -> None:
    """Transfer → ALL active grants on the agent revoked (multiple clients),
    their tokens dead on BOTH resolvers, transfer-stamped audit + events
    written — while a grant on ANOTHER agent of the same owner stays live and
    the transferred agent's key channel keeps working."""
    ctx = integration_context
    old_owner = await seeds.seed_user(ctx, "usr_t_old")
    new_owner = await seeds.seed_user(ctx, "usr_t_new")
    admin_id = await seeds.seed_user(ctx, "usr_t_admin")
    agent_id = await seeds.seed_agent(ctx, owner_id=old_owner, scopes=["apis:read"])
    sibling_id = await seeds.seed_agent(
        ctx, owner_id=old_owner, scopes=["apis:read"], name="sibling-agent"
    )
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"])
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"], client_id=_CLIENT_ID_2)

    # Two grants (two clients) on the transferring agent, one on the sibling.
    grant_1, access_1, refresh_1, _ = await seeds.mint_grant_channel_tokens(
        ctx, user_id=old_owner, agent_id=agent_id, grant_scopes=["apis:read"]
    )
    grant_2, access_2, _refresh_2, _ = await seeds.mint_grant_channel_tokens(
        ctx,
        user_id=old_owner,
        agent_id=agent_id,
        grant_scopes=["apis:read"],
        client_id=_CLIENT_ID_2,
    )
    sibling_grant, sibling_access, _sibling_refresh, _ = await seeds.mint_grant_channel_tokens(
        ctx, user_id=old_owner, agent_id=sibling_id, grant_scopes=["apis:read"]
    )
    admin = _admin_identity(admin_id)
    api_key = await AgentAuthService(ctx).register_api_key(agent_id, identity=admin)

    view = await AgentService(ctx).update_agent(
        agent_id, update_data={"owner_id": new_owner}, identity=admin
    )
    assert view.owner_id == new_owner

    grant_svc = OAuthGrantService(ctx)
    for grant_id in (grant_1, grant_2):
        grant = await grant_svc.get_grant(grant_id)
        assert grant is not None and grant.status == "revoked"
        assert grant.revoked_at is not None
    sibling = await grant_svc.get_grant(sibling_grant)
    assert sibling is not None and sibling.status == "active"

    # Tokens dead on both resolvers; refresh fails closed; sibling stays live.
    token_svc = TokenService(ctx)
    broker = InProcessTokenResolver(ctx.admin_db)
    for access in (access_1, access_2):
        assert await token_svc.resolve_access_token(access) is None
        broker_resolved = await broker.resolve_access_token(access)
        assert broker_resolved is not None and broker_resolved.active is False
    with pytest.raises(InvalidGrantError):
        await token_svc.refresh(refresh_1, client_id=seeds.CLIENT_ID)
    sibling_resolved = await token_svc.resolve_access_token(sibling_access)
    assert sibling_resolved is not None and sibling_resolved.active is True

    # The key channel is untouched by the sweep — only consent grants die.
    key_identity = await ApiKeyResolver(ctx.admin_db).resolve(api_key)
    assert key_identity is not None and key_identity.sub == agent_id

    # Audit rows: one REVOKE per grant, stamped with the transfer reason and
    # attributed to the transferring admin.
    audits = await _revoke_audit_rows(ctx, {grant_1, grant_2})
    assert len(audits) == 2
    assert all(a.reason == "oauth grant revoked: agent ownership transferred" for a in audits)
    assert all(a.actor_id == admin_id for a in audits)

    # Events: oauth_grant.revoked per grant, data.reason distinguishing the
    # transfer cause from a manual :revoke.
    events = await _transfer_revoked_events(ctx, agent_id)
    revoked_grant_ids = {(e.data or {}).get("grant_id") for e in events}
    assert {grant_1, grant_2} <= revoked_grant_ids
    for event in events:
        if (event.data or {}).get("grant_id") in (grant_1, grant_2):
            assert (event.data or {}).get("reason") == AGENT_TRANSFER_REVOCATION_REASON
            assert event.created_by == admin_id


async def test_transfer_with_no_grants_is_a_noop_and_name_patch_spares_grants(
    integration_context: Context, clean_grants: None
) -> None:
    """A transfer with no active grants writes no grant audit/events; a
    non-owner PATCH (name) on an agent WITH grants leaves them live."""
    ctx = integration_context
    old_owner = await seeds.seed_user(ctx, "usr_t_noop_old")
    new_owner = await seeds.seed_user(ctx, "usr_t_noop_new")
    admin_id = await seeds.seed_user(ctx, "usr_t_noop_admin")
    bare_agent = await seeds.seed_agent(ctx, owner_id=old_owner, scopes=["apis:read"])
    granted_agent = await seeds.seed_agent(
        ctx, owner_id=old_owner, scopes=["apis:read"], name="granted-agent"
    )
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"])
    grant_id, access, _refresh, _ = await seeds.mint_grant_channel_tokens(
        ctx, user_id=old_owner, agent_id=granted_agent, grant_scopes=["apis:read"]
    )
    admin = _admin_identity(admin_id)
    svc = AgentService(ctx)

    view = await svc.update_agent(bare_agent, update_data={"owner_id": new_owner}, identity=admin)
    assert view.owner_id == new_owner
    assert await _transfer_revoked_events(ctx, bare_agent) == []

    view = await svc.update_agent(
        granted_agent, update_data={"name": "renamed-agent"}, identity=admin
    )
    assert view.name == "renamed-agent"
    grant = await OAuthGrantService(ctx).get_grant(grant_id)
    assert grant is not None and grant.status == "active"
    assert await TokenService(ctx).resolve_access_token(access) is not None


async def test_transfer_rolls_back_when_grant_revocation_fails(
    integration_context: Context, clean_grants: None
) -> None:
    """Fail-safe posture: if the sweep fails mid-transfer, the WHOLE transfer
    rolls back — no transferred agent is ever left with live grants, and no
    ownerless half-state is committed."""
    ctx = integration_context
    old_owner = await seeds.seed_user(ctx, "usr_t_rb_old")
    new_owner = await seeds.seed_user(ctx, "usr_t_rb_new")
    admin_id = await seeds.seed_user(ctx, "usr_t_rb_admin")
    agent_id = await seeds.seed_agent(ctx, owner_id=old_owner, scopes=["apis:read"])
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"])
    grant_id, access, _refresh, _ = await seeds.mint_grant_channel_tokens(
        ctx, user_id=old_owner, agent_id=agent_id, grant_scopes=["apis:read"]
    )

    # Inject a failure INSIDE the sweep (the token kill-switch step) — the
    # repository itself is real; only this one call blows up.
    with (
        patch(
            "jentic_one.auth.services.oauth_grant_service.AccessTokenRepository.revoke_by_grant",
            new=AsyncMock(side_effect=RuntimeError("sweep exploded")),
        ),
        pytest.raises(RuntimeError, match="sweep exploded"),
    ):
        await AgentService(ctx).update_agent(
            agent_id, update_data={"owner_id": new_owner}, identity=_admin_identity(admin_id)
        )

    # Owner write rolled back with the failed sweep; the grant + token live on.
    async with ctx.admin_db.session() as session:
        agent = await AgentRepository.get_by_id(session, agent_id)
    assert agent is not None and agent.owner_id == old_owner
    grant = await OAuthGrantService(ctx).get_grant(grant_id)
    assert grant is not None and grant.status == "active"
    assert await TokenService(ctx).resolve_access_token(access) is not None


async def test_rest_patch_owner_transfer_revokes_grants(
    integration_context: Context, clean_grants: None
) -> None:
    """The full REST path: PATCH /agents/{id} with a new owner_id runs the
    sweep — the only identity shim is the auth dependency override; router,
    service, repos, and DB are all real."""
    ctx = integration_context
    old_owner = await seeds.seed_user(ctx, "usr_t_rest_old")
    new_owner = await seeds.seed_user(ctx, "usr_t_rest_new")
    admin_id = await seeds.seed_user(ctx, "usr_t_rest_admin")
    agent_id = await seeds.seed_agent(ctx, owner_id=old_owner, scopes=["apis:read"])
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"])
    grant_id, access, _refresh, _ = await seeds.mint_grant_channel_tokens(
        ctx, user_id=old_owner, agent_id=agent_id, grant_scopes=["apis:read"]
    )

    app = FastAPI()
    app.include_router(agents.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.state.ctx = ctx
    app.dependency_overrides[resolve_identity] = lambda: _admin_identity(admin_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            resp = await client.patch(f"/agents/{agent_id}", json={"owner_id": new_owner})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["owner_id"] == new_owner
    grant = await OAuthGrantService(ctx).get_grant(grant_id)
    assert grant is not None and grant.status == "revoked"
    assert await TokenService(ctx).resolve_access_token(access) is None


async def test_consent_mint_racing_transfer_is_refused(
    integration_context: Context, clean_grants: None
) -> None:
    """Two-session race simulation: the consent screen's ownership
    validation passes, a transfer commits in between, and the mint is refused
    by the lock + re-check inside ``create_grant``'s transaction — no live
    grant consented by the old owner ever commits.

    NOTE: SQLite ignores FOR UPDATE, so on this backend the test proves the
    in-transaction ownership re-check; the row lock's serialization against a
    concurrent transfer transaction is proven by CI's Postgres leg.
    """
    ctx = integration_context
    old_owner = await seeds.seed_user(ctx, "usr_t_race_old")
    new_owner = await seeds.seed_user(ctx, "usr_t_race_new")
    admin_id = await seeds.seed_user(ctx, "usr_t_race_admin")
    agent_id = await seeds.seed_agent(ctx, owner_id=old_owner, scopes=["apis:read"])
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"])

    # t0 — the consent screen's server-side validation: the agent IS a valid
    # pick for the old owner (this is the unlocked read the race exploits).
    options = await AuthorizeService(ctx).list_consentable_agents(old_owner)
    assert any(o.id == agent_id for o in options)

    # t1 — the transfer commits between validation and mint (the departing
    # owner can hold the consent handle for up to 300 s).
    await AgentService(ctx).update_agent(
        agent_id, update_data={"owner_id": new_owner}, identity=_admin_identity(admin_id)
    )

    # t2 — the mint re-checks ownership inside its own transaction and refuses.
    with pytest.raises(ConsentAgentNotEligibleError):
        await OAuthGrantService(ctx).create_grant(
            user_id=old_owner,
            oauth_client_id=seeds.CLIENT_ID,
            agent_id=agent_id,
            scopes=["apis:read"],
            client_name="Grant Channel App",
        )

    # Nothing committed: no grant row (any status) exists for the agent.
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(OAuthClientGrant).where(OAuthClientGrant.agent_id == agent_id)
        )
        assert list(result.scalars().all()) == []


async def test_transfer_rolls_back_when_post_sweep_step_fails(
    integration_context: Context, clean_grants: None
) -> None:
    """Same-transaction pin (review F2): fail the step AFTER the sweep — the
    transfer's own ``record_audit`` — and assert from a fresh session that
    BOTH the owner change and the grant revocations rolled back. This test
    fails if the sweep ever moves to its own (committed) transaction, which
    the sweep-failure rollback test alone cannot distinguish."""
    ctx = integration_context
    old_owner = await seeds.seed_user(ctx, "usr_t_pin_old")
    new_owner = await seeds.seed_user(ctx, "usr_t_pin_new")
    admin_id = await seeds.seed_user(ctx, "usr_t_pin_admin")
    agent_id = await seeds.seed_agent(ctx, owner_id=old_owner, scopes=["apis:read"])
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"])
    grant_id, access, _refresh, _ = await seeds.mint_grant_channel_tokens(
        ctx, user_id=old_owner, agent_id=agent_id, grant_scopes=["apis:read"]
    )

    # Patch ONLY the transfer transaction's own audit call (the sweep's
    # per-grant audits go through oauth_grant_service's import and stay real).
    with (
        patch(
            "jentic_one.auth.services.agent_service.record_audit",
            new=AsyncMock(side_effect=RuntimeError("audit exploded")),
        ),
        pytest.raises(RuntimeError, match="audit exploded"),
    ):
        await AgentService(ctx).update_agent(
            agent_id, update_data={"owner_id": new_owner}, identity=_admin_identity(admin_id)
        )

    # Fresh session: owner unchanged AND the sweep's writes rolled back with it.
    async with ctx.admin_db.session() as session:
        agent = await AgentRepository.get_by_id(session, agent_id)
    assert agent is not None and agent.owner_id == old_owner
    grant = await OAuthGrantService(ctx).get_grant(grant_id)
    assert grant is not None and grant.status == "active"
    assert await TokenService(ctx).resolve_access_token(access) is not None


async def test_transfer_from_unowned_agent_runs_empty_sweep(
    integration_context: Context, clean_grants: None
) -> None:
    """The NULL→owner PATCH arm (review F5): assigning an owner to an unowned
    agent counts as a transfer (``None != new_owner``) and runs the sweep —
    an unowned agent can hold no grants, so it is an empty no-op: no crash,
    no grant audit/events, owner set."""
    ctx = integration_context
    old_owner = await seeds.seed_user(ctx, "usr_t_null_old")
    new_owner = await seeds.seed_user(ctx, "usr_t_null_new")
    admin_id = await seeds.seed_user(ctx, "usr_t_null_admin")
    agent_id = await seeds.seed_agent(ctx, owner_id=old_owner, scopes=["apis:read"])
    admin = _admin_identity(admin_id)
    svc = AgentService(ctx)

    # Detach the owner first (owner→NULL is itself a transfer; with no grants
    # it sweeps nothing) so the PATCH under test genuinely starts from NULL.
    view = await svc.update_agent(agent_id, update_data={"owner_id": None}, identity=admin)
    assert view.owner_id is None

    view = await svc.update_agent(agent_id, update_data={"owner_id": new_owner}, identity=admin)
    assert view.owner_id == new_owner
    assert await _transfer_revoked_events(ctx, agent_id) == []
