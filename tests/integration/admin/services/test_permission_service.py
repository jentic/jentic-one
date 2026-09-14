"""Integration tests for PermissionService against real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.permissions import (
    CREDENTIALS_READ,
    CREDENTIALS_WRITE,
    EVENTS_WRITE,
    ORG_ADMIN,
    USERS_READ,
    USERS_WRITE,
)
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    UserPermissionGrantRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services.errors import (
    OrgAdminGrantForbiddenError,
    PermissionNotGrantableError,
    UnknownPermissionError,
)
from jentic_one.admin.services.permission_service import PermissionService
from jentic_one.shared.auth.identity import Identity
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
        identity=Identity(sub=admin_user, email="test@local"),
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
