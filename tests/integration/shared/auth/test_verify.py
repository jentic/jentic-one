"""Integration tests for ``verify_token``: DB-resolved permissions on a real admin DB."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.permissions import ORG_ADMIN
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import UserPermissionGrantRepository, UserRepository
from jentic_one.admin.services.permission_service import PermissionService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.tokens import InvalidTokenError, issue_jwt
from jentic_one.shared.auth.verify import verify_token
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration

_GRANTER_ID = "usr_verify_granter"
_USER_ID = "usr_token_test_1"
_PARENT_ID = "usr_parent_1"
_SEEDED_IDS = (_GRANTER_ID, _USER_ID, _PARENT_ID)


async def _delete_seeded(ctx: Context) -> None:
    async with ctx.admin_db.session() as session:
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id.in_(_SEEDED_IDS))
        )
        await session.execute(delete(User).where(User.id.in_(_SEEDED_IDS)))
        await session.commit()


@pytest.fixture()
async def granter(ctx: Context) -> AsyncGenerator[Identity, None]:
    """An ``org:admin`` granter plus the (permission-less) users the tests grant to."""
    await _delete_seeded(ctx)
    async with ctx.admin_db.session() as session:
        for user_id in _SEEDED_IDS:
            await UserRepository.create(
                session,
                id=user_id,
                email=f"{user_id}@verify.test",
                first_name="Verify",
                last_name="Test",
                active=True,
                created_by=_GRANTER_ID,
            )
        await UserPermissionGrantRepository.set_permissions(
            session, _GRANTER_ID, permissions={ORG_ADMIN}, granted_by=None, created_by=_GRANTER_ID
        )
        await session.commit()
    yield Identity(sub=_GRANTER_ID, email=f"{_GRANTER_ID}@verify.test", permissions=[ORG_ADMIN])
    await _delete_seeded(ctx)


@pytest.mark.asyncio
async def test_verify_token_resolves_user_permissions(ctx: Context, granter: Identity) -> None:
    # 1. Assign permissions to a user using PermissionService
    svc = PermissionService(ctx)
    user_id = _USER_ID
    await svc.set_assigned(user_id, [ORG_ADMIN], identity=granter)

    # 2. Mint a JWT for the user (notice we do NOT embed permissions)
    secret = "test-secret"
    claims = {"sub": user_id, "email": "user@test.com", "actor_type": "user", "scopes": ["*"]}
    token = issue_jwt(claims=claims, secret=secret, ttl_seconds=3600)

    # 3. Verify
    identity = await verify_token(token, secret=secret, ctx=ctx)

    assert identity.sub == user_id
    assert identity.actor_type == "user"
    assert "*" in identity.permissions

    # Crucial assertion: The verifier fetched permissions dynamically
    assert ORG_ADMIN in identity.permissions
    assert identity.parent_permissions == []


@pytest.mark.asyncio
async def test_verify_token_resolves_agent_parent_permissions(
    ctx: Context, granter: Identity
) -> None:
    # 1. Assign permissions to the PARENT user
    svc = PermissionService(ctx)
    parent_id = _PARENT_ID
    await svc.set_assigned(parent_id, [ORG_ADMIN], identity=granter)

    # 2. Mint a JWT for the AGENT
    secret = "test-secret"
    agent_id = "agt_token_test_1"
    claims = {
        "sub": agent_id,
        "email": "",
        "actor_type": "agent",
        "parent_actor_id": parent_id,
        "scopes": ["toolkits:read"],
    }
    token = issue_jwt(claims=claims, secret=secret, ttl_seconds=3600)

    # 3. Verify
    identity = await verify_token(token, secret=secret, ctx=ctx)

    assert identity.sub == agent_id
    assert identity.actor_type == "agent"
    assert identity.parent_actor_id == parent_id
    assert "toolkits:read" in identity.permissions

    # Crucial assertion: The verifier fetched the PARENT's permissions into parent_permissions
    assert ORG_ADMIN in identity.parent_permissions


@pytest.mark.asyncio
async def test_verify_token_refuses_retired_service_account_actor_type(ctx: Context) -> None:
    """``service_account`` is no longer an ``ActorType`` (theme-8 Phase 4), so a
    validly-signed JWT claiming it fails closed as an unknown actor type."""
    secret = "test-secret"
    claims = {
        "sub": "sva_token_test_1",
        "email": "",
        "actor_type": "service_account",
        "scopes": ["system:metrics"],
    }
    token = issue_jwt(claims=claims, secret=secret, ttl_seconds=3600)

    with pytest.raises(InvalidTokenError, match="unknown actor_type"):
        await verify_token(token, secret=secret, ctx=ctx)
