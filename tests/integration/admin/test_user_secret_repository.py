"""Integration tests for UserSecretRepository against real PostgreSQL."""

from __future__ import annotations

import asyncio
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

    async with admin_db.session() as session:
        assert await UserSecretRepository.record_failed_login(session, test_user) == 1
        await session.commit()

    async with admin_db.session() as session:
        assert await UserSecretRepository.record_failed_login(session, test_user) == 2
        await session.commit()

    async with admin_db.session() as session:
        secret = await UserSecretRepository.get_by_user_id(session, test_user)
        assert secret is not None
        assert secret.failed_login_count == 2


async def test_record_failed_login_concurrent_increments_are_not_lost(
    admin_db: DatabaseSession, test_user: str
) -> None:
    """Concurrent increments over separate transactions each see a distinct count."""
    async with admin_db.session() as session:
        await UserSecretRepository.create(session, user_id=test_user, created_by="usr_test")
        await session.commit()

    async def _fail_once() -> int | None:
        async with admin_db.transaction() as session:
            return await UserSecretRepository.record_failed_login(session, test_user)

    attempts = 8
    counts = await asyncio.gather(*(_fail_once() for _ in range(attempts)))

    assert sorted(c for c in counts if c is not None) == list(range(1, attempts + 1))
    async with admin_db.session() as session:
        secret = await UserSecretRepository.get_by_user_id(session, test_user)
        assert secret is not None
        assert secret.failed_login_count == attempts


async def test_reset_failed_logins(admin_db: DatabaseSession, test_user: str) -> None:
    async with admin_db.session() as session:
        await UserSecretRepository.create(session, user_id=test_user, created_by="usr_test")
        await session.commit()

    async with admin_db.session() as session:
        await UserSecretRepository.record_failed_login(session, test_user)
        await UserSecretRepository.record_failed_login(session, test_user)
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
        assert await UserSecretRepository.record_failed_login(session, "usr_noone") is None
        assert await UserSecretRepository.reset_failed_logins(session, "usr_noone") is None
        assert (
            await UserSecretRepository.lock_until(
                session, "usr_noone", locked_until=datetime.now(UTC)
            )
            is None
        )
