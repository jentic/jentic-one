"""Integration tests for PermissionService against real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.permissions import (
    CREDENTIALS_READ,
    CREDENTIALS_WRITE,
    EVENTS_READ,
    EVENTS_WRITE,
    ORG_ADMIN,
    USERS_READ,
    USERS_WRITE,
)
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    UserPermissionGrantRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services._support.passwords import hash_password
from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.errors import (
    InvalidCredentialsError,
    OrgAdminGrantForbiddenError,
    PermissionNotGrantableError,
    UnknownPermissionError,
)
from jentic_one.admin.services.permission_service import PermissionService
from jentic_one.admin.services.schemas.auth import LoginPayload
from jentic_one.shared.audit import AuditAction
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import DEFAULT_AGENT_PERMISSIONS, RETIRED_PERMISSIONS
from jentic_one.shared.auth.tokens import InvalidTokenError
from jentic_one.shared.auth.verify import verify_token
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState

pytestmark = pytest.mark.integration


@pytest.fixture()
async def admin_user(integration_context: Context) -> AsyncGenerator[str, None]:
    """Create an org:admin user and return their ID."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="perms-admin@test.local",
            first_name="Admin",
            last_name="Perms",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(session, user_id=user.id, created_by="usr_test")
        await UserPermissionGrantRepository.set_permissions(
            session, user.id, permissions={ORG_ADMIN}, granted_by=None, created_by="usr_test"
        )
        await session.commit()
    yield user.id

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user.id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


