"""Integration tests for UserService guards on managing other users.

Covers the org:admin requirement on changing another user's email, the
privilege ceiling on changing another user's account status or permissions,
the last-active-``org:admin`` guard (including its row locking under
concurrent removals on Postgres), and the removal of external IdP identity
links when an account's email changes. Real database, no mocking.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Awaitable, Callable

import pytest
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.external_identities import ExternalIdentity
from jentic_one.admin.core.schema.invite_tokens import InviteToken
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    ExternalIdentityRepository,
    UserPermissionGrantRepository,
    UserRepository,
)
from jentic_one.admin.services._support.user_management import ensure_not_last_active_admin
from jentic_one.admin.services.errors import (
    LastActiveAdminError,
    UserManagementForbiddenError,
    UserNotFoundError,
)
from jentic_one.admin.services.permission_service import PermissionService
from jentic_one.admin.services.schemas.users import UserUpdatePayload
from jentic_one.admin.services.user_service import UserService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState

pytestmark = pytest.mark.integration

_SQLITE = os.environ.get("JENTIC_TEST_BACKEND", "postgres").lower() == "sqlite"

MakeUser = Callable[..., Awaitable[str]]


@pytest.fixture()
async def make_user(integration_context: Context) -> AsyncGenerator[MakeUser, None]:
    """Factory creating users with direct permission grants; removes them afterwards."""
    ctx = integration_context
    created: list[str] = []

    async def _make(
        email: str,
        permissions: set[str],
        *,
        invite_state: InviteState = InviteState.REDEEMED,
    ) -> str:
        async with ctx.admin_db.transaction() as session:
            user = await UserRepository.create(
                session,
                email=email,
                first_name="Guard",
                last_name="Test",
                invite_state=invite_state,
                created_by="usr_test",
            )
            if permissions:
                await UserPermissionGrantRepository.set_permissions(
                    session,
                    user.id,
                    permissions=permissions,
                    granted_by=None,
                    created_by="usr_test",
                )
        created.append(user.id)
        return user.id

    yield _make

    async with ctx.admin_db.transaction() as session:
        for uid in created:
            await session.execute(delete(AuditEntry).where(AuditEntry.target_id == uid))
            await session.execute(delete(ExternalIdentity).where(ExternalIdentity.user_id == uid))
            await session.execute(delete(InviteToken).where(InviteToken.user_id == uid))
            await session.execute(
                delete(UserPermissionGrant).where(UserPermissionGrant.user_id == uid)
            )
            await session.execute(delete(UserSecret).where(UserSecret.user_id == uid))
            await session.execute(delete(User).where(User.id == uid))


@pytest.fixture()
async def no_other_active_admins(integration_context: Context) -> AsyncGenerator[None, None]:
    """Temporarily deactivate any pre-existing active org:admin users.

    Makes "the last active org:admin" deterministic regardless of rows other
    tests left behind; the previous state is restored on teardown.
    """
    ctx = integration_context
    async with ctx.admin_db.transaction() as session:
        result = await session.execute(
            select(User.id)
            .join(UserPermissionGrant, UserPermissionGrant.user_id == User.id)
            .where(UserPermissionGrant.permission == "org:admin", User.active.is_(True))
        )
        deactivated = sorted(set(result.scalars().all()))
        if deactivated:
            await session.execute(update(User).where(User.id.in_(deactivated)).values(active=False))
    yield
    if deactivated:
        async with ctx.admin_db.transaction() as session:
            await session.execute(update(User).where(User.id.in_(deactivated)).values(active=True))


def _identity(user_id: str) -> Identity:
    return Identity(sub=user_id, email=f"{user_id}@test.local")


async def _get_user(ctx: Context, user_id: str) -> User:
    async with ctx.admin_db.session() as session:
        user = await UserRepository.get_by_id(session, user_id)
    assert user is not None
    return user


async def _link_count(ctx: Context, user_id: str) -> int:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(ExternalIdentity.id).where(ExternalIdentity.user_id == user_id)
        )
        return len(result.scalars().all())


async def _add_link(ctx: Context, user_id: str, subject: str) -> None:
    async with ctx.admin_db.transaction() as session:
        await ExternalIdentityRepository.create(
            session,
            provider="oidc",
            external_subject=subject,
            user_id=user_id,
            email="old@test.local",
            created_by=user_id,
        )


async def test_users_write_holder_cannot_change_admin_email(
    integration_context: Context, make_user: MakeUser
) -> None:
    ctx = integration_context
    admin_id = await make_user("guard-admin-email@test.local", {"org:admin"})
    manager_id = await make_user("guard-manager-email@test.local", {"users:write"})

    with pytest.raises(UserManagementForbiddenError):
        await UserService(ctx).update(
            admin_id,
            UserUpdatePayload(email="guard-new-owner@test.local"),
            identity=_identity(manager_id),
        )

    assert (await _get_user(ctx, admin_id)).email == "guard-admin-email@test.local"


async def test_users_write_holder_can_still_rename_admin(
    integration_context: Context, make_user: MakeUser
) -> None:
    """Profile-name edits stay available to users:write; only email/status are guarded."""
    ctx = integration_context
    admin_id = await make_user("guard-admin-rename@test.local", {"org:admin"})
    manager_id = await make_user("guard-manager-rename@test.local", {"users:write"})

    view = await UserService(ctx).update(
        admin_id,
        UserUpdatePayload(
            first_name="Renamed",
            # Same address, different case: not an email change.
            email="Guard-Admin-Rename@test.local",
        ),
        identity=_identity(manager_id),
    )
    assert view.first_name == "Renamed"


@pytest.mark.parametrize("operation", ["disable", "enable", "delete", "reissue_invite"])
async def test_users_write_holder_cannot_manage_more_privileged_user(
    integration_context: Context, make_user: MakeUser, operation: str
) -> None:
    ctx = integration_context
    admin_id = await make_user(
        f"guard-admin-{operation}@test.local", {"org:admin"}, invite_state=InviteState.PENDING
    )
    manager_id = await make_user(f"guard-manager-{operation}@test.local", {"users:write"})

    with pytest.raises(UserManagementForbiddenError):
        await getattr(UserService(ctx), operation)(admin_id, identity=_identity(manager_id))

    user = await _get_user(ctx, admin_id)
    assert user.active is True
    assert user.email == f"guard-admin-{operation}@test.local"


async def test_caller_must_hold_all_target_permissions(
    integration_context: Context, make_user: MakeUser
) -> None:
    """The ceiling is not specific to org:admin: any permission the caller lacks blocks it."""
    ctx = integration_context
    target_id = await make_user("guard-target-ceiling@test.local", {"agents:write"})
    manager_id = await make_user("guard-manager-ceiling@test.local", {"users:write"})

    with pytest.raises(UserManagementForbiddenError):
        await UserService(ctx).disable(target_id, identity=_identity(manager_id))


async def test_users_write_holder_can_manage_less_privileged_user(
    integration_context: Context, make_user: MakeUser
) -> None:
    ctx = integration_context
    service = UserService(ctx)
    target_id = await make_user("guard-target-ok@test.local", {"users:read"})
    manager_id = await make_user("guard-manager-ok@test.local", {"users:write"})
    identity = _identity(manager_id)

    view = await service.update(target_id, UserUpdatePayload(last_name="Ok"), identity=identity)
    assert view.last_name == "Ok"

    await service.disable(target_id, identity=identity)
    assert (await _get_user(ctx, target_id)).active is False
    await service.enable(target_id, identity=identity)
    assert (await _get_user(ctx, target_id)).active is True
    await service.delete(target_id, identity=identity)
    assert (await _get_user(ctx, target_id)).active is False


async def test_users_write_holder_cannot_change_permissionless_user_email(
    integration_context: Context, make_user: MakeUser
) -> None:
    """Only org:admin may change another user's email, whatever the target holds."""
    ctx = integration_context
    target_id = await make_user("guard-target-noperm@test.local", set())
    manager_id = await make_user("guard-manager-noperm@test.local", {"users:read", "users:write"})

    with pytest.raises(UserManagementForbiddenError):
        await UserService(ctx).update(
            target_id,
            UserUpdatePayload(email="guard-target-noperm-new@test.local"),
            identity=_identity(manager_id),
        )
    assert (await _get_user(ctx, target_id)).email == "guard-target-noperm@test.local"


