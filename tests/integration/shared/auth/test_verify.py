import pytest

from jentic_one.admin.core.permissions import ORG_ADMIN
from jentic_one.admin.services.permission_service import PermissionService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.tokens import InvalidTokenError, issue_jwt
from jentic_one.shared.auth.verify import verify_token
from jentic_one.shared.context import Context


@pytest.mark.asyncio
async def test_verify_token_resolves_user_permissions(ctx: Context) -> None:
    # 1. Assign permissions to a user using PermissionService
    svc = PermissionService(ctx)
    user_id = "usr_token_test_1"
    await svc.set_assigned(
        user_id, [ORG_ADMIN], identity=Identity(sub="usr_admin", email="test@local")
    )

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
async def test_verify_token_resolves_agent_parent_permissions(ctx: Context) -> None:
    # 1. Assign permissions to the PARENT user
    svc = PermissionService(ctx)
    parent_id = "usr_parent_1"
    await svc.set_assigned(
        parent_id, [ORG_ADMIN], identity=Identity(sub="usr_admin", email="test@local")
    )

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
