"""Service-level integration tests for the consent workflows on AuthorizeService.

``create_agent_from_consent`` (the inline create-agent submit) and
``approve_agent_consent`` (the agent-picker approve) run here directly against
the real services and the integration database — no HTTP, no service or
repository mocking. The web suites cover the request plumbing around them.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.auth.services.authorize_service import (
    AuthorizeService,
    ConsentAgentCreateResult,
)
from jentic_one.shared.auth.permission_catalog import AGENTS_WRITE
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus
from jentic_one.shared.models.actors import Origin
from tests.integration.auth.seeds import (
    CLIENT_ID,
    seed_agent,
    seed_client,
    seed_permissions,
    seed_user,
)

pytestmark = pytest.mark.integration

_OWNER = "usr_svc_consent_owner"
_OTHER = "usr_svc_consent_other"


async def _agents_of(ctx: Context, owner_id: str) -> list[Agent]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(select(Agent).where(Agent.owner_id == owner_id))
        return list(result.scalars().all())


class _Slot:
    """The per-subject create-slot claim, recording how often it was asked."""

    def __init__(self, *, wins: bool = True) -> None:
        self.wins = wins
        self.calls = 0

    async def __call__(self) -> bool:
        self.calls += 1
        return self.wins


async def _create(
    ctx: Context, slot: _Slot, *, local_user_id: str | None = _OWNER
) -> ConsentAgentCreateResult:
    return await AuthorizeService(ctx).create_agent_from_consent(
        claims=None,
        local_user_id=local_user_id,
        user_email=f"{_OWNER}@grants.test",
        origin=Origin.DASHBOARD,
        agent_name="consent-created",
        client_id=CLIENT_ID,
        claim_create_slot=slot,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("permissions", "expected_status", "expected_outcome"),
    [
        ([AGENTS_WRITE], ActorStatus.ACTIVE, "reenter_consent"),
        ([], ActorStatus.PENDING, "await_approval"),
    ],
)
async def test_create_agent_from_consent_arm_follows_agents_write(
    integration_context: Context,
    clean_grants: None,
    permissions: list[str],
    expected_status: ActorStatus,
    expected_outcome: str,
) -> None:
    """A first-run user gets exactly one agent, owned by them; ``agents:write``
    decides ACTIVE (re-enter consent) vs PENDING (park on the awaiting page)."""
    ctx = integration_context
    await seed_user(ctx, _OWNER)
    await seed_permissions(ctx, _OWNER, permissions)
    slot = _Slot()

    result = await _create(ctx, slot)

    agents = await _agents_of(ctx, _OWNER)
    assert len(agents) == 1
    assert agents[0].name == "consent-created"
    assert agents[0].status == expected_status.value
    assert result.outcome == expected_outcome
    assert result.error is None
    if expected_outcome == "await_approval":
        assert (result.agent_id, result.agent_name) == (agents[0].id, "consent-created")
    else:
        assert result.agent_id is None
    assert slot.calls == 1


@pytest.mark.asyncio
async def test_create_agent_from_consent_skips_when_user_owns_any_agent(
    integration_context: Context, clean_grants: None
) -> None:
    """An owner whose only agent is DISABLED is not first-run: nothing is
    created and the slot is never claimed."""
    ctx = integration_context
    await seed_user(ctx, _OWNER)
    await seed_permissions(ctx, _OWNER, [AGENTS_WRITE])
    existing = await seed_agent(ctx, owner_id=_OWNER, scopes=[], status=ActorStatus.DISABLED)
    slot = _Slot()

    result = await _create(ctx, slot)

    assert result == ConsentAgentCreateResult(outcome="reenter_consent")
    assert [a.id for a in await _agents_of(ctx, _OWNER)] == [existing]
    assert slot.calls == 0


@pytest.mark.asyncio
async def test_create_agent_from_consent_lost_slot_creates_nothing(
    integration_context: Context, clean_grants: None
) -> None:
    ctx = integration_context
    await seed_user(ctx, _OWNER)
    await seed_permissions(ctx, _OWNER, [AGENTS_WRITE])
    slot = _Slot(wins=False)

    result = await _create(ctx, slot)

    assert result == ConsentAgentCreateResult(outcome="reenter_consent")
    assert await _agents_of(ctx, _OWNER) == []
    assert slot.calls == 1


@pytest.mark.asyncio
async def test_create_agent_from_consent_without_subject_fails_closed(
    integration_context: Context, clean_grants: None
) -> None:
    """No local user and no IdP claims: the invalid_consent error, nothing created."""
    slot = _Slot()

    result = await _create(integration_context, slot, local_user_id=None)

    assert result == ConsentAgentCreateResult(outcome="error", error="invalid_consent")
    assert slot.calls == 0


async def _grants_for(ctx: Context, agent_id: str) -> list[OAuthClientGrant]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(OAuthClientGrant).where(OAuthClientGrant.agent_id == agent_id)
        )
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_approve_agent_consent_mints_grant_for_scope_intersection(
    integration_context: Context, clean_grants: None
) -> None:
    """The grant carries requested ∩ allowlist ∩ the agent's live permissions,
    OIDC scopes stripped, in request order."""
    ctx = integration_context
    await seed_user(ctx, _OWNER)
    agent_id = await seed_agent(ctx, owner_id=_OWNER, scopes=["apis:read", "apis:write"])
    await seed_client(ctx, allowed_scopes=["apis:read", "apis:write"])

    approval = await AuthorizeService(ctx).approve_agent_consent(
        user_id=_OWNER,
        client_id=CLIENT_ID,
        client_name="Grant Channel App",
        allowed_scopes=["apis:read", "apis:write"],
        agent_id=agent_id,
        scope="openid apis:write credentials:read apis:read",
    )

    assert approval.error is None
    assert approval.scopes == ("apis:write", "apis:read")
    grants = await _grants_for(ctx, agent_id)
    assert [g.id for g in grants] == [approval.grant_id]
    assert grants[0].user_id == _OWNER
    assert list(grants[0].scopes) == ["apis:write", "apis:read"]


@pytest.mark.asyncio
async def test_approve_agent_consent_rejects_ineligible_selections(
    integration_context: Context, clean_grants: None
) -> None:
    """Missing, foreign, and pending agents are rejected, as is a selection
    with no grantable scope — and none of them mints a grant."""
    ctx = integration_context
    await seed_user(ctx, _OWNER)
    await seed_user(ctx, _OTHER)
    foreign = await seed_agent(ctx, owner_id=_OTHER, scopes=["apis:read"])
    pending = await seed_agent(
        ctx, owner_id=_OWNER, scopes=["apis:read"], status=ActorStatus.PENDING
    )
    scopeless = await seed_agent(ctx, owner_id=_OWNER, scopes=["credentials:read"])
    await seed_client(ctx, allowed_scopes=["apis:read"])
    svc = AuthorizeService(ctx)

    cases = [
        (None, "invalid_agent_selection"),
        (foreign, "invalid_agent_selection"),
        (pending, "invalid_agent_selection"),
        (scopeless, "no_grantable_scopes"),
    ]
    for agent_id, expected_error in cases:
        approval = await svc.approve_agent_consent(
            user_id=_OWNER,
            client_id=CLIENT_ID,
            client_name="Grant Channel App",
            allowed_scopes=["apis:read"],
            agent_id=agent_id,
            scope="openid apis:read",
        )
        assert approval.error == expected_error, agent_id
        assert approval.grant_id is None
    for agent_id in (foreign, pending, scopeless):
        assert await _grants_for(ctx, agent_id) == []
