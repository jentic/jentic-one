"""Repository for ActorPermissionGrant CRUD."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.actor_permission_grants import ActorPermissionGrant


class ActorPermissionGrantRepository:
    """Data access layer for ActorPermissionGrant entities — flush-only, never commits."""

    @staticmethod
    async def grant(
        session: AsyncSession,
        *,
        actor_id: str,
        actor_type: str,
        permission: str,
        granted_by: str | None = None,
        created_by: str,
    ) -> ActorPermissionGrant:
        grant = ActorPermissionGrant(
            actor_id=actor_id,
            actor_type=actor_type,
            permission=permission,
            granted_by=granted_by,
            created_by=created_by,
        )
        session.add(grant)
        await session.flush()
        return grant

    @staticmethod
    async def revoke(session: AsyncSession, *, actor_id: str, permission: str) -> bool:
        stmt = (
            delete(ActorPermissionGrant)
            .where(ActorPermissionGrant.actor_id == actor_id)
            .where(ActorPermissionGrant.permission == permission)
        )
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount) > 0  # type: ignore[attr-defined]

    @staticmethod
    async def list_for_actor(
        session: AsyncSession, actor_id: str, actor_type: str | None = None
    ) -> list[ActorPermissionGrant]:
        stmt = (
            select(ActorPermissionGrant)
            .where(ActorPermissionGrant.actor_id == actor_id)
            .order_by(ActorPermissionGrant.permission)
        )
        if actor_type is not None:
            stmt = stmt.where(ActorPermissionGrant.actor_type == actor_type)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_for_actors(
        session: AsyncSession, actor_ids: Sequence[str], actor_type: str | None = None
    ) -> list[ActorPermissionGrant]:
        """Batch variant of :meth:`list_for_actor`: one query for many actors."""
        if not actor_ids:
            return []
        stmt = (
            select(ActorPermissionGrant)
            .where(ActorPermissionGrant.actor_id.in_(list(actor_ids)))
            .order_by(ActorPermissionGrant.actor_id, ActorPermissionGrant.permission)
        )
        if actor_type is not None:
            stmt = stmt.where(ActorPermissionGrant.actor_type == actor_type)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def revoke_all(session: AsyncSession, actor_id: str) -> int:
        stmt = delete(ActorPermissionGrant).where(ActorPermissionGrant.actor_id == actor_id)
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]
