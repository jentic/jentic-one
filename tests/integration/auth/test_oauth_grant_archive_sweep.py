"""Integration tests for grant revocation on agent archive (#1233, archive arm).

``AgentService.archive`` is terminal — ``ActorStatus`` has no exit from
``ARCHIVED`` — yet before this fix it swept scope grants and toolkit bindings
while leaving the agent's ``oauth_client_grants`` rows ``active`` forever.
That is not a live credential (both resolvers and refresh gate on
``agent.status == 'active'``), but every "active grants" listing/count — the
per-agent "Connected clients" panel, ``count_active_by_client``, the admin
cross-view — reported live consent on a dead agent. The fix reuses the G10
transfer sweep (``revoke_active_grants_for_agent``) inside the archive
transaction with an archive-specific cause stamp.

``disable`` deliberately does NOT sweep — DECIDED (#1233, disable arm):
disable is a reversible kill-switch, so grants go DORMANT (the enforcement
layer fail-closes everything while disabled) and re-enable restores the
standing consent without a new consent round — the GitHub app-suspension
model, not the Google Workspace app-block model. The honesty half of the
issue is fixed at the listing layer instead: client-level counts exclude,
and grant listings annotate, grants whose agent is non-active. The disable
test below PINS the decided behaviour.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.repos import AgentRepository
from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.admin.services.oauth_grant_admin_service import OAuthGrantAdminService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.services.oauth_grant_service import (
    AGENT_ARCHIVE_REVOCATION_REASON,
    OAuthGrantService,
)
from jentic_one.auth.services.token_service import TokenService
from jentic_one.broker.repos.token_resolver import InProcessTokenResolver
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.events import EventType
from tests.integration.auth import seeds

pytestmark = pytest.mark.integration


def _admin_identity(admin_id: str) -> Identity:
    return Identity(
        sub=admin_id,
        email="admin@archive-sweep.test",
        permissions=["agents:write", "agents:read", "org:admin"],
    )


async def _revoked_events(ctx: Context, agent_id: str) -> list[Event]:
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


async def test_archive_revokes_active_grants_and_fails_refresh_closed(
    integration_context: Context, clean_grants: None
) -> None:
    """Archive → the agent's active grant is revoked in the same mutation,
    its tokens are dead on BOTH resolvers, refresh fails closed, and the
    audit row + event carry the ARCHIVE stamp (never the transfer one)."""
    ctx = integration_context
    owner = await seeds.seed_user(ctx, "usr_arc_owner")
    admin_id = await seeds.seed_user(ctx, "usr_arc_admin")
    agent_id = await seeds.seed_agent(ctx, owner_id=owner, scopes=["apis:read"])
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"])
    grant_id, access, refresh, _ = await seeds.mint_grant_channel_tokens(
        ctx, user_id=owner, agent_id=agent_id, grant_scopes=["apis:read"]
    )

    await AgentService(ctx).archive(agent_id, identity=_admin_identity(admin_id))

    # The grant row itself is revoked — not merely masked by the status gate.
    grant = await OAuthGrantService(ctx).get_grant(grant_id)
    assert grant is not None and grant.status == "revoked"
    assert grant.revoked_at is not None

    # Tokens dead on both resolvers; refresh fails closed (the grant re-check
    # and the actor-active gate both refuse — either way InvalidGrantError).
    token_svc = TokenService(ctx)
    assert await token_svc.resolve_access_token(access) is None
    broker_resolved = await InProcessTokenResolver(ctx.admin_db).resolve_access_token(access)
    assert broker_resolved is not None and broker_resolved.active is False
    with pytest.raises(InvalidGrantError):
        await token_svc.refresh(refresh, client_id=seeds.CLIENT_ID)

    # Audit: one REVOKE for the grant, stamped with the archive reason and
    # attributed to the archiving admin.
    audits = await _revoke_audit_rows(ctx, {grant_id})
    assert len(audits) == 1
    assert audits[0].reason == "oauth grant revoked: agent archived"
    assert audits[0].actor_id == admin_id

    # Event: oauth_grant.revoked with data.reason distinguishing the archive
    # cause from a manual :revoke and from the G10 transfer sweep.
    events = await _revoked_events(ctx, agent_id)
    assert [e for e in events if (e.data or {}).get("grant_id") == grant_id]
    for event in events:
        if (event.data or {}).get("grant_id") == grant_id:
            assert (event.data or {}).get("reason") == AGENT_ARCHIVE_REVOCATION_REASON
            assert event.created_by == admin_id


async def test_disable_leaves_grants_active_by_design(
    integration_context: Context, clean_grants: None
) -> None:
    """PINS the DECIDED disable arm of #1233: ``disable`` does NOT sweep.

    Decided: dormant + honest listings. Disable is a reversible kill-switch —
    the enforcement layer already fail-closes everything while disabled (no
    token resolves and refresh refuses for a non-active agent, the #1136
    gates), so sweeping would make disable semi-terminal (re-enable would
    force every connected client through a fresh consent round). The grant
    rows stay ``active`` (dormant); the listing/count surfaces tell the truth
    about them instead (see ``test_disabled_agent_grants_excluded_from_client_counts``
    and ``test_grant_listings_annotate_agent_status``). If this policy is
    ever reversed, this test is the one to flip.
    """
    ctx = integration_context
    owner = await seeds.seed_user(ctx, "usr_dis_owner")
    admin_id = await seeds.seed_user(ctx, "usr_dis_admin")
    agent_id = await seeds.seed_agent(ctx, owner_id=owner, scopes=["apis:read"])
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"])
    grant_id, access, refresh, _ = await seeds.mint_grant_channel_tokens(
        ctx, user_id=owner, agent_id=agent_id, grant_scopes=["apis:read"]
    )

    await AgentService(ctx).disable(agent_id, identity=_admin_identity(admin_id))

    # The grant row is untouched (the open question) …
    grant = await OAuthGrantService(ctx).get_grant(grant_id)
    assert grant is not None and grant.status == "active"
    assert await _revoked_events(ctx, agent_id) == []

    # … but nothing is live: the status gates fail the tokens closed anyway.
    token_svc = TokenService(ctx)
    resolved = await token_svc.resolve_access_token(access)
    assert resolved is None or resolved.active is False
    with pytest.raises(InvalidGrantError, match="not active"):
        await token_svc.refresh(refresh, client_id=seeds.CLIENT_ID)


async def test_reenable_restores_standing_consent(
    integration_context: Context, clean_grants: None
) -> None:
    """The dormancy round-trip that makes the no-sweep decision meaningful:
    disable → refresh refuses; enable → the SAME grant refreshes to fresh
    working tokens, with no new consent round. If disable ever started
    sweeping, this test would fail with the grant revoked."""
    ctx = integration_context
    owner = await seeds.seed_user(ctx, "usr_ren_owner")
    admin_id = await seeds.seed_user(ctx, "usr_ren_admin")
    agent_id = await seeds.seed_agent(ctx, owner_id=owner, scopes=["apis:read"])
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"])
    grant_id, _access, refresh, _ = await seeds.mint_grant_channel_tokens(
        ctx, user_id=owner, agent_id=agent_id, grant_scopes=["apis:read"]
    )
    svc = AgentService(ctx)
    identity = _admin_identity(admin_id)

    await svc.disable(agent_id, identity=identity)
    with pytest.raises(InvalidGrantError, match="not active"):
        await TokenService(ctx).refresh(refresh, client_id=seeds.CLIENT_ID)

    await svc.enable(agent_id, identity=identity)

    grant = await OAuthGrantService(ctx).get_grant(grant_id)
    assert grant is not None and grant.status == "active"
    new_access, _new_refresh, _scopes = await TokenService(ctx).refresh(
        refresh, client_id=seeds.CLIENT_ID
    )
    resolved = await TokenService(ctx).resolve_access_token(new_access)
    assert resolved is not None and resolved.active is True


async def test_disabled_agent_grants_excluded_from_client_counts(
    integration_context: Context, clean_grants: None
) -> None:
    """#1233 honesty, count side: a dormant grant (active row, disabled
    agent) does not count as a working connection on the client surfaces —
    and comes back when the agent is re-enabled."""
    ctx = integration_context
    owner = await seeds.seed_user(ctx, "usr_cnt_owner")
    admin_id = await seeds.seed_user(ctx, "usr_cnt_admin")
    agent_id = await seeds.seed_agent(ctx, owner_id=owner, scopes=["apis:read"])
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"])
    await seeds.mint_grant_channel_tokens(
        ctx, user_id=owner, agent_id=agent_id, grant_scopes=["apis:read"]
    )
    client_svc = OAuthClientService(ctx)
    client_view = await client_svc.get_by_client_id(seeds.CLIENT_ID)
    assert client_view is not None

    async def _counts() -> tuple[int, int]:
        per_client = (await client_svc.get(client_view.id)).active_grant_count
        listed = {c.client_id: c.active_grant_count for c in await client_svc.list_all()}
        return per_client or 0, listed.get(seeds.CLIENT_ID) or 0

    assert await _counts() == (1, 1)

    identity = _admin_identity(admin_id)
    await AgentService(ctx).disable(agent_id, identity=identity)
    assert await _counts() == (0, 0)

    await AgentService(ctx).enable(agent_id, identity=identity)
    assert await _counts() == (1, 1)


async def test_grant_listings_annotate_agent_status(
    integration_context: Context, clean_grants: None
) -> None:
    """#1233 honesty, list side: the per-agent "Connected clients" listing
    and the admin cross-view stamp each row with the bound agent's lifecycle
    state, so a dormant grant is never mistaken for a working connection."""
    ctx = integration_context
    owner = await seeds.seed_user(ctx, "usr_ann_owner")
    admin_id = await seeds.seed_user(ctx, "usr_ann_admin")
    agent_id = await seeds.seed_agent(ctx, owner_id=owner, scopes=["apis:read"])
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"])
    grant_id, _a, _r, _ = await seeds.mint_grant_channel_tokens(
        ctx, user_id=owner, agent_id=agent_id, grant_scopes=["apis:read"]
    )
    identity = _admin_identity(admin_id)

    async def _statuses() -> tuple[str | None, str | None]:
        per_agent = await OAuthGrantService(ctx).list_grants_for_agent(agent_id, identity=identity)
        cross_view = await OAuthGrantAdminService(ctx).list_grants(
            identity=identity, agent_id=agent_id
        )
        by_id_a = {v.id: v.agent_status for v in per_agent.data}
        by_id_x = {v.id: v.agent_status for v in cross_view.data}
        return by_id_a[grant_id], by_id_x[grant_id]

    assert await _statuses() == ("active", "active")

    await AgentService(ctx).disable(agent_id, identity=identity)
    # The row is still an ACTIVE grant — dormant, not revoked — but both
    # listing surfaces now carry the disabled agent status.
    assert await _statuses() == ("disabled", "disabled")

    await AgentService(ctx).enable(agent_id, identity=identity)
    assert await _statuses() == ("active", "active")


async def test_archive_rolls_back_when_grant_sweep_fails(
    integration_context: Context, clean_grants: None
) -> None:
    """Same-transaction pin (mirrors the G10 rollback test): if the sweep
    fails mid-archive, the WHOLE archive rolls back — the agent stays active
    and the grant lives on. Fails if the sweep ever moves outside the archive
    transaction."""
    ctx = integration_context
    owner = await seeds.seed_user(ctx, "usr_arb_owner")
    admin_id = await seeds.seed_user(ctx, "usr_arb_admin")
    agent_id = await seeds.seed_agent(ctx, owner_id=owner, scopes=["apis:read"])
    await seeds.seed_client(ctx, allowed_scopes=["apis:read"])
    grant_id, access, _refresh, _ = await seeds.mint_grant_channel_tokens(
        ctx, user_id=owner, agent_id=agent_id, grant_scopes=["apis:read"]
    )

    with (
        patch(
            "jentic_one.shared.oauth_grant_revocation.AccessTokenRepository.revoke_by_grant",
            new=AsyncMock(side_effect=RuntimeError("sweep exploded")),
        ),
        pytest.raises(RuntimeError, match="sweep exploded"),
    ):
        await AgentService(ctx).archive(agent_id, identity=_admin_identity(admin_id))

    async with ctx.admin_db.session() as session:
        agent = await AgentRepository.get_by_id(session, agent_id)
    assert agent is not None and agent.status == "active"
    grant = await OAuthGrantService(ctx).get_grant(grant_id)
    assert grant is not None and grant.status == "active"
    assert await TokenService(ctx).resolve_access_token(access) is not None
