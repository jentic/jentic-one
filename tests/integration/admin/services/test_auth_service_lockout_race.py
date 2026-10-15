"""Concurrency tests for the failed-login lockout (#1441).

Fires bursts of wrong-password attempts through separate sessions at once and
pins that the atomic ``UPDATE … RETURNING`` counter loses no increment and
trips the lock exactly at the configured threshold, however the burst
interleaves.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import structlog.testing
from sqlalchemy import delete

from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import UserRepository, UserSecretRepository
from jentic_one.admin.services._support.passwords import hash_password
from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.errors import AccountLockedError, InvalidCredentialsError
from jentic_one.admin.services.schemas.auth import LoginPayload
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState

pytestmark = pytest.mark.integration

_EMAIL = "lockout-race@example.com"
_PASSWORD = "correct-password"  # pragma: allowlist secret
_WRONG = "wrong-password"  # pragma: allowlist secret


@pytest.fixture()
async def race_user(integration_context: Context) -> AsyncGenerator[str, None]:
    """A user with a password and a zeroed failed-login counter."""
    ctx = integration_context
    async with ctx.admin_db.transaction() as session:
        user = await UserRepository.create(
            session,
            email=_EMAIL,
            first_name="Lockout",
            last_name="Race",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(
            session,
            user_id=user.id,
            password_hash=hash_password(_PASSWORD),
            created_by="usr_test",
        )
        user_id = user.id
    yield user_id
    async with ctx.admin_db.transaction() as session:
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))


async def _load_secret(ctx: Context, user_id: str) -> UserSecret:
    async with ctx.admin_db.session() as session:
        secret = await UserSecretRepository.get_by_user_id(session, user_id)
    assert secret is not None
    return secret


async def test_record_failed_login_concurrent_increments_are_not_lost(
    integration_context: Context, race_user: str
) -> None:
    """N concurrent increments over separate sessions yield counts 1..N and lock once."""
    ctx = integration_context
    attempts = 8
    threshold = 5
    locked_until = datetime.now(UTC) + timedelta(minutes=15)

    async def _fail_once() -> int | None:
        async with ctx.admin_db.transaction() as session:
            return await UserSecretRepository.record_failed_login(
                session, race_user, lockout_threshold=threshold, locked_until=locked_until
            )

    counts = await asyncio.gather(*(_fail_once() for _ in range(attempts)))

    # Every increment observed a distinct post-update count: none was lost.
    assert sorted(c for c in counts if c is not None) == list(range(1, attempts + 1))
    secret = await _load_secret(ctx, race_user)
    assert secret.failed_login_count == attempts
    assert secret.locked_until is not None
    assert abs((secret.locked_until - locked_until).total_seconds()) < 1


async def test_authenticate_concurrent_wrong_passwords_lock_at_threshold(
    integration_context: Context, race_user: str
) -> None:
    """A concurrent burst of exactly ``threshold`` wrong guesses locks the account.

    Every attempt reads the counter before any of them writes it, so a lockout
    decided from that stale read never fires; the decision must come from the
    database's post-increment count.
    """
    ctx = integration_context
    threshold = ctx.config.admin.auth.failed_login_lockout_threshold
    service = AuthService(ctx)

    with structlog.testing.capture_logs() as logs:
        results = await asyncio.gather(
            *(
                service.authenticate(LoginPayload(email=_EMAIL, password=_WRONG))
                for _ in range(threshold)
            ),
            return_exceptions=True,
        )

    assert all(isinstance(r, InvalidCredentialsError | AccountLockedError) for r in results)
    failures = sum(isinstance(r, InvalidCredentialsError) for r in results)

    secret = await _load_secret(ctx, race_user)
    # Each attempt that reached the password check was counted exactly once.
    assert secret.failed_login_count == failures
    assert failures == threshold
    assert secret.locked_until is not None
    assert secret.locked_until > datetime.now(UTC)
    # The lock tripped exactly once — on the attempt whose count hit the threshold.
    assert sum(1 for log in logs if log["event"] == "account_locked") == 1

    # The lock holds: even the correct password is refused.
    with pytest.raises(AccountLockedError):
        await service.authenticate(LoginPayload(email=_EMAIL, password=_PASSWORD))


async def test_authenticate_burst_beyond_threshold_keeps_account_locked(
    integration_context: Context, race_user: str
) -> None:
    """A burst larger than the threshold leaves the account locked with no lost counts."""
    ctx = integration_context
    threshold = ctx.config.admin.auth.failed_login_lockout_threshold
    service = AuthService(ctx)

    results = await asyncio.gather(
        *(
            service.authenticate(LoginPayload(email=_EMAIL, password=_WRONG))
            for _ in range(threshold * 2)
        ),
        return_exceptions=True,
    )

    assert all(isinstance(r, InvalidCredentialsError | AccountLockedError) for r in results)
    failures = sum(isinstance(r, InvalidCredentialsError) for r in results)
    secret = await _load_secret(ctx, race_user)
    assert failures >= threshold
    assert secret.failed_login_count == failures
    assert secret.locked_until is not None
    assert secret.locked_until > datetime.now(UTC)
