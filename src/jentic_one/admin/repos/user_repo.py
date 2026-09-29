"""Repository for User CRUD."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.services.errors import UserNotFoundError
from jentic_one.shared.models import AuthProvider, InviteState


class UserRepository:
    """Data access layer for User entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        id: str | None = None,
        email: str,
        first_name: str,
        last_name: str,
        active: bool = True,
        auth_provider: str = AuthProvider.LOCAL,
        external_subject_id: str | None = None,
        must_change_password: bool = False,
        invite_state: InviteState = InviteState.PENDING,
        created_by: str,
    ) -> User:
        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            active=active,
            auth_provider=auth_provider,
            external_subject_id=external_subject_id,
            must_change_password=must_change_password,
            invite_state=invite_state,
            created_by=created_by,
        )
        if id is not None:
            user.id = id
        session.add(user)
        await session.flush()
        return user

    @staticmethod
    async def count(session: AsyncSession) -> int:
        """Return the total number of users — used to detect first-run setup."""
        result = await session.execute(select(sa_func.count()).select_from(User))
        return int(result.scalar_one())

    @staticmethod
    async def lock_active_holders_of(session: AsyncSession, permission: str) -> set[str]:
        """Return the ids of active users directly granted ``permission``, row-locked.

        Takes ``FOR UPDATE`` on the matching user and grant rows (ordered by user
        id so concurrent callers lock in the same order), so two transactions
        each removing a different holder serialise: the second waits for the
        first to commit and then no longer sees the holder it disabled or
        stripped. SQLite has no row locks and ignores the clause; its
        single-writer model already serialises the writes.
        """
        stmt = (
            select(User.id)
            .join(UserPermissionGrant, UserPermissionGrant.user_id == User.id)
            .where(UserPermissionGrant.permission == permission, User.active.is_(True))
            .order_by(User.id)
            .with_for_update()
        )
        result = await session.execute(stmt)
        return set(result.scalars().all())

    @staticmethod
    async def get_by_id(session: AsyncSession, user_id: str) -> User | None:
        return await session.get(User, user_id)

    @staticmethod
    async def get_by_email(session: AsyncSession, email: str) -> User | None:
        stmt = select(User).where(sa_func.lower(User.email) == email.lower())
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def update(
        session: AsyncSession,
        user_id: str,
        *,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        active: bool | None = None,
        auth_provider: str | None = None,
        external_subject_id: str | None = None,
        must_change_password: bool | None = None,
        invite_state: str | None = None,
    ) -> User:
        user = await session.get(User, user_id)
        if user is None:
            raise UserNotFoundError(user_id)

        if email is not None:
            user.email = email
        if first_name is not None:
            user.first_name = first_name
        if last_name is not None:
            user.last_name = last_name
        if active is not None:
            user.active = active
        if auth_provider is not None:
            user.auth_provider = auth_provider
        if external_subject_id is not None:
            user.external_subject_id = external_subject_id
        if must_change_password is not None:
            user.must_change_password = must_change_password
        if invite_state is not None:
            user.invite_state = invite_state

        await session.flush()
        return user

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        limit: int = 50,
        cursor: datetime | None = None,
        invite_state: InviteState | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[User]:
        stmt = select(User).order_by(User.created_at.desc()).limit(limit)
        if cursor is not None:
            stmt = stmt.where(User.created_at < cursor)
        if invite_state is not None:
            stmt = stmt.where(User.invite_state == invite_state)
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def disable(session: AsyncSession, user_id: str) -> User:
        user = await session.get(User, user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        user.active = False
        await session.flush()
        return user

    @staticmethod
    async def enable(session: AsyncSession, user_id: str) -> User:
        user = await session.get(User, user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        user.active = True
        await session.flush()
        return user
