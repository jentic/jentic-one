"""Tests for GET /me identity endpoint."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from jentic_one.admin.core.schema.agent_toolkit_bindings import AgentToolkitBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.invite_tokens import InviteToken
from jentic_one.admin.core.schema.service_accounts import ServiceAccount
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    ActorScopeGrantRepository,
    AgentRepository,
    AgentToolkitBindingRepository,
    ServiceAccountRepository,
    UserPermissionGrantRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services._support.passwords import hash_password
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.auth.services.token_service import TokenService
from jentic_one.auth.web.app import create_app
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType, InviteState
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration

ADMIN_EMAIL = "me-test-admin@test.local"
OWNER_EMAIL = "me-test-owner@test.local"

# A real toolkit (control DB) the agent is bound to — /me must resolve its name
# (issue #686). ``tk_me_orphan`` is a binding with no toolkit row, exercising the
# graceful name=None path.
NAMED_TOOLKIT_ID = "tk_me_named"
NAMED_TOOLKIT_NAME = "Design news radar"
ORPHAN_TOOLKIT_ID = "tk_me_orphan"


def _build_app(ctx: Context) -> FastAPI:
    """Build the auth app using the real factory, with lifespan disabled."""
    app = create_app(ctx)
    app.router.lifespan_context = noop_lifespan
    return app


def _make_token(
    ctx: Context,
    sub: str,
    email: str,
    permissions: list[str],
    *,
    must_change_password: bool = False,
    actor_type: str = "user",
    parent_actor_id: str | None = None,
) -> str:
    config = ctx.config.admin.auth
    claims = {
        "sub": sub,
        "email": email,
        "scopes": permissions,
        "actor_type": actor_type,
        "must_change_password": must_change_password,
    }
    if parent_actor_id:
        claims["parent_actor_id"] = parent_actor_id
    # NOTE: The test _make_token was embedding permissions, we now rely on DB lookup
    return issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)


@pytest.fixture()
async def admin_user_id(web_context: Context) -> AsyncGenerator[str, None]:
    ctx = web_context
    async with ctx.admin_db.transaction() as session:
        user = await UserRepository.create(
            session,
            email=ADMIN_EMAIL,
            first_name="Me",
            last_name="Admin",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(
            session,
            user_id=user.id,
            password_hash=hash_password("test-password-123"),
            created_by="usr_test",
        )
        await UserPermissionGrantRepository.set_permissions(
            session, user.id, permissions={"org:admin"}, granted_by=None, created_by="usr_test"
        )
    yield user.id

    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user.id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user.id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user.id))
        await session.execute(delete(Agent).where(Agent.owner_id == user.id))
        await session.execute(delete(ServiceAccount).where(ServiceAccount.owner_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


@pytest.fixture()
async def owner_user_id(web_context: Context) -> AsyncGenerator[str, None]:
    ctx = web_context
    async with ctx.admin_db.transaction() as session:
        user = await UserRepository.create(
            session,
            email=OWNER_EMAIL,
            first_name="Owner",
            last_name="User",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(
            session,
            user_id=user.id,
            password_hash=hash_password("test-password-123"),
            created_by="usr_test",
        )
        await UserPermissionGrantRepository.set_permissions(
            session,
            user.id,
            permissions={
                "agents:read",
                "agents:write",
                "service-accounts:read",
                "service-accounts:write",
            },
            granted_by=None,
            created_by="usr_test",
        )
    yield user.id

    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user.id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user.id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user.id))
        await session.execute(delete(Agent).where(Agent.owner_id == user.id))
        await session.execute(delete(ServiceAccount).where(ServiceAccount.owner_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


@pytest.fixture()
async def approved_agent_id(
    web_context: Context, owner_user_id: str, admin_user_id: str
) -> AsyncGenerator[str, None]:
    ctx = web_context
    async with ctx.admin_db.transaction() as session:
        agent = await AgentRepository.create(
            session,
            name="me-test-agent",
            owner_id=owner_user_id,
            registered_by=owner_user_id,
            description="Agent for /me tests",
            created_by="usr_test",
        )
        await AgentRepository.set_approval(session, agent.id, approved_by=admin_user_id)
        await AgentToolkitBindingRepository.bind(
            session, agent_id=agent.id, toolkit_id=NAMED_TOOLKIT_ID, created_by="usr_test"
        )
        await AgentToolkitBindingRepository.bind(
            session, agent_id=agent.id, toolkit_id=ORPHAN_TOOLKIT_ID, created_by="usr_test"
        )
        # A live scope grant the presented token won't carry — exercises #673:
        # /me must reflect current grants, not just the token's baked-in scopes.
        await ActorScopeGrantRepository.grant(
            session,
            actor_id=agent.id,
            actor_type="agent",
            scope="capabilities:read",
            granted_by=admin_user_id,
            created_by="usr_test",
        )
    # The toolkit name lives in the control DB; seed a real row so /me can resolve
    # NAMED_TOOLKIT_ID → its name (issue #686). ORPHAN_TOOLKIT_ID has no row.
    async with ctx.control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO toolkits (id, name, created_by) "
                "VALUES (:id, :name, :created_by) ON CONFLICT DO NOTHING"
            ),
            {"id": NAMED_TOOLKIT_ID, "name": NAMED_TOOLKIT_NAME, "created_by": owner_user_id},
        )
        await session.commit()
    yield agent.id

    async with ctx.admin_db.session() as session:
        await session.execute(
            delete(AgentToolkitBinding).where(AgentToolkitBinding.agent_id == agent.id)
        )
        await ActorScopeGrantRepository.revoke_all(session, agent.id)
        await session.execute(delete(Agent).where(Agent.id == agent.id))
        await session.commit()
    async with ctx.control_db.session() as session:
        await session.execute(text("DELETE FROM toolkits WHERE id = :id"), {"id": NAMED_TOOLKIT_ID})
        await session.commit()


@pytest.fixture()
async def approved_sa_id(
    web_context: Context, owner_user_id: str, admin_user_id: str
) -> AsyncGenerator[str, None]:
    ctx = web_context
    async with ctx.admin_db.transaction() as session:
        sa = await ServiceAccountRepository.create(
            session,
            name="me-test-sa",
            owner_id=owner_user_id,
            registered_by=owner_user_id,
            description="SA for /me tests",
            created_by="usr_test",
        )
        await ServiceAccountRepository.set_approval(session, sa.id, approved_by=admin_user_id)
        # A live scope grant the presented token won't carry — exercises #673
        # for service accounts: /me must reflect current grants, not just the
        # token's baked-in scopes.
        await ActorScopeGrantRepository.grant(
            session,
            actor_id=sa.id,
            actor_type="service_account",
            scope="capabilities:read",
            granted_by=admin_user_id,
            created_by="usr_test",
        )
    yield sa.id

    async with ctx.admin_db.session() as session:
        await ActorScopeGrantRepository.revoke_all(session, sa.id)
        await session.execute(delete(ServiceAccount).where(ServiceAccount.id == sa.id))
        await session.commit()


def test_me_user_admin(web_context: Context, admin_user_id: str) -> None:
    token = _make_token(web_context, admin_user_id, ADMIN_EMAIL, ["org:admin"])
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "user"
    assert body["id"] == admin_user_id
    assert body["email"] == ADMIN_EMAIL
    assert body["name"] == "Me Admin"
    assert body["admin"] is True
    assert "org:admin" in body["scopes"]
    assert body["status"] == "active"
    assert body["must_change_password"] is False


def test_me_user_owner(web_context: Context, owner_user_id: str) -> None:
    token = _make_token(
        web_context,
        owner_user_id,
        OWNER_EMAIL,
        ["agents:read", "agents:write", "service-accounts:read", "service-accounts:write"],
    )
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "user"
    assert body["id"] == owner_user_id
    assert body["admin"] is False
    assert "agents:read" in body["scopes"]
    assert body["status"] == "active"


def test_me_agent(web_context: Context, approved_agent_id: str) -> None:
    token = _make_token(
        web_context,
        approved_agent_id,
        "agent@internal",
        ["toolkits:execute"],
        actor_type="agent",
    )
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "agent"
    assert body["id"] == approved_agent_id
    assert body["name"] == "me-test-agent"
    assert body["status"] == "active"
    # scopes reflect the *live* actor_scope_grants (the grant from the fixture),
    # not the token's baked-in scopes — this is the #673 fix. token_scopes
    # carries the presented token's view so a stale-grant gap is detectable.
    assert body["scopes"] == ["capabilities:read"]
    assert body["token_scopes"] == ["toolkits:execute"]
    assert body["parent_agent_id"] is None
    assert body["approved_by"] is not None
    bindings = {b["toolkit_id"]: b for b in body["toolkit_bindings"]}
    assert len(bindings) == 2
    # The bound toolkit's human-readable name is resolved from the control DB so
    # the agent can map the opaque id to a name (issue #686)…
    assert bindings[NAMED_TOOLKIT_ID]["name"] == NAMED_TOOLKIT_NAME
    # …while a binding whose toolkit row is absent degrades gracefully to null
    # rather than failing the whole response.
    assert bindings[ORPHAN_TOOLKIT_ID]["name"] is None


async def test_me_agent_opaque_token_surfaces_minted_scopes(
    web_context: Context, approved_agent_id: str
) -> None:
    """Regression: an opaque agent access token (``at_…``) must surface the scopes
    minted onto its token row.

    The auth verifier (``_make_auth_verifier``) used to discard the token-row
    scopes for agents and recompute via ``resolve_permissions_for_actor``, whose
    AGENT branch is an unimplemented stub that returns ``[]`` — so an approved
    ``capabilities:read`` never took effect and ``jentic access refresh`` could
    not help. Unlike the JWT path in ``test_me_agent`` (which falls back to the
    token's ``scopes`` claim), the opaque-token path has no such claim, and it is
    the path real CLI agents use after the jwt-bearer exchange. token_scopes must
    therefore echo the minted scopes, not come back empty.
    """
    token_svc = TokenService(web_context)
    access_token, _refresh = await token_svc.issue_pair(
        approved_agent_id, ActorType.AGENT, ["capabilities:read"]
    )
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {access_token}"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type"] == "agent"
    assert body["id"] == approved_agent_id
    # The presented opaque token carries capabilities:read on its row; the
    # verifier must surface it (pre-fix this came back [] and every
    # capabilities:read-gated call 403'd).
    assert body["token_scopes"] == ["capabilities:read"]
    # scopes still reflects the live actor_scope_grants.
    assert body["scopes"] == ["capabilities:read"]


def test_me_service_account(web_context: Context, approved_sa_id: str, owner_user_id: str) -> None:
    token = _make_token(
        web_context,
        approved_sa_id,
        "sa@internal",
        ["registry:read"],
        actor_type="service_account",
    )
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "service_account"
    assert body["id"] == approved_sa_id
    assert body["name"] == "me-test-sa"
    assert body["status"] == "active"
    # `scopes` reflects LIVE grants from actor_scope_grants (the fixture granted
    # capabilities:read), not the token's baked-in scopes — mirroring the agent
    # path (#673). token_scopes echoes the presented token.
    assert body["scopes"] == ["capabilities:read"]
    assert body["token_scopes"] == ["registry:read"]
    assert body["registered_by"] == owner_user_id
    assert body["approved_by"] is not None


def test_me_unauthenticated(web_context: Context) -> None:
    app = _build_app(web_context)
    with TestClient(app) as client:
        resp = client.get("/me")
    assert resp.status_code == 401


def test_me_invalid_token(web_context: Context) -> None:
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": "Bearer invalid.token.here"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 401


def test_me_unknown_prefix(web_context: Context) -> None:
    token = _make_token(web_context, "xyz_12345", "unknown@test.local", ["some:perm"])
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.get("/me")
    assert resp.status_code == 401
