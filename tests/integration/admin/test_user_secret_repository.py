"""Integration tests for UserSecretRepository against real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import UserRepository, UserSecretRepository
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_secrets(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.session() as session:
        await session.execute(delete(UserSecret))
        await session.execute(delete(User))
        await session.commit()
    yield
    async with admin_db.session() as session:
        await session.execute(delete(UserSecret))
        await session.execute(delete(User))
        await session.commit()


@pytest.fixture()
async def test_user(admin_db: DatabaseSession, clean_secrets: None) -> str:
    async with admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="secret-test@example.com",
            first_name="Secret",
            last_name="Test",
            created_by="usr_test",
        )
        await session.commit()
        return user.id


async def test_create_and_get_by_user_id(admin_db: DatabaseSession, test_user: str) -> None:
    async with admin_db.session() as session:
        secret = await UserSecretRepository.create(
            session,
            user_id=test_user,
            password_hash="$argon2id$hash_here",
            password_algo="argon2id",
            created_by="usr_test",
        )
        await session.commit()
        assert secret.id.startswith("usec_")

    async with admin_db.session() as session:
        loaded = await UserSecretRepository.get_by_user_id(session, test_user)
        assert loaded is not None
        assert loaded.password_hash == "$argon2id$hash_here"
        assert loaded.password_algo == "argon2id"
        assert loaded.failed_login_count == 0
        assert loaded.locked_until is None


async def test_set_password_hash_creates_if_missing(
    admin_db: DatabaseSession, test_user: str
) -> None:
    async with admin_db.session() as session:
        secret = await UserSecretRepository.set_password_hash(
            session,
            test_user,
            password_hash="$argon2id$new_hash",
            created_by="usr_test",
        )
        await session.commit()
        assert secret.password_hash == "$argon2id$new_hash"
        assert secret.password_changed_at is not None


async def test_set_password_hash_updates_existing(
    admin_db: DatabaseSession, test_user: str
) -> None:
    async with admin_db.session() as session:
        await UserSecretRepository.create(
            session, user_id=test_user, password_hash="$argon2id$old", created_by="usr_test"
        )
        await session.commit()

    async with admin_db.session() as session:
        updated = await UserSecretRepository.set_password_hash(
            session, test_user, password_hash="$argon2id$updated", created_by="usr_test"
        )
        await session.commit()
        assert updated.password_hash == "$argon2id$updated"
        assert updated.password_changed_at is not None


async def test_record_failed_login(admin_db: DatabaseSession, test_user: str) -> None:
    async with admin_db.session() as session:
        await UserSecretRepository.create(session, user_id=test_user, created_by="usr_test")
        await session.commit()

    lock_time = datetime.now(UTC) + timedelta(minutes=15)
    async with admin_db.session() as session:
        count = await UserSecretRepository.record_failed_login(
            session, test_user, lockout_threshold=3, locked_until=lock_time, now=datetime.now(UTC)
        )
        await session.commit()
        assert count == 1

    async with admin_db.session() as session:
        count = await UserSecretRepository.record_failed_login(
            session, test_user, lockout_threshold=3, locked_until=lock_time, now=datetime.now(UTC)
        )
        await session.commit()
        assert count == 2

    # Below the threshold the account stays unlocked.
    async with admin_db.session() as session:
        secret = await UserSecretRepository.get_by_user_id(session, test_user)
        assert secret is not None
        assert secret.failed_login_count == 2
        assert secret.locked_until is None


async def test_record_failed_login_locks_at_threshold(
    admin_db: DatabaseSession, test_user: str
) -> None:
    """The increment that reaches the threshold sets ``locked_until`` in the same UPDATE."""
    async with admin_db.session() as session:
        await UserSecretRepository.create(session, user_id=test_user, created_by="usr_test")
        await session.commit()

    lock_time = datetime.now(UTC) + timedelta(minutes=15)
    async with admin_db.session() as session:
        counts = [
            await UserSecretRepository.record_failed_login(
                session,
                test_user,
                lockout_threshold=2,
                locked_until=lock_time,
                now=datetime.now(UTC),
            )
            for _ in range(2)
        ]
        await session.commit()
    assert counts == [1, 2]

    async with admin_db.session() as session:
        secret = await UserSecretRepository.get_by_user_id(session, test_user)
        assert secret is not None
        assert secret.failed_login_count == 2
        assert secret.locked_until is not None
        assert abs((secret.locked_until - lock_time).total_seconds()) < 1


async def test_reset_failed_logins_unless_locked(admin_db: DatabaseSession, test_user: str) -> None:
    """The success-path reset clears the counter only while no lock is active."""
    now = datetime.now(UTC)
    async with admin_db.session() as session:
        await UserSecretRepository.create(session, user_id=test_user, created_by="usr_test")
        await UserSecretRepository.record_failed_login(
            session,
            test_user,
            lockout_threshold=1,
            locked_until=now + timedelta(minutes=15),
            now=now,
        )
        await session.commit()

    async with admin_db.session() as session:
        assert not await UserSecretRepository.reset_failed_logins_unless_locked(
            session, test_user, now=now
        )
        await session.commit()
        secret = await UserSecretRepository.get_by_user_id(session, test_user)
        assert secret is not None
        assert secret.failed_login_count == 1

    # Once the lock has expired the reset goes through.
    async with admin_db.session() as session:
        assert await UserSecretRepository.reset_failed_logins_unless_locked(
            session, test_user, now=now + timedelta(minutes=16)
        )
        await session.commit()

    async with admin_db.session() as session:
        secret = await UserSecretRepository.get_by_user_id(session, test_user)
        assert secret is not None
        assert secret.failed_login_count == 0
        # The expired lock timestamp is cleared along with the counter.
        assert secret.locked_until is None


async def test_record_failed_login_after_expired_lock_starts_fresh_count(
    admin_db: DatabaseSession, test_user: str
) -> None:
    """A waited-out lockout grants a fresh threshold: one typo neither re-locks nor carries over."""
    t0 = datetime.now(UTC)
    threshold = 3
    lock_time = t0 + timedelta(minutes=15)
    async with admin_db.session() as session:
        await UserSecretRepository.create(session, user_id=test_user, created_by="usr_test")
        for _ in range(threshold):
            await UserSecretRepository.record_failed_login(
                session, test_user, lockout_threshold=threshold, locked_until=lock_time, now=t0
            )
        await session.commit()

    after_expiry = t0 + timedelta(minutes=16)
    async with admin_db.session() as session:
        count = await UserSecretRepository.record_failed_login(
            session,
            test_user,
            lockout_threshold=threshold,
            locked_until=after_expiry + timedelta(minutes=15),
            now=after_expiry,
        )
        await session.commit()
    assert count == 1

    async with admin_db.session() as session:
        secret = await UserSecretRepository.get_by_user_id(session, test_user)
        assert secret is not None
        assert secret.failed_login_count == 1
        assert secret.locked_until is None


async def test_record_failed_login_during_active_lock_does_not_extend_it(
    admin_db: DatabaseSession, test_user: str
) -> None:
    """Stragglers that hit an already-active lock advance the count but keep ``locked_until``."""
    t0 = datetime.now(UTC)
    lock_time = t0 + timedelta(minutes=15)
    async with admin_db.session() as session:
        await UserSecretRepository.create(session, user_id=test_user, created_by="usr_test")
        await UserSecretRepository.record_failed_login(
            session, test_user, lockout_threshold=1, locked_until=lock_time, now=t0
        )
        count = await UserSecretRepository.record_failed_login(
            session,
            test_user,
            lockout_threshold=1,
            locked_until=lock_time + timedelta(minutes=5),
            now=t0 + timedelta(seconds=1),
        )
        await session.commit()
    assert count == 2

    async with admin_db.session() as session:
        secret = await UserSecretRepository.get_by_user_id(session, test_user)
        assert secret is not None
        assert secret.locked_until is not None
        assert abs((secret.locked_until - lock_time).total_seconds()) < 1


async def test_reset_failed_logins(admin_db: DatabaseSession, test_user: str) -> None:
    async with admin_db.session() as session:
        await UserSecretRepository.create(session, user_id=test_user, created_by="usr_test")
        await session.commit()

    async with admin_db.session() as session:
        for _ in range(2):
            await UserSecretRepository.record_failed_login(
                session,
                test_user,
                lockout_threshold=5,
                locked_until=datetime.now(UTC) + timedelta(minutes=15),
                now=datetime.now(UTC),
            )
        await session.commit()

    async with admin_db.session() as session:
        secret = await UserSecretRepository.reset_failed_logins(session, test_user)
        await session.commit()
        assert secret is not None
        assert secret.failed_login_count == 0


async def test_lock_until(admin_db: DatabaseSession, test_user: str) -> None:
    lock_time = datetime.now(UTC) + timedelta(minutes=30)
    async with admin_db.session() as session:
        await UserSecretRepository.create(session, user_id=test_user, created_by="usr_test")
        await session.commit()

    async with admin_db.session() as session:
        secret = await UserSecretRepository.lock_until(session, test_user, locked_until=lock_time)
        await session.commit()
        assert secret is not None
        assert secret.locked_until is not None
        assert abs((secret.locked_until - lock_time).total_seconds()) < 1


async def test_operations_on_nonexistent_user(
    admin_db: DatabaseSession, clean_secrets: None
) -> None:
    async with admin_db.session() as session:
        assert await UserSecretRepository.get_by_user_id(session, "usr_noone") is None
        assert (
            await UserSecretRepository.record_failed_login(
                session,
                "usr_noone",
                lockout_threshold=5,
                locked_until=datetime.now(UTC),
                now=datetime.now(UTC),
            )
            is None
        )
        assert not await UserSecretRepository.reset_failed_logins_unless_locked(
            session, "usr_noone", now=datetime.now(UTC)
        )
        assert await UserSecretRepository.reset_failed_logins(session, "usr_noone") is None
        assert (
            await UserSecretRepository.lock_until(
                session, "usr_noone", locked_until=datetime.now(UTC)
            )
            is None
        )
