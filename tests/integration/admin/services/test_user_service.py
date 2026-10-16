"""Integration tests for UserService against real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, update

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
from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.errors import (
    ConflictError,
    EmailAlreadyExistsError,
    UserNotFoundError,
)
from jentic_one.admin.services.schemas.users import UserCreatePayload, UserUpdatePayload
from jentic_one.admin.services.user_service import UserService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState

pytestmark = pytest.mark.integration

_ORG_ADMIN = Identity(sub="usr_test_org_admin", email="admin@test.local", permissions=["org:admin"])


@pytest.fixture()
async def admin_user(integration_context: Context) -> AsyncGenerator[str, None]:
    """Create an admin user for granting permissions."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="usrsvc-admin@test.local",
            first_name="Admin",
            last_name="User",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(
            session,
            user_id=user.id,
            password_hash=hash_password("admin-pass"),
            created_by="usr_test",
        )
        await UserPermissionGrantRepository.set_permissions(
            session, user.id, permissions={"org:admin"}, granted_by=None, created_by="usr_test"
        )
        await session.commit()
    yield user.id

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user.id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user.id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


async def test_create_user(integration_context: Context, admin_user: str) -> None:
    ctx = integration_context
    service = UserService(ctx)
    created = await service.create(
        UserCreatePayload(
            email="new-user@test.local",
            first_name="New",
            last_name="User",
            permissions=["users:read"],
        ),
        identity=Identity(sub=admin_user, email="test@local"),
    )

    assert created.user.email == "new-user@test.local"
    assert created.user.invite_state == "pending"
    assert created.invite_token.startswith("inv_")
    assert "users:read" in created.user.assigned

    # Cleanup
    user_id = created.user.id
    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user_id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_create_duplicate_email_raises(integration_context: Context, admin_user: str) -> None:
    ctx = integration_context
    service = UserService(ctx)

    created = await service.create(
        UserCreatePayload(email="dup@test.local", first_name="First", last_name="User"),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id

    with pytest.raises(EmailAlreadyExistsError):
        await service.create(
            UserCreatePayload(email="dup@test.local", first_name="Second", last_name="User"),
            identity=Identity(sub=admin_user, email="test@local"),
        )

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user_id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_get_by_id(integration_context: Context, admin_user: str) -> None:
    ctx = integration_context
    service = UserService(ctx)

    created = await service.create(
        UserCreatePayload(email="getme@test.local", first_name="Get", last_name="Me"),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id

    view = await service.get_by_id(user_id)
    assert view.id == user_id
    assert view.email == "getme@test.local"

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user_id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_get_by_id_not_found(integration_context: Context) -> None:
    service = UserService(integration_context)
    with pytest.raises(UserNotFoundError):
        await service.get_by_id("usr_nonexistent000000000000")


async def test_list(integration_context: Context, admin_user: str) -> None:
    ctx = integration_context
    service = UserService(ctx)

    created_ids = []
    for i in range(3):
        created = await service.create(
            UserCreatePayload(
                email=f"list-user-{i}@test.local", first_name=f"User{i}", last_name="List"
            ),
            identity=Identity(sub=admin_user, email="test@local"),
        )
        created_ids.append(created.user.id)

    page = await service.list_all(limit=50, identity=_ORG_ADMIN)
    page_ids = [u.id for u in page.data]
    for uid in created_ids:
        assert uid in page_ids

    # Cleanup
    async with ctx.admin_db.session() as session:
        for uid in created_ids:
            await session.execute(delete(InviteToken).where(InviteToken.user_id == uid))
            await session.execute(
                delete(UserPermissionGrant).where(UserPermissionGrant.user_id == uid)
            )
            await session.execute(delete(UserSecret).where(UserSecret.user_id == uid))
            await session.execute(delete(User).where(User.id == uid))
        await session.commit()


async def test_update(integration_context: Context, admin_user: str) -> None:
    ctx = integration_context
    service = UserService(ctx)

    created = await service.create(
        UserCreatePayload(email="update-me@test.local", first_name="Old", last_name="Name"),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id

    updated = await service.update(
        user_id,
        UserUpdatePayload(first_name="New"),
        identity=Identity(sub=admin_user, email="test@local", permissions=["org:admin"]),
    )
    assert updated.first_name == "New"
    assert updated.last_name == "Name"

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user_id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_delete_anonymises(integration_context: Context, admin_user: str) -> None:
    ctx = integration_context
    service = UserService(ctx)

    created = await service.create(
        UserCreatePayload(email="delete-me@test.local", first_name="Del", last_name="User"),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id

    await service.delete(user_id, identity=Identity(sub="usr_test", email="test@local"))

    async with ctx.admin_db.session() as session:
        user = await UserRepository.get_by_id(session, user_id)
    assert user is not None
    assert user.active is False
    assert user.email.startswith("deleted-") and user.email.endswith("@local")

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user_id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_deleted_user_stays_deleted_after_email_patch(
    integration_context: Context, admin_user: str
) -> None:
    """Delete is terminal: ``deleted_at``, not the tombstone email, is the marker.

    An operator cannot launder a deleted row back to life by rewriting its
    email first: the PATCH itself is refused, and enable, invite reissue and a
    password reset stay refused whatever the email reads.
    """
    ctx = integration_context
    service = UserService(ctx)
    org_admin = Identity(sub=admin_user, email="test@local", permissions=["org:admin"])
    created = await service.create(
        UserCreatePayload(email="undelete-me@test.local", first_name="Un", last_name="Del"),
        identity=org_admin,
    )
    user_id = created.user.id
    await service.delete(user_id, identity=org_admin)

    async with ctx.admin_db.session() as session:
        user = await UserRepository.get_by_id(session, user_id)
    assert user is not None and user.deleted_at is not None

    with pytest.raises(ConflictError, match="deleted user"):
        await service.update(
            user_id, UserUpdatePayload(email="revived@test.local"), identity=org_admin
        )

    # Even with the email rewritten underneath the service (the guard does not
    # key on it), the row stays terminal.
    async with ctx.admin_db.session() as session:
        await session.execute(
            update(User).where(User.id == user_id).values(email="revived@test.local")
        )
        await session.commit()
    with pytest.raises(ConflictError, match="deleted user"):
        await service.enable(user_id, identity=org_admin)
    with pytest.raises(ConflictError, match="deleted user"):
        await service.reissue_invite(user_id, identity=org_admin)
    with pytest.raises(ConflictError, match="deleted user"):
        await AuthService(ctx).reset_password(
            email="revived@test.local",
            temporary_password="temporary-pass-123",  # pragma: allowlist secret
        )

    async with ctx.admin_db.session() as session:
        user = await UserRepository.get_by_id(session, user_id)
    assert user is not None and user.active is False

    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user_id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_delete_not_found(integration_context: Context) -> None:
    service = UserService(integration_context)
    with pytest.raises(UserNotFoundError):
        await service.delete(
            "usr_nonexistent000000000000",
            identity=Identity(sub="usr_test", email="test@local"),
        )


async def test_disable_and_enable(integration_context: Context, admin_user: str) -> None:
    ctx = integration_context
    service = UserService(ctx)

    created = await service.create(
        UserCreatePayload(email="toggle@test.local", first_name="Toggle", last_name="User"),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id

    await service.disable(user_id, identity=Identity(sub="usr_test", email="test@local"))
    user_after_disable = await service.get_by_id(user_id)
    assert user_after_disable.active is False

    await service.enable(user_id, identity=Identity(sub="usr_test", email="test@local"))
    user_after_enable = await service.get_by_id(user_id)
    assert user_after_enable.active is True

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user_id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_reissue_invite(integration_context: Context, admin_user: str) -> None:
    ctx = integration_context
    service = UserService(ctx)

    created = await service.create(
        UserCreatePayload(email="reissue@test.local", first_name="Reissue", last_name="User"),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id

    invite = await service.reissue_invite(
        user_id, identity=Identity(sub="usr_test", email="test@local")
    )
    assert invite.token.startswith("inv_")
    assert invite.token != created.invite_token

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user_id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_reissue_invite_redeemed_raises(
    integration_context: Context, admin_user: str
) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="redeemed-inv@test.local",
            first_name="Redeemed",
            last_name="User",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(session, user_id=user.id, created_by="usr_test")
        await session.commit()
    user_id = user.id

    service = UserService(ctx)
    with pytest.raises(ConflictError):
        await service.reissue_invite(user_id, identity=Identity(sub="usr_test", email="test@local"))

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_get_by_id_derives_expired_for_lapsed_pending_invite(
    integration_context: Context, admin_user: str
) -> None:
    # A pending user whose only invite token has lapsed is derived as EXPIRED,
    # even though the DB still stores 'pending'.
    ctx = integration_context
    service = UserService(ctx)
    created = await service.create(
        UserCreatePayload(email="lapsed@test.local", first_name="Lapsed", last_name="User"),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id
    assert created.user.invite_state == InviteState.PENDING

    # Backdate the issued token so it is expired.
    async with ctx.admin_db.session() as session:
        await session.execute(
            update(InviteToken)
            .where(InviteToken.user_id == user_id)
            .values(expires_at=datetime.now(UTC) - timedelta(hours=1))
        )
        await session.commit()

    view = await service.get_by_id(user_id)
    assert view.invite_state == InviteState.EXPIRED
    # The stored column is untouched (derivation is read-only).
    async with ctx.admin_db.session() as session:
        stored = await UserRepository.get_by_id(session, user_id)
    assert stored is not None and stored.invite_state == InviteState.PENDING

    page = await service.list_all(limit=50, identity=_ORG_ADMIN)
    listed = next(u for u in page.data if u.id == user_id)
    assert listed.invite_state == InviteState.EXPIRED

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user_id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_get_by_id_pending_with_active_invite_not_expired(
    integration_context: Context, admin_user: str
) -> None:
    # A freshly-created (thus active-token) pending user stays 'pending'.
    ctx = integration_context
    service = UserService(ctx)
    created = await service.create(
        UserCreatePayload(email="fresh-pending@test.local", first_name="Fresh", last_name="User"),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id

    view = await service.get_by_id(user_id)
    assert view.invite_state == InviteState.PENDING

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user_id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_redeemed_invite_not_derived_as_expired(
    integration_context: Context, admin_user: str
) -> None:
    # A redeemed user is never overlaid (only 'pending' is), even with no active
    # token — confirms REDEEMED passes through.
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="redeemed-view@test.local",
            first_name="Redeemed",
            last_name="View",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(session, user_id=user.id, created_by="usr_test")
        await session.commit()
    user_id = user.id

    service = UserService(ctx)
    view = await service.get_by_id(user_id)
    assert view.invite_state == InviteState.REDEEMED

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


@pytest.fixture()
async def interleaved_invite_users(
    integration_context: Context, admin_user: str
) -> AsyncGenerator[tuple[list[str], list[str]], None]:
    """Three lapsed-invite and two live-invite pending users, newest first, interleaved.

    ``created_at`` is pinned in the future so these rows head the roster in a
    known order (expired, live, expired, live, expired), independent of other
    users in the database.
    """
    ctx = integration_context
    service = UserService(ctx)
    base = datetime.now(UTC) + timedelta(days=1)
    expired_ids: list[str] = []
    live_ids: list[str] = []
    for i, lapsed in enumerate([True, False, True, False, True]):
        created = await service.create(
            UserCreatePayload(
                email=f"invite-filter-{i}@test.local", first_name=f"Filter{i}", last_name="User"
            ),
            identity=Identity(sub=admin_user, email="test@local"),
        )
        user_id = created.user.id
        async with ctx.admin_db.session() as session:
            await session.execute(
                update(User)
                .where(User.id == user_id)
                .values(created_at=base - timedelta(minutes=i))
            )
            if lapsed:
                await session.execute(
                    update(InviteToken)
                    .where(InviteToken.user_id == user_id)
                    .values(expires_at=datetime.now(UTC) - timedelta(hours=1))
                )
            await session.commit()
        (expired_ids if lapsed else live_ids).append(user_id)

    yield expired_ids, live_ids

    async with ctx.admin_db.session() as session:
        for uid in expired_ids + live_ids:
            await session.execute(delete(InviteToken).where(InviteToken.user_id == uid))
            await session.execute(
                delete(UserPermissionGrant).where(UserPermissionGrant.user_id == uid)
            )
            await session.execute(delete(UserSecret).where(UserSecret.user_id == uid))
            await session.execute(delete(User).where(User.id == uid))
        await session.commit()


async def test_list_filter_expired_returns_only_lapsed_pending_users(
    integration_context: Context, interleaved_invite_users: tuple[list[str], list[str]]
) -> None:
    """Pins #1618: ``expired`` filters on the derived state, not the stored column."""
    expired_ids, live_ids = interleaved_invite_users
    service = UserService(integration_context)

    page = await service.list_all(limit=200, invite_state=InviteState.EXPIRED, identity=_ORG_ADMIN)

    listed = [u.id for u in page.data]
    assert listed[: len(expired_ids)] == expired_ids
    assert not set(live_ids) & set(listed)
    assert all(u.invite_state == InviteState.EXPIRED for u in page.data)


async def test_list_filter_pending_excludes_lapsed_invites(
    integration_context: Context, interleaved_invite_users: tuple[list[str], list[str]]
) -> None:
    """Pins #1618: ``pending`` excludes stored-pending users whose invite has lapsed."""
    expired_ids, live_ids = interleaved_invite_users
    service = UserService(integration_context)

    page = await service.list_all(limit=200, invite_state=InviteState.PENDING, identity=_ORG_ADMIN)

    listed = [u.id for u in page.data]
    assert listed[: len(live_ids)] == live_ids
    assert not set(expired_ids) & set(listed)
    assert all(u.invite_state == InviteState.PENDING for u in page.data)


@pytest.mark.parametrize("invite_state", [InviteState.EXPIRED, InviteState.PENDING])
async def test_list_filter_invite_state_paginates_with_exact_page_sizes(
    integration_context: Context,
    interleaved_invite_users: tuple[list[str], list[str]],
    invite_state: InviteState,
) -> None:
    """The derived-state filter runs before LIMIT, so every page is full until the last."""
    expired_ids, live_ids = interleaved_invite_users
    expected = expired_ids if invite_state == InviteState.EXPIRED else live_ids
    service = UserService(integration_context)

    first = await service.list_all(limit=1, invite_state=invite_state, identity=_ORG_ADMIN)
    assert [u.id for u in first.data] == expected[:1]
    assert first.has_more is True
    assert first.next_cursor is not None

    second = await service.list_all(
        cursor=first.next_cursor, limit=1, invite_state=invite_state, identity=_ORG_ADMIN
    )
    assert [u.id for u in second.data] == expected[1:2]
