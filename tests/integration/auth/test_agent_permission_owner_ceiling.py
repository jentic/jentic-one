"""Integration tests for live owner-permission capping and transfer re-capping on agents."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.repos import (
    ActorPermissionGrantRepository,
    AgentRepository,
)
from jentic_one.auth.services.agent_auth_service import AgentAuthService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.services.token_service import TokenService
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import agents
from jentic_one.broker.repos.token_resolver import InProcessTokenResolver
from jentic_one.shared.auth.api_key_resolver import ApiKeyResolver
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType
from jentic_one.shared.web.deps import resolve_identity
from tests.integration.auth import seeds

pytestmark = pytest.mark.integration


def _admin_identity(admin_id: str) -> Identity:
    return Identity(
        sub=admin_id,
        email="admin@grants.test",
        permissions=["agents:write", "agents:read", "org:admin"],
    )


async def test_owner_demotion_narrows_agent_api_key(
    integration_context: Context, clean_grants: None
) -> None:
    """Narrowing an agent owner's permissions removes permissions above the owner's
    live set when resolving the agent's API key, while keeping default agent permissions."""
    ctx = integration_context
    owner_id = await seeds.seed_user(ctx, "usr_ceil_key_owner")
    await seeds.seed_permissions(ctx, owner_id, ["config:write", "agents:write"])
    agent_id = await seeds.seed_agent(
        ctx,
        owner_id=owner_id,
        scopes=["capabilities:execute", "config:write", "config:read"],
    )
    owner_identity = Identity(
        sub=owner_id,
        email=f"{owner_id}@grants.test",
        permissions=["agents:write", "agents:read", "config:write", "config:read"],
    )
    api_key = await AgentAuthService(ctx).register_api_key(agent_id, identity=owner_identity)
    resolver = ApiKeyResolver(ctx.admin_db)

    before = await resolver.resolve(api_key)
    assert before is not None and before.active is True
    assert set(before.permissions) == {"capabilities:execute", "config:write", "config:read"}

    # Narrow the owner: drop config:write (retaining only config:read).
    await seeds.seed_permissions(ctx, owner_id, ["config:read"])

    after_narrow = await resolver.resolve(api_key)
    assert after_narrow is not None and after_narrow.active is True
    assert "config:write" not in after_narrow.permissions
    assert set(after_narrow.permissions) == {"capabilities:execute", "config:read"}

    # Strip all non-default permissions from the owner: only default permissions remain.
    await seeds.seed_permissions(ctx, owner_id, [])

    after_strip = await resolver.resolve(api_key)
    assert after_strip is not None and after_strip.active is True
    assert after_strip.permissions == ["capabilities:execute"]


async def test_owner_demotion_narrows_agent_access_token(
    integration_context: Context, clean_grants: None
) -> None:
    """Narrowing an agent owner's permissions narrows the agent's opaque access token
    on both the auth-surface TokenService and the broker InProcessTokenResolver."""
    ctx = integration_context
    owner_id = await seeds.seed_user(ctx, "usr_ceil_tok_owner")
    await seeds.seed_permissions(ctx, owner_id, ["config:write", "users:write"])
    agent_id = await seeds.seed_agent(
        ctx,
        owner_id=owner_id,
        scopes=["capabilities:execute", "config:write", "users:read"],
    )
    token_svc = TokenService(ctx)
    broker_resolver = InProcessTokenResolver(ctx.admin_db)
    access_token, refresh_token = await token_svc.issue_pair(
        agent_id,
        ActorType.AGENT,
        ["capabilities:execute", "config:write", "users:read"],
    )

    for resolver in (token_svc, broker_resolver):
        resolved = await resolver.resolve_access_token(access_token)
        assert resolved is not None and resolved.active is True
        assert set(resolved.permissions) == {"capabilities:execute", "config:write", "users:read"}

    # Narrow the owner: drop config:write, keep users:write (which implies users:read).
    await seeds.seed_permissions(ctx, owner_id, ["users:write"])

    for resolver in (token_svc, broker_resolver):
        resolved = await resolver.resolve_access_token(access_token)
        assert resolved is not None and resolved.active is True
        assert "config:write" not in resolved.permissions
        assert set(resolved.permissions) == {"capabilities:execute", "users:read"}

    introspected = await token_svc.introspect(access_token)
    assert introspected["active"] is True
    assert set(str(introspected["scope"]).split()) == {"capabilities:execute", "users:read"}

    new_access, _new_refresh, reported_scopes = await token_svc.refresh(refresh_token)
    assert set(reported_scopes) == {"capabilities:execute", "users:read"}
    for resolver in (token_svc, broker_resolver):
        resolved = await resolver.resolve_access_token(new_access)
        assert resolved is not None and resolved.active is True
        assert set(resolved.permissions) == {"capabilities:execute", "users:read"}


