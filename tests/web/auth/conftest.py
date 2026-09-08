"""Auth web test fixtures — real services, real database."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.invite_tokens import InviteToken
from jentic_one.admin.core.schema.service_accounts import ServiceAccount
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    AgentRepository,
    ServiceAccountRepository,
    UserPermissionGrantRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services._support.passwords import hash_password
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.auth.web.app import create_app
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration

ADMIN_EMAIL = "auth-web-test-admin@test.local"
OWNER_EMAIL = "auth-web-test-owner@test.local"


def _build_app(ctx: Context) -> FastAPI:
    """Build the auth FastAPI app using the real factory, with lifespan disabled."""
    app = create_app(ctx)
    app.router.lifespan_context = noop_lifespan
    return app


@pytest.fixture()
async def admin_user_id(web_context: Context) -> AsyncGenerator[str, None]:
    """Create an admin user with org:admin permissions for test use."""
    ctx = web_context
    async with ctx.admin_db.transaction() as session:
        user = await UserRepository.create(
            session,
            email=ADMIN_EMAIL,
            first_name="Auth",
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
    """Create an owner user with agents:read/write and service-accounts:read/write."""
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
async def test_agent_id(web_context: Context, owner_user_id: str) -> AsyncGenerator[str, None]:
    """Create a test agent in pending status."""
    ctx = web_context
    async with ctx.admin_db.transaction() as session:
        agent = await AgentRepository.create(
            session,
            name="test-agent",
            owner_id=owner_user_id,
            registered_by=owner_user_id,
            description="Test agent for web tests",
            created_by="usr_test",
        )
    yield agent.id

    async with ctx.admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.id == agent.id))
        await session.commit()


@pytest.fixture()
async def test_service_account_id(
    web_context: Context, owner_user_id: str
) -> AsyncGenerator[str, None]:
    """Create a test service account in pending status."""
    ctx = web_context
    async with ctx.admin_db.transaction() as session:
        sa = await ServiceAccountRepository.create(
            session,
            name="test-service-account",
            owner_id=owner_user_id,
            registered_by=owner_user_id,
            description="Test SA for web tests",
            created_by="usr_test",
        )
    yield sa.id

    async with ctx.admin_db.session() as session:
        await session.execute(delete(ServiceAccount).where(ServiceAccount.id == sa.id))
        await session.commit()


def _make_token(ctx: Context, user_id: str, email: str, permissions: list[str]) -> str:
    config = ctx.config.admin.auth
    claims = {
        "sub": user_id,
        "email": email,
        "actor_type": "user",
        "permissions": permissions,
        "must_change_password": False,
    }
    return issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)


@pytest.fixture()
def admin_token(web_context: Context, admin_user_id: str) -> str:
    """Issue a valid JWT with org:admin."""
    return _make_token(web_context, admin_user_id, ADMIN_EMAIL, ["org:admin"])


@pytest.fixture()
def owner_token(web_context: Context, owner_user_id: str) -> str:
    """Issue a valid JWT with agents and service-accounts permissions."""
    return _make_token(
        web_context,
        owner_user_id,
        OWNER_EMAIL,
        ["agents:read", "agents:write", "service-accounts:read", "service-accounts:write"],
    )


@pytest.fixture()
def admin_client(web_context: Context, admin_token: str) -> Iterator[TestClient]:
    """TestClient authenticated as org:admin."""
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {admin_token}"}) as tc:
        yield tc


@pytest.fixture()
def owner_client(web_context: Context, owner_token: str) -> Iterator[TestClient]:
    """TestClient authenticated as owner user."""
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {owner_token}"}) as tc:
        yield tc


@pytest.fixture()
def unauthed_client(web_context: Context) -> Iterator[TestClient]:
    """TestClient with no Authorization header."""
    app = _build_app(web_context)
    with TestClient(app) as tc:
        yield tc