async def test_org_admin_can_change_another_user_email(
    integration_context: Context, make_user: MakeUser
) -> None:
    ctx = integration_context
    target_id = await make_user("guard-target-admin-email@test.local", {"users:read"})
    admin_id = await make_user("guard-admin-changes-email@test.local", {"org:admin"})

    view = await UserService(ctx).update(
        target_id,
        UserUpdatePayload(email="guard-target-admin-email-new@test.local"),
        identity=_identity(admin_id),
    )
    assert view.email == "guard-target-admin-email-new@test.local"


async def test_users_write_holder_can_change_own_email(
    integration_context: Context, make_user: MakeUser
) -> None:
    ctx = integration_context
    manager_id = await make_user("guard-manager-self-email@test.local", {"users:write"})

    view = await UserService(ctx).update(
        manager_id,
        UserUpdatePayload(email="guard-manager-self-email-new@test.local"),
        identity=_identity(manager_id),
    )
    assert view.email == "guard-manager-self-email-new@test.local"


async def test_email_change_removes_external_identity_links(
    integration_context: Context, make_user: MakeUser
) -> None:
    ctx = integration_context
    admin_id = await make_user("guard-org-admin-link@test.local", {"org:admin"})
    target_id = await make_user("guard-linked@test.local", {"org:admin"})
    await _add_link(ctx, target_id, "ext-guard-linked")
    assert await _link_count(ctx, target_id) == 1

    service = UserService(ctx)
    # A name-only update keeps the link.
    await service.update(
        target_id, UserUpdatePayload(first_name="Still"), identity=_identity(admin_id)
    )
    assert await _link_count(ctx, target_id) == 1

    await service.update(
        target_id,
        UserUpdatePayload(email="guard-linked-new@test.local"),
        identity=_identity(admin_id),
    )
    assert await _link_count(ctx, target_id) == 0

    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(AuditEntry)
            .where(AuditEntry.target_id == target_id, AuditEntry.action == "update")
            .order_by(AuditEntry.occurred_at.desc())
        )
        latest = result.scalars().first()
    assert latest is not None
    assert latest.reason == "removed 1 external identity link(s)"