async def test_transfer_revokes_permissions_above_new_owner(
    integration_context: Context, clean_grants: None
) -> None:
    """Transferring an agent via PATCH /agents/{id} revokes stored permission grants
    that exceed the new owner's ceiling and writes a REVOKE audit entry."""
    ctx = integration_context
    alice_id = await seeds.seed_user(ctx, "usr_ceil_alice")
    bob_id = await seeds.seed_user(ctx, "usr_ceil_bob")
    admin_id = await seeds.seed_user(ctx, "usr_ceil_admin")
    await seeds.seed_permissions(ctx, alice_id, ["users:write", "config:write", "agents:write"])
    await seeds.seed_permissions(ctx, bob_id, ["config:read"])

    agent_id = await seeds.seed_agent(
        ctx,
        owner_id=alice_id,
        scopes=["capabilities:execute", "users:write", "config:write", "config:read"],
    )
    admin = _admin_identity(admin_id)
    api_key = await AgentAuthService(ctx).register_api_key(agent_id, identity=admin)
    access_token, _refresh = await TokenService(ctx).issue_pair(
        agent_id,
        ActorType.AGENT,
        ["capabilities:execute", "users:write", "config:write", "config:read"],
    )

    app = FastAPI()
    app.state.ctx = ctx
    app.include_router(agents.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.dependency_overrides[resolve_identity] = lambda: admin

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(f"/agents/{agent_id}", json={"owner_id": bob_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner_id"] == bob_id

    async with ctx.admin_db.session() as session:
        grants = await ActorPermissionGrantRepository.list_for_actor(
            session, agent_id, actor_type=ActorType.AGENT
        )
        stored_permissions = [g.permission for g in grants]
        audits = list(
            (
                await session.execute(
                    select(AuditEntry).where(
                        AuditEntry.target_type == "agent",
                        AuditEntry.target_id == agent_id,
                        AuditEntry.action == "revoke",
                        AuditEntry.reason
                        == "agent ownership transferred: permissions above new owner",
                    )
                )
            )
            .scalars()
            .all()
        )

    assert set(stored_permissions) == {"capabilities:execute", "config:read"}
    assert "users:write" not in stored_permissions
    assert "config:write" not in stored_permissions

    assert len(audits) == 1
    audit = audits[0]
    assert audit.actor_id == admin_id
    assert audit.before is not None
    assert set(audit.before["permissions"]) == {
        "capabilities:execute",
        "users:write",
        "config:write",
        "config:read",
    }
    assert audit.after is not None
    assert set(audit.after["permissions"]) == {"capabilities:execute", "config:read"}

    key_resolved = await ApiKeyResolver(ctx.admin_db).resolve(api_key)
    assert key_resolved is not None
    assert set(key_resolved.permissions) == {"capabilities:execute", "config:read"}

    for resolver in (TokenService(ctx), InProcessTokenResolver(ctx.admin_db)):
        tok_resolved = await resolver.resolve_access_token(access_token)
        assert tok_resolved is not None
        assert set(tok_resolved.permissions) == {"capabilities:execute", "config:read"}


async def test_admin_owner_agent_keeps_admin_granted_permissions(
    integration_context: Context, clean_grants: None
) -> None:
    """An agent owned by an org:admin user (or ownerless) keeps all admin-granted
    permissions across all resolvers and on transfer to another org:admin user."""
    ctx = integration_context
    admin_owner = await seeds.seed_user(ctx, "usr_ceil_adm_owner1")
    admin_owner_2 = await seeds.seed_user(ctx, "usr_ceil_adm_owner2")
    await seeds.seed_permissions(ctx, admin_owner, ["org:admin"])
    await seeds.seed_permissions(ctx, admin_owner_2, ["org:admin"])

    granted = ["capabilities:execute", "org:admin", "agents:write", "config:write"]
    agent_id = await seeds.seed_agent(ctx, owner_id=admin_owner, scopes=granted)
    admin = _admin_identity(admin_owner)
    api_key = await AgentAuthService(ctx).register_api_key(agent_id, identity=admin)
    access_token, _ = await TokenService(ctx).issue_pair(agent_id, ActorType.AGENT, granted)

    key_resolved = await ApiKeyResolver(ctx.admin_db).resolve(api_key)
    assert key_resolved is not None
    assert set(key_resolved.permissions) == set(granted)

    for resolver in (TokenService(ctx), InProcessTokenResolver(ctx.admin_db)):
        tok_resolved = await resolver.resolve_access_token(access_token)
        assert tok_resolved is not None
        assert set(tok_resolved.permissions) == set(granted)

    # Transferring to another org:admin user keeps all grants.
    await AgentService(ctx).update_agent(
        agent_id, update_data={"owner_id": admin_owner_2}, identity=admin
    )
    async with ctx.admin_db.session() as session:
        stored = [
            g.permission
            for g in await ActorPermissionGrantRepository.list_for_actor(
                session, agent_id, actor_type=ActorType.AGENT
            )
        ]
    assert set(stored) == set(granted)

    # An ownerless agent also keeps admin-granted permissions unchanged.
    async with ctx.admin_db.session() as session:
        ownerless = await AgentRepository.create_dcr(
            session,
            name="ownerless-ceil-agent",
            jwks={"keys": []},
            rat_hash="h",
            rat_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        ownerless.status = ActorStatus.ACTIVE
        ownerless.created_by = seeds.SEED_MARKER
        for perm in granted:
            await ActorPermissionGrantRepository.grant(
                session,
                actor_id=ownerless.id,
                actor_type=ActorType.AGENT,
                permission=perm,
                granted_by=admin_owner,
                created_by=seeds.SEED_MARKER,
            )
        await session.commit()
        ownerless_id = ownerless.id

    ownerless_at, _ = await TokenService(ctx).issue_pair(ownerless_id, ActorType.AGENT, granted)
    for resolver in (TokenService(ctx), InProcessTokenResolver(ctx.admin_db)):
        tok_resolved = await resolver.resolve_access_token(ownerless_at)
        assert tok_resolved is not None and tok_resolved.active is True
        assert set(tok_resolved.permissions) == set(granted)
