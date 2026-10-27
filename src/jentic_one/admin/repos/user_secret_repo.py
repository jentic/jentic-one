"""Repository for UserSecret CRUD."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.user_secrets import UserSecret


class UserSecretRepository:
    """Data access layer for UserSecret entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        user_id: str,
        password_hash: str | None = None,
        password_algo: str = "argon2id",
        created_by: str,
    ) -> UserSecret:
        secret = UserSecret(
            user_id=user_id,
            password_hash=password_hash,
            password_algo=password_algo,
            created_by=created_by,
        )
        session.add(secret)
        await session.flush()
        return secret

    @staticmethod
    async def get_by_user_id(session: AsyncSession, user_id: str) -> UserSecret | None:
        stmt = select(UserSecret).where(UserSecret.user_id == user_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def set_password_hash(
        session: AsyncSession,
        user_id: str,
        *,
        password_hash: str,
        password_algo: str = "argon2id",
        created_by: str,
    ) -> UserSecret:
        stmt = select(UserSecret).where(UserSecret.user_id == user_id)
        result = await session.execute(stmt)
        secret = result.scalar_one_or_none()
        if secret is None:
            secret = UserSecret(
                user_id=user_id,
                password_hash=password_hash,
                password_algo=password_algo,
                password_changed_at=datetime.now(UTC),
                created_by=created_by,
            )
            session.add(secret)
        else:
            secret.password_hash = password_hash
            secret.password_algo = password_algo
            secret.password_changed_at = datetime.now(UTC)
        await session.flush()
        return secret

    @staticmethod
    async def record_failed_login(session: AsyncSession, user_id: str) -> int | None:
        """Atomically add one to the failed-login count and return the new count.

        The increment runs in the database as a single ``UPDATE … RETURNING``,
        so concurrent failures each observe a distinct count and none is lost.
        Callers decide the lockout from the returned count, never from a value
        read earlier. Returns ``None`` when the user has no secret row.
        """
        stmt = (
            update(UserSecret)
            .where(UserSecret.user_id == user_id)
            .values(failed_login_count=UserSecret.failed_login_count + 1)
            .returning(UserSecret.failed_login_count)
            .execution_options(synchronize_session="fetch")
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def reset_failed_logins(session: AsyncSession, user_id: str) -> UserSecret | None:
        stmt = select(UserSecret).where(UserSecret.user_id == user_id)
        result = await session.execute(stmt)
        secret = result.scalar_one_or_none()
        if secret is None:
            return None
        secret.failed_login_count = 0
        await session.flush()
        return secret

    @staticmethod
    async def lock_until(
        session: AsyncSession, user_id: str, *, locked_until: datetime
    ) -> UserSecret | None:
        stmt = select(UserSecret).where(UserSecret.user_id == user_id)
        result = await session.execute(stmt)
        secret = result.scalar_one_or_none()
        if secret is None:
            return None
        secret.locked_until = locked_until
        await session.flush()
        return secret

    @staticmethod
    async def unlock(session: AsyncSession, user_id: str) -> UserSecret | None:
        """Clear any active lockout and reset the failed-login counter.

        Used by an operator password reset: a user who forgot their password may
        also be locked out from repeated failed attempts, so the reset must free
        the account in the same breath as issuing the temporary credential.
        """
        stmt = select(UserSecret).where(UserSecret.user_id == user_id)
        result = await session.execute(stmt)
        secret = result.scalar_one_or_none()
        if secret is None:
            return None
        secret.locked_until = None
        secret.failed_login_count = 0
        await session.flush()
        return secret