async def test_delete_removes_external_identity_links(
    integration_context: Context, make_user: MakeUser
) -> None:
    ctx = integration_context
    admin_id = await make_user("guard-org-admin-del@test.local", {"org:admin"})
    target_id = await make_user("guard-linked-del@test.local", {"users:read"})
    await _add_link(ctx, target_id, "ext-guard-linked-del")

    await UserService(ctx).delete(target_id, identity=_identity(admin_id))
    assert await _link_count(ctx, target_id) == 0


@pytest.mark.parametrize("operation", ["disable", "delete"])
async def test_last_active_admin_cannot_be_deactivated(
    integration_context: Context,
    make_user: MakeUser,
    no_other_active_admins: None,
    operation: str,
) -> None:
    ctx = integration_context
    service = UserService(ctx)
    admin_id = await make_user(f"guard-sole-admin-{operation}@test.local", {"org:admin"})

    with pytest.raises(LastActiveAdminError):
        await getattr(service, operation)(admin_id, identity=_identity(admin_id))
    assert (await _get_user(ctx, admin_id)).active is True

    # With a second active admin the same operation is allowed.
    second_id = await make_user(f"guard-second-admin-{operation}@test.local", {"org:admin"})
    await getattr(service, operation)(admin_id, identity=_identity(second_id))
    assert (await _get_user(ctx, admin_id)).active is False

    # The remaining admin is now the last one.
    with pytest.raises(LastActiveAdminError):
        await service.disable(second_id, identity=_identity(second_id))


async def _assigned(ctx: Context, user_id: str) -> set[str]:
    async with ctx.admin_db.session() as session:
        sets = await UserPermissionGrantRepository.get_permission_sets(session, [user_id])
    return sets[user_id]


async def test_users_write_holder_cannot_change_admin_permissions(
    integration_context: Context, make_user: MakeUser
) -> None:
    ctx = integration_context
    admin_id = await make_user("guard-admin-perms@test.local", {"org:admin"})
    manager_id = await make_user("guard-manager-perms@test.local", {"users:write"})

    with pytest.raises(UserManagementForbiddenError):
        await PermissionService(ctx).set_assigned(
            admin_id, ["users:read"], identity=_identity(manager_id)
        )
    assert await _assigned(ctx, admin_id) == {"org:admin"}


