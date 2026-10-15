"""Integration tests verifying structured log output for auth events.

Exercises AuthService against a real PostgreSQL database (no mocking) and
asserts on the structured log records emitted for login, lockout, and
password-change flows.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import structlog.testing
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
from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.errors import AccountLockedError, InvalidCredentialsError
from jentic_one.admin.services.schemas.auth import ChangePasswordPayload, LoginPayload
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState

pytestmark = pytest.mark.integration


@pytest.fixture()
async def logging_user(
    integration_context: Context,
) -> AsyncGenerator[tuple[str, str], None]:
    """Create a user with a known password and return (user_id, email)."""
    ctx = integration_context
    email = "auth-logging@test.local"
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email=email,
            first_name="Log",
            last_name="Test",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(
            session,
            user_id=user.id,
            password_hash=hash_password("correct-password"),
            created_by="usr_test",
        )
        await UserPermissionGrantRepository.set_permissions(
            session, user.id, permissions={"users:read"}, granted_by=None, created_by="usr_test"
        )
        await session.commit()
    yield (user.id, email)

    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user.id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user.id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


async def test_login_success_logs_info(
    integration_context: Context, logging_user: tuple[str, str]
) -> None:
    user_id, email = logging_user
    service = AuthService(integration_context)

    with structlog.testing.capture_logs() as logs:
        await service.login(
            LoginPayload(email=email, password="correct-password")  # pragma: allowlist secret
        )

    login_logs = [log for log in logs if log["event"] == "login_success"]
    assert len(login_logs) == 1
    assert login_logs[0]["user_id"] == user_id
    assert login_logs[0]["log_level"] == "info"


async def test_login_failure_logs_warning(
    integration_context: Context, logging_user: tuple[str, str]
) -> None:
    _, email = logging_user
    service = AuthService(integration_context)

    with structlog.testing.capture_logs() as logs, pytest.raises(InvalidCredentialsError):
        await service.login(
            LoginPayload(email=email, password="wrong-password")  # pragma: allowlist secret
        )

    login_logs = [log for log in logs if log["event"] == "login_failed"]
    assert len(login_logs) == 1
    assert login_logs[0]["email"] == email
    assert login_logs[0]["log_level"] == "warning"


async def test_login_lockout_logs_warning(
    integration_context: Context, logging_user: tuple[str, str]
) -> None:
    user_id, email = logging_user
    ctx = integration_context

    # Drive the failed-login count to one below the lockout threshold so the
    # next failure trips the lockout.
    threshold = ctx.config.admin.auth.failed_login_lockout_threshold
    async with ctx.admin_db.transaction() as session:
        for _ in range(threshold - 1):
            await UserSecretRepository.record_failed_login(
                session,
                user_id,
                lockout_threshold=threshold,
                locked_until=datetime.now(UTC) + timedelta(minutes=15),
                now=datetime.now(UTC),
            )

    service = AuthService(ctx)
    with structlog.testing.capture_logs() as logs, pytest.raises(InvalidCredentialsError):
        await service.login(
            LoginPayload(email=email, password="wrong-password")  # pragma: allowlist secret
        )

    lockout_logs = [log for log in logs if log["event"] == "account_locked"]
    assert len(lockout_logs) == 1
    assert lockout_logs[0]["user_id"] == user_id
    assert lockout_logs[0]["log_level"] == "warning"
    assert "locked_until" in lockout_logs[0]

    # Unlock and reset for any subsequent test relying on the fixture.
    async with ctx.admin_db.transaction() as session:
        await UserSecretRepository.lock_until(
            session, user_id, locked_until=datetime.now(UTC) - timedelta(minutes=1)
        )
        await UserSecretRepository.reset_failed_logins(session, user_id)


async def test_login_already_locked_raises(
    integration_context: Context, logging_user: tuple[str, str]
) -> None:
    user_id, email = logging_user
    ctx = integration_context

    async with ctx.admin_db.transaction() as session:
        await UserSecretRepository.lock_until(
            session, user_id, locked_until=datetime.now(UTC) + timedelta(minutes=15)
        )

    service = AuthService(ctx)
    with pytest.raises(AccountLockedError):
        await service.login(
            LoginPayload(email=email, password="correct-password")  # pragma: allowlist secret
        )

    async with ctx.admin_db.transaction() as session:
        await UserSecretRepository.lock_until(
            session, user_id, locked_until=datetime.now(UTC) - timedelta(minutes=1)
        )


async def test_change_password_logs_info(
    integration_context: Context, logging_user: tuple[str, str]
) -> None:
    user_id, _ = logging_user
    service = AuthService(integration_context)

    with structlog.testing.capture_logs() as logs:
        await service.change_own_password(
            ChangePasswordPayload(
                current_password="correct-password",  # pragma: allowlist secret
                new_password="newpassword123",  # pragma: allowlist secret
            ),
            identity=Identity(sub=user_id, email="test@local"),
        )

    pw_logs = [log for log in logs if log["event"] == "password_changed"]
    assert len(pw_logs) == 1
    assert pw_logs[0]["user_id"] == user_id
    assert pw_logs[0]["log_level"] == "info"
