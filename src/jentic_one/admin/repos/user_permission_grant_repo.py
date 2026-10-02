"""Repository for UserPermissionGrant CRUD."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant


class UserPermissionGrantRepository:
    """Data access layer for UserPermissionGrant entities — flush-only, never commits."""

    @staticmethod
    async def set_permissions(
        session: AsyncSession,
        user_id: str,
        *,
        permissions: set[str],
        granted_by: str | None = None,
        created_by: str,
    ) -> list[UserPermissionGrant]:
        """Replace all grants for a user with the given permission set."""
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user_id)
        )
        grants = []
        for perm in sorted(permissions):
            grant = UserPermissionGrant(
                user_id=user_id,
                permission=perm,
                granted_by=granted_by,
                created_by=created_by,
            )
            session.add(grant)
            grants.append(grant)
        await session.flush()
        return grants

    @staticmethod
    async def get_grants_for_user(session: AsyncSession, user_id: str) -> list[UserPermissionGrant]:
        stmt = (
            select(UserPermissionGrant)
            .where(UserPermissionGrant.user_id == user_id)
            .order_by(UserPermissionGrant.permission)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_permission_sets(
        session: AsyncSession, user_ids: Sequence[str]
    ) -> dict[str, set[str]]:
        """Return each user's directly-granted permission names (empty set if none)."""
        result_map: dict[str, set[str]] = {uid: set() for uid in user_ids}
        if not user_ids:
            return result_map
        stmt = select(UserPermissionGrant.user_id, UserPermissionGrant.permission).where(
            UserPermissionGrant.user_id.in_(user_ids)
        )
        result = await session.execute(stmt)
        for user_id, permission in result.all():
            result_map[user_id].add(permission)
        return result_map

    @staticmethod
    async def list_for_users(
        session: AsyncSession, user_ids: Sequence[str]
    ) -> list[UserPermissionGrant]:
        """Batch-fetch grants for multiple users in a single query."""
        if not user_ids:
            return []
        stmt = (
            select(UserPermissionGrant)
            .where(UserPermissionGrant.user_id.in_(user_ids))
            .order_by(UserPermissionGrant.user_id, UserPermissionGrant.permission)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_users_with_permission(
        session: AsyncSession, permission: str
    ) -> list[UserPermissionGrant]:
        stmt = (
            select(UserPermissionGrant)
            .where(UserPermissionGrant.permission == permission)
            .order_by(UserPermissionGrant.user_id)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
