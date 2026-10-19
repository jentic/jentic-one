"""Integration tests verifying audit entries are recorded for user lifecycle ops.

Exercises UserService against a real PostgreSQL database (no mocking) and
asserts that the expected audit_entries rows are written for create, update,
delete, disable, and enable.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select

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
from jentic_one.admin.services.schemas.users import UserCreatePayload, UserUpdatePayload
from jentic_one.admin.services.user_service import UserService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState
from jentic_one.shared.models.audit import AuditAction, AuditTargetType

pytestmark = pytest.mark.integration


async def _audit_entries_for(ctx: Context, target_id: str) -> list[AuditEntry]:
    """Fetch all audit entries for a user target, newest first."""
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(AuditEntry)
            .where(
                AuditEntry.target_type == AuditTargetType.USER.value,
                AuditEntry.target_id == target_id,
            )
            .order_by(AuditEntry.occurred_at.desc())
        )
        return list(result.scalars().all())


async def _cleanup_user(ctx: Context, user_id: str) -> None:
    async with ctx.admin_db.session() as session:
        await session.execute(delete(AuditEntry).where(AuditEntry.target_id == user_id))
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user_id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


@pytest.fixture()
async def admin_user(integration_context: Context) -> AsyncGenerator[str, None]:
    """Create an admin user to act as the granting actor."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="audit-admin@test.local",
            first_name="Audit",
            last_name="Admin",
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

    await _cleanup_user(ctx, user.id)


async def test_create_records_audit_entry(integration_context: Context, admin_user: str) -> None:
    ctx = integration_context
    service = UserService(ctx)

    created = await service.create(
        UserCreatePayload(
            email="audit-create@test.local",
            first_name="New",
            last_name="User",
            permissions=["users:read"],
        ),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id

    entries = await _audit_entries_for(ctx, user_id)
    create_entries = [e for e in entries if e.action == AuditAction.CREATE.value]
    assert len(create_entries) == 1
    entry = create_entries[0]
    assert entry.target_type == AuditTargetType.USER.value
    assert entry.target_id == user_id
    assert entry.actor_id == admin_user
    assert entry.after == {
        "email": "audit-create@test.local",
        "first_name": "New",
        "last_name": "User",
    }

    await _cleanup_user(ctx, user_id)


async def test_update_records_audit_with_before_and_after(
    integration_context: Context, admin_user: str
) -> None:
    ctx = integration_context
    service = UserService(ctx)

    created = await service.create(
        UserCreatePayload(email="audit-old@test.local", first_name="Old", last_name="Name"),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id

    await service.update(
        user_id,
        UserUpdatePayload(email="audit-new@test.local", first_name="New", last_name=None),
        identity=Identity(sub=admin_user, email="test@local", permissions=["org:admin"]),
    )

    entries = await _audit_entries_for(ctx, user_id)
    update_entries = [e for e in entries if e.action == AuditAction.UPDATE.value]
    assert len(update_entries) == 1
    entry = update_entries[0]
    assert entry.actor_id == admin_user
    assert entry.before == {
        "email": "audit-old@test.local",
        "first_name": "Old",
        "last_name": "Name",
    }
    assert entry.after == {
        "email": "audit-new@test.local",
        "first_name": "New",
        "last_name": "Name",
    }

    await _cleanup_user(ctx, user_id)


async def test_delete_records_audit_entry(integration_context: Context, admin_user: str) -> None:
    ctx = integration_context
    service = UserService(ctx)

    created = await service.create(
        UserCreatePayload(email="audit-delete@test.local", first_name="Del", last_name="User"),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id

    await service.delete(
        user_id, identity=Identity(sub=admin_user, email="test@local", permissions=["org:admin"])
    )

    entries = await _audit_entries_for(ctx, user_id)
    delete_entries = [e for e in entries if e.action == AuditAction.DELETE.value]
    assert len(delete_entries) == 1
    assert delete_entries[0].actor_id == admin_user

    await _cleanup_user(ctx, user_id)


async def test_disable_records_audit_entry(integration_context: Context, admin_user: str) -> None:
    ctx = integration_context
    service = UserService(ctx)

    created = await service.create(
        UserCreatePayload(email="audit-disable@test.local", first_name="Dis", last_name="User"),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id

    await service.disable(
        user_id, identity=Identity(sub=admin_user, email="test@local", permissions=["org:admin"])
    )

    entries = await _audit_entries_for(ctx, user_id)
    disable_entries = [e for e in entries if e.action == AuditAction.DISABLE.value]
    assert len(disable_entries) == 1
    assert disable_entries[0].actor_id == admin_user

    await _cleanup_user(ctx, user_id)


async def test_enable_records_audit_entry(integration_context: Context, admin_user: str) -> None:
    ctx = integration_context
    service = UserService(ctx)

    created = await service.create(
        UserCreatePayload(email="audit-enable@test.local", first_name="En", last_name="User"),
        identity=Identity(sub=admin_user, email="test@local"),
    )
    user_id = created.user.id

    await service.disable(
        user_id, identity=Identity(sub=admin_user, email="test@local", permissions=["org:admin"])
    )
    await service.enable(
        user_id, identity=Identity(sub=admin_user, email="test@local", permissions=["org:admin"])
    )

    entries = await _audit_entries_for(ctx, user_id)
    enable_entries = [e for e in entries if e.action == AuditAction.ENABLE.value]
    assert len(enable_entries) == 1
    assert enable_entries[0].actor_id == admin_user

    await _cleanup_user(ctx, user_id)
