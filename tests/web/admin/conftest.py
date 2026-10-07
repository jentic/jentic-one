"""Admin web test fixtures — real services, real database."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.invite_tokens import InviteToken
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    UserPermissionGrantRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services._support.passwords import hash_password
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.admin.web.app import create_app
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration

ADMIN_EMAIL = "web-test-admin@test.local"
ADMIN_PASSWORD = "test-password-123"


def _build_app(ctx: Context) -> FastAPI:
    """Build the admin FastAPI app using the real factory, with lifespan disabled."""
    app = create_app(ctx)
    app.router.lifespan_context = noop_lifespan
    return app


@pytest.fixture()
async def admin_user_id(web_context: Context) -> AsyncGenerator[str, None]:
    """Create an admin user with org:admin permissions for test use."""
    ctx = web_context
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email=ADMIN_EMAIL,
            first_name="Web",
            last_name="Admin",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(
            session,
            user_id=user.id,
            password_hash=hash_password(ADMIN_PASSWORD),
            created_by="usr_test",
        )
        await UserPermissionGrantRepository.set_permissions(
            session, user.id, permissions={"org:admin"}, granted_by=None, created_by="usr_test"
        )
        await session.commit()
    yield user.id

    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user.id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user.id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


@pytest.fixture()
async def managed_user_id(web_context: Context) -> AsyncGenerator[str, None]:
    """A second, unprivileged user for tests that act on someone other than the caller."""
    ctx = web_context
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="web-managed@test.local",
            first_name="Web",
            last_name="Managed",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await session.commit()
    yield user.id

    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user.id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user.id)
        )
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


@pytest.fixture()
def auth_token(web_context: Context, admin_user_id: str) -> str:
    """Issue a valid JWT for the admin user."""
    config = web_context.config.admin.auth
    claims = {
        "sub": admin_user_id,
        "email": ADMIN_EMAIL,
        "actor_type": "user",
        "scopes": ["*"],
        "must_change_password": False,
    }
    return issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)


@pytest.fixture()
def authed_client(web_context: Context, auth_token: str) -> Iterator[TestClient]:
    """TestClient with a valid Authorization header — all requests are authenticated."""
    app = _build_app(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {auth_token}"}) as tc:
        yield tc


@pytest.fixture()
def unauthed_client(web_context: Context) -> Iterator[TestClient]:
    """TestClient with no Authorization header — requests are unauthenticated."""
    app = _build_app(web_context)
    with TestClient(app) as tc:
        yield tc