async def test_permission_change_requires_holding_all_target_permissions(
    integration_context: Context, make_user: MakeUser
) -> None:
    """Even a strip-only change is refused when the target holds something the caller lacks."""
    ctx = integration_context
    target_id = await make_user("guard-target-perms@test.local", {"agents:write", "users:read"})
    manager_id = await make_user("guard-manager-perms2@test.local", {"users:write"})

    with pytest.raises(UserManagementForbiddenError):
        await PermissionService(ctx).set_assigned(target_id, [], identity=_identity(manager_id))
    assert await _assigned(ctx, target_id) == {"agents:write", "users:read"}


async def test_users_write_holder_can_change_less_privileged_permissions(
    integration_context: Context, make_user: MakeUser
) -> None:
    ctx = integration_context
    target_id = await make_user("guard-target-perms-ok@test.local", {"users:read"})
    manager_id = await make_user("guard-manager-perms-ok@test.local", {"users:write"})

    await PermissionService(ctx).set_assigned(
        target_id, ["users:write"], identity=_identity(manager_id)
    )
    assert await _assigned(ctx, target_id) == {"users:write"}


async def test_set_permissions_unknown_user(
    integration_context: Context, make_user: MakeUser
) -> None:
    ctx = integration_context
    admin_id = await make_user("guard-admin-perms-404@test.local", {"org:admin"})

    with pytest.raises(UserNotFoundError):
        await PermissionService(ctx).set_assigned(
            "usr_does_not_exist", ["users:read"], identity=_identity(admin_id)
        )


async def test_last_active_admin_cannot_lose_org_admin(
    integration_context: Context, make_user: MakeUser, no_other_active_admins: None
) -> None:
    ctx = integration_context
    service = PermissionService(ctx)
    admin_id = await make_user("guard-sole-admin-perms@test.local", {"org:admin"})

    with pytest.raises(LastActiveAdminError):
        await service.set_assigned(admin_id, ["users:write"], identity=_identity(admin_id))
    assert await _assigned(ctx, admin_id) == {"org:admin"}

    # Keeping org:admin while changing other grants is fine.
    await service.set_assigned(admin_id, ["org:admin", "users:read"], identity=_identity(admin_id))

    # With a second active admin, org:admin can be removed.
    second_id = await make_user("guard-second-admin-perms@test.local", {"org:admin"})
    await service.set_assigned(admin_id, ["users:write"], identity=_identity(second_id))
    assert await _assigned(ctx, admin_id) == {"users:write"}

    # An inactive org:admin does not count as a remaining admin.
    inactive_id = await make_user("guard-inactive-admin-perms@test.local", {"org:admin"})
    async with ctx.admin_db.transaction() as session:
        await UserRepository.disable(session, inactive_id)
    with pytest.raises(LastActiveAdminError):
        await service.set_assigned(second_id, [], identity=_identity(second_id))


@pytest.mark.skipif(_SQLITE, reason="row locks require Postgres")
@pytest.mark.parametrize("second_operation", ["disable", "strip_org_admin"])
async def test_concurrent_admin_removal_is_serialised(
    integration_context: Context,
    make_user: MakeUser,
    no_other_active_admins: None,
    second_operation: str,
) -> None:
    """Removing two different admins concurrently cannot leave no active org:admin.

    The first transaction takes the admin row locks and disables its target
    without committing; the second must wait, then see that its own target is
    now the last active admin.
    """
    ctx = integration_context
    first_id = await make_user(f"guard-race-a-{second_operation}@test.local", {"org:admin"})
    second_id = await make_user(f"guard-race-b-{second_operation}@test.local", {"org:admin"})

    async def _second() -> None:
        if second_operation == "disable":
            await UserService(ctx).disable(second_id, identity=_identity(first_id))
        else:
            await PermissionService(ctx).set_assigned(second_id, [], identity=_identity(first_id))

    async with ctx.admin_db.transaction() as session:
        await ensure_not_last_active_admin(session, first_id)
        await UserRepository.disable(session, first_id)
        task = asyncio.create_task(_second())
        await asyncio.sleep(0.5)
        assert not task.done(), "second removal should wait on the admin row locks"

    with pytest.raises(LastActiveAdminError):
        await task
    assert (await _get_user(ctx, second_id)).active is True
    assert await _assigned(ctx, second_id) == {"org:admin"}