@pytest.fixture()
async def regular_user(integration_context: Context) -> AsyncGenerator[str, None]:
    """Create a user with users:manage permission and return their ID."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="perms-regular@test.local",
            first_name="Regular",
            last_name="User",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(session, user_id=user.id, created_by="usr_test")
        await UserPermissionGrantRepository.set_permissions(
            session, user.id, permissions={USERS_WRITE}, granted_by=None, created_by="usr_test"
        )
        await session.commit()
    yield user.id

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user.id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


async def test_get_effective_expands_implications(
    integration_context: Context, admin_user: str
) -> None:
    service = PermissionService(integration_context)
    view = await service.get_effective_for_user(admin_user)
    assert ORG_ADMIN in view.effective
    assert USERS_WRITE in view.effective
    assert USERS_READ in view.effective
    assert CREDENTIALS_WRITE in view.effective


async def test_get_assigned_for_user(integration_context: Context, admin_user: str) -> None:
    service = PermissionService(integration_context)
    assigned = await service.get_assigned_for_user(admin_user)
    assert ORG_ADMIN in assigned


async def test_list_catalogue_excludes_org_admin_for_non_admin(
    integration_context: Context, regular_user: str
) -> None:
    service = PermissionService(integration_context)
    entries = await service.list_catalogue(regular_user)
    names = [e.name for e in entries]
    assert ORG_ADMIN not in names
    assert USERS_WRITE in names


async def test_list_catalogue_includes_org_admin_for_admin(
    integration_context: Context, admin_user: str
) -> None:
    service = PermissionService(integration_context)
    entries = await service.list_catalogue(admin_user)
    names = [e.name for e in entries]
    assert ORG_ADMIN in names


async def test_list_catalogue_grantable_follows_agent_permission_ceiling(
    integration_context: Context, regular_user: str, admin_user: str
) -> None:
    """``grantable_by_caller`` mirrors what ``POST /agents`` accepts from the caller.

    The regular user holds ``users:write`` (+ implied ``users:read``); add
    ``agents:write`` so the admin-only exclusion is exercised on a held scope.
    """
    async with integration_context.admin_db.session() as session:
        await UserPermissionGrantRepository.set_permissions(
            session,
            regular_user,
            permissions={USERS_WRITE, "agents:write"},
            granted_by=None,
            created_by="usr_test",
        )
        await session.commit()
    service = PermissionService(integration_context)

    grantable = {e.name: e.grantable_by_caller for e in await service.list_catalogue(regular_user)}
    assert grantable["agents:write"] is False
    assert grantable[USERS_WRITE] is True
    assert grantable[USERS_READ] is True
    assert grantable["agents:read"] is True
    for scope in DEFAULT_AGENT_PERMISSIONS:
        assert grantable[scope] is True, scope
    assert grantable[EVENTS_WRITE] is False
    assert grantable[CREDENTIALS_WRITE] is False

    admin_view = await service.list_catalogue(admin_user)
    assert all(e.grantable_by_caller for e in admin_view)


async def test_validate_grants_unknown_permission(
    integration_context: Context, admin_user: str
) -> None:
    service = PermissionService(integration_context)
    with pytest.raises(UnknownPermissionError):
        await service.validate_grants(admin_user, ["nonexistent:perm"])


async def test_validate_grants_non_grantable(
    integration_context: Context, regular_user: str
) -> None:
    service = PermissionService(integration_context)
    with pytest.raises(PermissionNotGrantableError):
        await service.validate_grants(regular_user, [EVENTS_WRITE])


async def test_validate_grants_org_admin_forbidden_for_non_admin(
    integration_context: Context, regular_user: str
) -> None:
    service = PermissionService(integration_context)
    with pytest.raises(OrgAdminGrantForbiddenError):
        await service.validate_grants(regular_user, [ORG_ADMIN])


async def test_validate_grants_tolerates_retired_scopes(
    integration_context: Context, admin_user: str
) -> None:
    """Every ``RETIRED_PERMISSIONS`` member is accepted and skipped (never a 422).

    A stored grant set written before a scope retirement (theme-5 toolkit
    scopes, theme-7 ``owner:access-requests:read``) must re-submit unchanged;
    the retired string is stored as-is and grants nothing.
    """
    service = PermissionService(integration_context)
    await service.validate_grants(admin_user, sorted(RETIRED_PERMISSIONS))  # must not raise


async def test_set_assigned(integration_context: Context, admin_user: str) -> None:
    ctx = integration_context
    # Create a target user
    async with ctx.admin_db.session() as session:
        target = await UserRepository.create(
            session,
            email="perms-target@test.local",
            first_name="Target",
            last_name="User",
            created_by="usr_test",
        )
        await UserSecretRepository.create(session, user_id=target.id, created_by="usr_test")
        await session.commit()
    target_id = target.id

    service = PermissionService(ctx)
    result = await service.set_assigned(
        target_id,
        [CREDENTIALS_WRITE, USERS_READ],
        identity=Identity(sub=admin_user, email="test@local", permissions=["org:admin"]),
    )
    assert CREDENTIALS_WRITE in result
    assert USERS_READ in result

    # Verify effective includes implied
    view = await service.get_effective_for_user(target_id)
    assert CREDENTIALS_READ in view.effective

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == target_id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == target_id))
        await session.execute(delete(User).where(User.id == target_id))
        await session.commit()


# ── Login sessions follow a narrowed permission set ─────────────────────────

_LOGIN_EMAIL = "perms-login@test.local"
_LOGIN_PW = "perms-login-passw0rd"  # pragma: allowlist secret


@pytest.fixture()
async def login_user(integration_context: Context) -> AsyncGenerator[str, None]:
    """A password user with no grants (each test sets its own); returns the ID."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email=_LOGIN_EMAIL,
            first_name="Login",
            last_name="Perms",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(
            session, user_id=user.id, password_hash=hash_password(_LOGIN_PW), created_by="usr_test"
        )
        await session.commit()
    yield user.id

    async with ctx.admin_db.session() as session:
        await session.execute(delete(AuditEntry).where(AuditEntry.target_id == user.id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user.id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


async def _grant_and_login(ctx: Context, user_id: str, permissions: set[str]) -> str:
    async with ctx.admin_db.session() as session:
        await UserPermissionGrantRepository.set_permissions(
            session, user_id, permissions=permissions, granted_by=None, created_by="usr_test"
        )
        await session.commit()
    bundle = await AuthService(ctx).login(LoginPayload(email=_LOGIN_EMAIL, password=_LOGIN_PW))
    return bundle.access_token


async def _revoke_reasons(ctx: Context, user_id: str) -> list[str | None]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(AuditEntry.reason).where(
                AuditEntry.target_id == user_id, AuditEntry.action == AuditAction.REVOKE
            )
        )
    return list(result.scalars().all())


async def _verify(ctx: Context, token: str) -> Identity:
    return await verify_token(
        token, secret=ctx.config.admin.auth.jwt_secret.get_secret_value(), ctx=ctx
    )


async def test_set_assigned_removing_a_permission_ends_existing_login_sessions(
    integration_context: Context, admin_user: str, login_user: str
) -> None:
    """A login JWT carrying a removed permission is refused on its next use.

    The token embeds the permission set it was minted with, so it must stop
    verifying (and stop refreshing) once that set is narrowed; a fresh login
    carries the narrower set.
    """
    ctx = integration_context
    token = await _grant_and_login(ctx, login_user, {EVENTS_READ, USERS_READ})
    assert EVENTS_READ in (await _verify(ctx, token)).permissions

    org_admin = Identity(sub=admin_user, email="test@local", permissions=[ORG_ADMIN])
    await PermissionService(ctx).set_assigned(login_user, [USERS_READ], identity=org_admin)

    with pytest.raises(InvalidTokenError, match="revoked login session"):
        await _verify(ctx, token)
    with pytest.raises(InvalidCredentialsError):
        await AuthService(ctx).refresh(token)
    assert await _revoke_reasons(ctx, login_user) == [
        f"revoked: {EVENTS_READ}; login sessions ended"
    ]

    relogin = await AuthService(ctx).login(LoginPayload(email=_LOGIN_EMAIL, password=_LOGIN_PW))
    identity = await _verify(ctx, relogin.access_token)
    assert EVENTS_READ not in identity.permissions
    assert USERS_READ in identity.permissions


async def test_set_assigned_narrowing_own_permissions_ends_own_login_sessions(
    integration_context: Context, login_user: str
) -> None:
    """A caller who removes one of their own permissions is signed out too."""
    ctx = integration_context
    token = await _grant_and_login(ctx, login_user, {USERS_WRITE, EVENTS_READ})
    caller = await _verify(ctx, token)

    await PermissionService(ctx).set_assigned(login_user, [USERS_WRITE], identity=caller)

    with pytest.raises(InvalidTokenError, match="revoked login session"):
        await _verify(ctx, token)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param({USERS_READ}, [USERS_READ, EVENTS_READ], id="add-only"),
        pytest.param({USERS_WRITE, USERS_READ}, [USERS_WRITE], id="drop-implied"),
        pytest.param({USERS_READ, sorted(RETIRED_PERMISSIONS)[0]}, [USERS_READ], id="drop-retired"),
        pytest.param({USERS_READ}, [USERS_READ], id="unchanged"),
    ],
)
async def test_set_assigned_without_narrowing_keeps_login_sessions(
    integration_context: Context,
    admin_user: str,
    login_user: str,
    before: set[str],
    after: list[str],
) -> None:
    """An edit that drops no effective permission leaves login JWTs valid."""
    ctx = integration_context
    token = await _grant_and_login(ctx, login_user, before)

    org_admin = Identity(sub=admin_user, email="test@local", permissions=[ORG_ADMIN])
    await PermissionService(ctx).set_assigned(login_user, after, identity=org_admin)

    assert (await _verify(ctx, token)).sub == login_user
    for reason in await _revoke_reasons(ctx, login_user):
        assert reason is not None
        assert not reason.endswith("login sessions ended")


async def test_replace_permissions_returns_the_replaced_grants(
    integration_context: Context, login_user: str
) -> None:
    """The replaced set is what the write removed, read at write time."""
    ctx = integration_context
    async with ctx.admin_db.transaction() as session:
        replaced, _ = await UserPermissionGrantRepository.replace_permissions(
            session, login_user, permissions={USERS_READ, EVENTS_READ}, created_by="usr_test"
        )
        assert replaced == set()
    async with ctx.admin_db.transaction() as session:
        replaced, grants = await UserPermissionGrantRepository.replace_permissions(
            session, login_user, permissions={USERS_WRITE}, created_by="usr_test"
        )
    assert replaced == {USERS_READ, EVENTS_READ}
    assert [g.permission for g in grants] == [USERS_WRITE]
