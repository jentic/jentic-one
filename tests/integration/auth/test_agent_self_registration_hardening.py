"""Integration tests for agent self-registration scope filtering and rate limiting."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.repos import ActorPermissionGrantRepository, AgentRepository
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.registration_service import RegistrationService
from jentic_one.auth.web.app import create_app
from jentic_one.shared.audit import AuditAction, AuditTargetType
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    AGENTS_WRITE,
    CAPABILITIES_EXECUTE,
    DEFAULT_AGENT_PERMISSIONS,
    ORG_ADMIN,
)
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.events import EventType
from tests.integration.auth import seeds

pytestmark = pytest.mark.integration

_ADMIN_ID = "usr_self_reg_admin"
_DCR_JWKS: dict[str, Any] = {
    "keys": [
        {
            "kty": "OKP",
            "crv": "Ed25519",
            "x": "dGVzdC1wdWJsaWMta2V5LWJhc2U2NA",
            "kid": "k1",
        }
    ]
}


def _admin_identity() -> Identity:
    return Identity(
        sub=_ADMIN_ID,
        email=f"{_ADMIN_ID}@grants.test",
        permissions=[ORG_ADMIN],
    )


@pytest.fixture()
async def seeded_admin(
    integration_context: Context, clean_grants: None
) -> AsyncGenerator[Identity, None]:
    """Seed an admin user and clean up any agents/audits/events created during the test."""
    await seeds.seed_user(integration_context, _ADMIN_ID)
    await seeds.seed_permissions(integration_context, _ADMIN_ID, [ORG_ADMIN])
    yield _admin_identity()
    async with integration_context.admin_db.session() as session:
        agent_rows = await session.execute(
            select(Agent.id).where((Agent.owner_id == _ADMIN_ID) | (Agent.registered_by == "self"))
        )
        agent_ids = [row[0] for row in agent_rows.all()]
        if agent_ids:
            await session.execute(delete(AuditEntry).where(AuditEntry.target_id.in_(agent_ids)))
            await session.execute(delete(Event).where(Event.actor_id.in_(agent_ids)))
            await session.execute(delete(Agent).where(Agent.id.in_(agent_ids)))
        await session.execute(
            delete(Event).where(
                Event.type.in_(
                    [
                        EventType.AGENT_SELF_REGISTERED,
                        EventType.AGENT_REGISTRATION_APPROVED,
                    ]
                )
            )
        )
        await session.commit()


async def test_register_drops_admin_only_and_unknown_scopes(
    integration_context: Context, seeded_admin: Identity
) -> None:
    """Self-registration keeps non-admin catalogue permissions and drops admin-only,
    retired, and unknown scope strings."""
    _ = seeded_admin
    svc = RegistrationService(integration_context)
    result = await svc.register(
        "self-reg-filtered",
        _DCR_JWKS,
        scope="org:admin agents:write capabilities:execute toolkits:read broker:execute not:a:perm",
    )

    async with integration_context.admin_db.session() as session:
        grants = await ActorPermissionGrantRepository.list_for_actor(
            session, result.client_id, actor_type=ActorType.AGENT
        )
        audit = (
            await session.execute(
                select(AuditEntry).where(
                    AuditEntry.target_type == AuditTargetType.AGENT.value,
                    AuditEntry.target_id == result.client_id,
                    AuditEntry.action == AuditAction.REGISTER.value,
                )
            )
        ).scalar_one()

    assert [g.permission for g in grants] == [CAPABILITIES_EXECUTE]
    assert grants[0].granted_by is None
    assert grants[0].created_by == "dcr"
    assert audit.after is not None
    assert audit.after["permissions"] == [CAPABILITIES_EXECUTE]


async def test_approve_drops_pre_staged_admin_only_grants(
    integration_context: Context, seeded_admin: Identity
) -> None:
    """Approving a pending agent revokes any pre-staged granted_by=None admin-only
    permissions and records a REVOKE audit entry, while still allowing explicit
    replace_permissions by an org:admin afterwards."""
    async with integration_context.admin_db.transaction() as session:
        agent = await AgentRepository.create_dcr(
            session,
            name="legacy-staged-agent",
            jwks=_DCR_JWKS,
            rat_hash="a" * 64,
            rat_expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        agent_id = agent.id
        for perm in (ORG_ADMIN, AGENTS_WRITE, CAPABILITIES_EXECUTE):
            await ActorPermissionGrantRepository.grant(
                session,
                actor_id=agent_id,
                actor_type=ActorType.AGENT,
                permission=perm,
                granted_by=None,
                created_by="dcr",
            )

    agent_svc = AgentService(integration_context)
    view = await agent_svc.approve(agent_id, identity=seeded_admin)
    assert view.status == "active"
    assert view.owner_id == _ADMIN_ID

    held = await agent_svc.get_permissions(agent_id, identity=seeded_admin)
    assert held == [CAPABILITIES_EXECUTE]

    async with integration_context.admin_db.session() as session:
        revoke_audits = list(
            (
                await session.execute(
                    select(AuditEntry).where(
                        AuditEntry.target_type == AuditTargetType.AGENT.value,
                        AuditEntry.target_id == agent_id,
                        AuditEntry.action == AuditAction.REVOKE.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        approve_audit = (
            await session.execute(
                select(AuditEntry).where(
                    AuditEntry.target_type == AuditTargetType.AGENT.value,
                    AuditEntry.target_id == agent_id,
                    AuditEntry.action == AuditAction.APPROVE.value,
                )
            )
        ).scalar_one()

    assert len(revoke_audits) == 1
    assert revoke_audits[0].reason == "self_requested_admin_permission_dropped"
    assert revoke_audits[0].before == {
        "permissions": sorted([ORG_ADMIN, AGENTS_WRITE, CAPABILITIES_EXECUTE])
    }
    assert revoke_audits[0].after == {"permissions": [CAPABILITIES_EXECUTE]}
    assert approve_audit.after is not None
    assert approve_audit.after["permissions"] == [CAPABILITIES_EXECUTE]

    # Explicit replace_permissions by org:admin can still grant admin-only permissions.
    updated = await agent_svc.replace_permissions(
        agent_id, [CAPABILITIES_EXECUTE, AGENTS_WRITE, ORG_ADMIN], identity=seeded_admin
    )
    assert set(updated) == {CAPABILITIES_EXECUTE, AGENTS_WRITE, ORG_ADMIN}

    # If a legacy pending agent had ONLY admin-only self-staged grants, dropping them
    # leaves no grants and approve falls back to DEFAULT_AGENT_PERMISSIONS.
    async with integration_context.admin_db.transaction() as session:
        only_admin_agent = await AgentRepository.create_dcr(
            session,
            name="legacy-only-admin-agent",
            jwks=_DCR_JWKS,
            rat_hash="b" * 64,
            rat_expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        only_admin_id = only_admin_agent.id
        await ActorPermissionGrantRepository.grant(
            session,
            actor_id=only_admin_id,
            actor_type=ActorType.AGENT,
            permission=ORG_ADMIN,
            granted_by=None,
            created_by="dcr",
        )

    await agent_svc.approve(only_admin_id, identity=seeded_admin)
    fallback_held = await agent_svc.get_permissions(only_admin_id, identity=seeded_admin)
    assert set(fallback_held) == set(DEFAULT_AGENT_PERMISSIONS)


async def test_register_rate_limited_per_network(
    integration_context: Context, seeded_admin: Identity
) -> None:
    """POST /register enforces a per-client-network rate limit in its own bucket,
    returning 429 with Retry-After after burst requests from one IP while admitting
    another client network."""
    _ = seeded_admin
    rl_cfg = integration_context.config.auth.oauth_rate_limit
    prior_rpm = rl_cfg.registration_rpm
    prior_burst = rl_cfg.registration_burst
    rl_cfg.registration_rpm = 10
    rl_cfg.registration_burst = 2
    try:
        app = create_app(integration_context)
        payload = {"client_name": "rate-limited-agent", "jwks": _DCR_JWKS}

        async with AsyncClient(
            transport=ASGITransport(app=app, client=("198.51.100.40", 50000)),
            base_url="https://testserver",
        ) as client_one:
            first = await client_one.post("/register", json=payload)
            second = await client_one.post("/register", json=payload)
            third = await client_one.post("/register", json=payload)

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert third.status_code == 429, third.text
        assert third.json()["type"] == "rate_limit_exceeded"
        assert int(third.headers["Retry-After"]) >= 1

        async with AsyncClient(
            transport=ASGITransport(app=app, client=("198.51.100.41", 50000)),
            base_url="https://testserver",
        ) as client_two:
            other_net = await client_two.post("/register", json=payload)

        assert other_net.status_code == 201, other_net.text
    finally:
        rl_cfg.registration_rpm = prior_rpm
        rl_cfg.registration_burst = prior_burst
