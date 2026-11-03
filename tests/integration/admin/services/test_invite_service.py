"""Integration tests for InviteService against real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.audit import AuditEntry
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
from jentic_one.admin.services.errors import (
    InvalidInputError,
    InviteTokenAlreadyRedeemedError,
    InviteTokenNotFoundError,
)
from jentic_one.admin.services.invite_service import InviteService
from jentic_one.admin.services.schemas.users import UserCreatePayload
from jentic_one.admin.services.user_service import UserService
from jentic_one.admin.web.app import create_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState

pytestmark = pytest.mark.integration


@pytest.fixture()
async def admin_user(integration_context: Context) -> AsyncGenerator[str, None]:
    """Create an admin user for creating other users."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="invsvc-admin@test.local",
            first_name="Admin",
            last_name="Inv",
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


async def test_issue_and_redeem(integration_context: Context, admin_user: str) -> None:
    ctx = integration_context
    user_svc = UserService(ctx)
    invite_svc = InviteService(ctx)

    created = await user_svc.create(
        UserCreatePayload(email="invite-test@test.local", first_name="Inv", last_name="Test"),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id
    token = created.invite_token

    assert token.startswith("inv_")

    result = await invite_svc.redeem(token, "my-secure-password")
    assert result.access_token
    assert result.token_type == "bearer"

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user_id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_reissue_invalidates_old_token(integration_context: Context, admin_user: str) -> None:
    ctx = integration_context
    user_svc = UserService(ctx)
    invite_svc = InviteService(ctx)

    created = await user_svc.create(
        UserCreatePayload(email="reissue-inv@test.local", first_name="Re", last_name="Issue"),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id
    old_token = created.invite_token

    new_invite = await invite_svc.reissue(
        user_id, identity=Identity(sub="usr_test", email="test@local")
    )
    assert new_invite.token.startswith("inv_")
    assert new_invite.token != old_token

    # Old token should be invalid (already redeemed)
    with pytest.raises(InviteTokenAlreadyRedeemedError):
        await invite_svc.redeem(old_token, "some-password")

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user_id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_redeem_already_redeemed_raises(
    integration_context: Context, admin_user: str
) -> None:
    ctx = integration_context
    user_svc = UserService(ctx)
    invite_svc = InviteService(ctx)

    created = await user_svc.create(
        UserCreatePayload(email="double-redeem@test.local", first_name="Double", last_name="R"),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id
    token = created.invite_token

    await invite_svc.redeem(token, "first-password")

    with pytest.raises(InviteTokenAlreadyRedeemedError):
        await invite_svc.redeem(token, "second-password")

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user_id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_redeem_not_found(integration_context: Context) -> None:
    invite_svc = InviteService(integration_context)
    with pytest.raises(InviteTokenNotFoundError):
        await invite_svc.redeem("inv_nonexistent_token_value", "long-enough-pw")


async def test_redeem_short_password_raises(integration_context: Context) -> None:
    invite_svc = InviteService(integration_context)
    with pytest.raises(InvalidInputError, match="at least 12 characters"):
        await invite_svc.redeem("inv_doesnt_matter", "short")


async def test_redeem_route_status_for_disabled_deleted_and_reenabled_user(
    integration_context: Context, admin_user: str
) -> None:
    """POST /users:redeem-invite returns 404 invite_token_not_found when the
    invitee is disabled or deleted, and 409 invite_already_redeemed for a
    pre-disable token after re-enable."""
    ctx = integration_context
    user_svc = UserService(ctx)
    org_admin = Identity(sub=admin_user, email="test@local", permissions=["org:admin"])

    user_ids: list[str] = []
    async with ctx.admin_db.session() as session:
        for email in ("inv-route-dis@test.local", "inv-route-del@test.local"):
            stale = await UserRepository.get_by_email(session, email)
            if stale is not None:
                user_ids.append(stale.id)
        for uid in user_ids:
            await session.execute(delete(AuditEntry).where(AuditEntry.target_id == uid))
            await session.execute(delete(InviteToken).where(InviteToken.user_id == uid))
            await session.execute(
                delete(UserPermissionGrant).where(UserPermissionGrant.user_id == uid)
            )
            await session.execute(delete(UserSecret).where(UserSecret.user_id == uid))
            await session.execute(delete(User).where(User.id == uid))
        if user_ids:
            await session.commit()
    user_ids.clear()

    try:
        disabled = await user_svc.create(
            UserCreatePayload(email="inv-route-dis@test.local", first_name="Dis", last_name="R"),
            identity=org_admin,
        )
        user_ids.append(disabled.user.id)
        deleted = await user_svc.create(
            UserCreatePayload(email="inv-route-del@test.local", first_name="Del", last_name="R"),
            identity=org_admin,
        )
        user_ids.append(deleted.user.id)
        await user_svc.disable(disabled.user.id, identity=org_admin)
        await user_svc.delete(deleted.user.id, identity=org_admin)

        app = create_app(ctx)
        transport = httpx.ASGITransport(app=app)
        redeem_pw = "twelve_chars_p"  # pragma: allowlist secret
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unknown_resp = await client.post(
                "/users:redeem-invite",
                json={"invite_token": "inv_unknown_token", "password": redeem_pw},
            )
            dis_resp = await client.post(
                "/users:redeem-invite",
                json={"invite_token": disabled.invite_token, "password": redeem_pw},
            )
            del_resp = await client.post(
                "/users:redeem-invite",
                json={"invite_token": deleted.invite_token, "password": redeem_pw},
            )
            assert unknown_resp.status_code == dis_resp.status_code == del_resp.status_code == 404
            assert (
                unknown_resp.json()["type"]
                == dis_resp.json()["type"]
                == del_resp.json()["type"]
                == "invite_token_not_found"
            )
            assert unknown_resp.content == dis_resp.content == del_resp.content

            await user_svc.enable(disabled.user.id, identity=org_admin)
            reenabled_resp = await client.post(
                "/users:redeem-invite",
                json={"invite_token": disabled.invite_token, "password": redeem_pw},
            )
            assert reenabled_resp.status_code == 409
            assert reenabled_resp.json()["type"] == "invite_already_redeemed"
    finally:
        async with ctx.admin_db.session() as session:
            for uid in user_ids:
                await session.execute(delete(AuditEntry).where(AuditEntry.target_id == uid))
                await session.execute(delete(InviteToken).where(InviteToken.user_id == uid))
                await session.execute(
                    delete(UserPermissionGrant).where(UserPermissionGrant.user_id == uid)
                )
                await session.execute(delete(UserSecret).where(UserSecret.user_id == uid))
                await session.execute(delete(User).where(User.id == uid))
            await session.commit()
