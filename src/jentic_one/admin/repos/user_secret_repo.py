"""Repository for UserSecret CRUD."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import case, literal, null, or_, select, update
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
    async def record_failed_login(
        session: AsyncSession,
        user_id: str,
        *,
        lockout_threshold: int,
        locked_until: datetime,
        now: datetime,
    ) -> int | None:
        """Atomically count one failed login, locking the account at the threshold.

        A single ``UPDATE … RETURNING`` increments the counter in the database
        and, in the same statement, sets ``locked_until`` once the incremented
        count reaches ``lockout_threshold`` — concurrent failures each see a
        distinct count, so none is lost and the lock cannot be skipped. (The
        right-hand side of every ``SET`` reads the pre-update row on both
        Postgres and SQLite, so the ``CASE`` expressions all see the same
        pre-update count and lock.)

        Lock lifecycle, evaluated against ``now``:

        * **expired lock** (``locked_until <= now``) — the counter restarts at
          1 and the stale lock is cleared, so a lockout that has been waited
          out grants a fresh ``lockout_threshold`` attempts instead of
          re-locking on the first typo;
        * **active lock** — the counter still advances, but ``locked_until``
          is left as is, so stragglers of a burst that raced past the lock
          check cannot keep extending the lock;
        * **no lock** — the counter advances and the lock is set once it
          reaches ``lockout_threshold``.

        Returns the new failed-login count, or ``None`` when the user has no
        secret row.
        """
        # A NULL ``locked_until`` makes both comparisons NULL (not true), so a
        # never-locked row falls through to the ``else`` branches.
        now_param = literal(now, type_=UserSecret.locked_until.type)
        lock_expired = UserSecret.locked_until <= now_param
        lock_active = UserSecret.locked_until > now_param
        new_count = case((lock_expired, 0), else_=UserSecret.failed_login_count) + 1
        stmt = (
            update(UserSecret)
            .where(UserSecret.user_id == user_id)
            .values(
                failed_login_count=new_count,
                locked_until=case(
                    (lock_active, UserSecret.locked_until),
                    (
                        new_count >= lockout_threshold,
                        literal(locked_until, type_=UserSecret.locked_until.type),
                    ),
                    else_=null(),
                ),
            )
            .returning(UserSecret.failed_login_count)
            .execution_options(synchronize_session=False)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def reset_failed_logins_unless_locked(
        session: AsyncSession, user_id: str, *, now: datetime
    ) -> bool:
        """Atomically clear the failed-login state unless a lock is active at ``now``.

        The success-path counterpart of :meth:`record_failed_login`: a correct
        password verified concurrently with a burst of wrong ones must not
        clear a lockout that burst has just applied. On success both the
        counter and any expired ``locked_until`` are cleared. Returns ``False``
        when the account is locked (or has no secret row), leaving the row
        untouched.
        """
        stmt = (
            update(UserSecret)
            .where(
                UserSecret.user_id == user_id,
                or_(UserSecret.locked_until.is_(None), UserSecret.locked_until <= now),
            )
            .values(failed_login_count=0, locked_until=None)
            .returning(UserSecret.id)
            .execution_options(synchronize_session=False)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

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
