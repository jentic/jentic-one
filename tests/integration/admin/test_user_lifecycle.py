"""Integration test: full user lifecycle from creation to deletion.

Covers: create user -> redeem invite -> login -> change password ->
        list users -> disable -> enable -> delete.
"""

from __future__ import annotations

import pytest
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
from jentic_one.admin.services import (
    AuthService,
    InviteService,
    UserService,
)
from jentic_one.admin.services._support.passwords import hash_password
from jentic_one.admin.services.schemas.auth import ChangePasswordPayload, LoginPayload
from jentic_one.admin.services.schemas.users import UserCreatePayload
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState

pytestmark = pytest.mark.integration


async def test_user_lifecycle(integration_context: Context) -> None:
    """Full happy-path user lifecycle against real Postgres."""
    ctx = integration_context

    user_svc = UserService(ctx)
    auth_svc = AuthService(ctx)
    invite_svc = InviteService(ctx)

    # Step 1: Create bootstrap admin with org:admin so they can grant permissions
    async with ctx.admin_db.session() as session:
        admin = await UserRepository.create(
            session,
            email="admin-lifecycle@local",
            first_name="Admin",
            last_name="User",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(
            session,
            user_id=admin.id,
            password_hash=hash_password("admin-pass-123"),
            created_by="usr_test",
        )
        await UserPermissionGrantRepository.set_permissions(
            session, admin.id, permissions={"org:admin"}, granted_by=None, created_by="usr_test"
        )
        await session.commit()
    admin_id = admin.id

    # Step 2: Create a new user
    created = await user_svc.create(
        UserCreatePayload(
            email="lifecycle-test@example.com",
            first_name="Lifecycle",
            last_name="Test",
            permissions=["users:read"],
        ),
        identity=Identity(sub=admin_id, email="test@local"),
    )
    user_id = created.user.id
    invite_token = created.invite_token

    assert created.user.email == "lifecycle-test@example.com"
    assert created.user.invite_state == "pending"
    assert invite_token.startswith("inv_")

    # Step 3: Redeem the invite
    redeem_result = await invite_svc.redeem(invite_token, "secure-password-123")
    assert redeem_result.access_token
    assert redeem_result.token_type == "bearer"

    # Step 4: Login with the new password
    login_result = await auth_svc.login(
        LoginPayload(email="lifecycle-test@example.com", password="secure-password-123")
    )
    assert login_result.access_token

    # Step 5: Verify the token
    identity = await auth_svc.verify_token(login_result.access_token)
    assert identity.sub == user_id
    assert identity.email == "lifecycle-test@example.com"
    assert "users:read" in identity.permissions

    # Step 6: Change password
    await auth_svc.change_own_password(
        ChangePasswordPayload(
            current_password="secure-password-123",
            new_password="newer-password-456",
        ),
        identity=Identity(sub=user_id, email="test@local"),
    )

    # Step 7: Login with new password
    login_result2 = await auth_svc.login(
        LoginPayload(email="lifecycle-test@example.com", password="newer-password-456")
    )
    assert login_result2.access_token

    # Step 8: List users
    page = await user_svc.list_all(
        identity=Identity(sub=admin_id, email="test@local", permissions=["org:admin"])
    )
    user_ids_in_page = [u.id for u in page.data]
    assert user_id in user_ids_in_page

    # Step 9: Disable user
    await user_svc.disable(
        user_id, identity=Identity(sub=admin_id, email="test@local", permissions=["org:admin"])
    )
    disabled = await user_svc.get_by_id(user_id)
    assert disabled.active is False

    # Step 10: Enable user
    await user_svc.enable(
        user_id, identity=Identity(sub=admin_id, email="test@local", permissions=["org:admin"])
    )
    enabled = await user_svc.get_by_id(user_id)
    assert enabled.active is True

    # Step 11: Delete user (soft-delete)
    await user_svc.delete(
        user_id, identity=Identity(sub=admin_id, email="test@local", permissions=["org:admin"])
    )

    # Verify user is anonymised but still exists in DB
    async with ctx.admin_db.session() as session:
        deleted_user = await UserRepository.get_by_id(session, user_id)
    assert deleted_user is not None
    assert deleted_user.active is False
    assert deleted_user.email.startswith("deleted-") and deleted_user.email.endswith("@local")

    # Cleanup
    async with ctx.admin_db.session() as session:
        for uid in [admin_id, user_id]:
            await session.execute(delete(InviteToken).where(InviteToken.user_id == uid))
            await session.execute(
                delete(UserPermissionGrant).where(UserPermissionGrant.user_id == uid)
            )
            await session.execute(delete(UserSecret).where(UserSecret.user_id == uid))
            await session.execute(delete(User).where(User.id == uid))
        await session.commit()
