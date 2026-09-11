"""Repository for ToolkitKey lifecycle operations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.control.core.schema.toolkit_keys import ToolkitKey
from jentic_one.control.core.schema.toolkits import Toolkit


class ToolkitKeyRepository:
    """Data access layer for ToolkitKey entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        toolkit_id: str,
        hashed_key: str,
        key_preview: str,
        lookup_hash: str,
        label: str | None = None,
        allowed_ips: list[str] | None = None,
        created_by: str,
    ) -> ToolkitKey:
        key = ToolkitKey(
            toolkit_id=toolkit_id,
            hashed_key=hashed_key,
            key_preview=key_preview,
            lookup_hash=lookup_hash,
            label=label,
            allowed_ips=allowed_ips,
            created_by=created_by,
        )
        session.add(key)
        await session.flush()
        return key

    @staticmethod
    async def get_by_id(session: AsyncSession, key_id: str) -> ToolkitKey | None:
        return await session.get(ToolkitKey, key_id)

    @staticmethod
    async def list_all_with_toolkits(session: AsyncSession) -> list[tuple[ToolkitKey, Toolkit]]:
        """Every key joined to its toolkit, id-ordered — the retirement job's inventory."""
        result = await session.execute(
            select(ToolkitKey, Toolkit)
            .join(Toolkit, Toolkit.id == ToolkitKey.toolkit_id)
            .order_by(ToolkitKey.id)
        )
        return [(key, toolkit) for key, toolkit in result.tuples().all()]

    @staticmethod
    async def stamp_migrated_actor(session: AsyncSession, key_id: str, actor_id: str) -> None:
        """Record the service account a key retired to (theme-5 Phase 4)."""
        key = await session.get(ToolkitKey, key_id)
        assert key is not None, f"toolkit key {key_id} vanished mid-retirement"
        key.migrated_actor_id = actor_id
        await session.flush()

    @staticmethod
    async def list_by_toolkit(
        session: AsyncSession,
        toolkit_id: str,
        *,
        cursor: tuple[datetime, str] | None = None,
        limit: int = 50,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[ToolkitKey]:
        """List keys for a toolkit with keyset pagination (created_at, id)."""
        stmt = (
            select(ToolkitKey)
            .where(ToolkitKey.toolkit_id == toolkit_id)
            .order_by(ToolkitKey.created_at.desc(), ToolkitKey.id.desc())
        )
        if cursor is not None:
            cursor_ts, cursor_id = cursor
            stmt = stmt.where(
                (ToolkitKey.created_at < cursor_ts)
                | ((ToolkitKey.created_at == cursor_ts) & (ToolkitKey.id < cursor_id))
            )
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        stmt = stmt.limit(limit + 1)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update(
        session: AsyncSession,
        key_id: str,
        *,
        label: str | None = None,
        allowed_ips: list[str] | None = None,
        revoked: bool | None = None,
    ) -> ToolkitKey | None:
        key = await session.get(ToolkitKey, key_id)
        if key is None:
            return None
        if label is not None:
            key.label = label
        if allowed_ips is not None:
            key.allowed_ips = allowed_ips
        if revoked is not None:
            key.revoked = revoked
        await session.flush()
        return key

    @staticmethod
    async def delete(session: AsyncSession, key_id: str) -> bool:
        key = await session.get(ToolkitKey, key_id)
        if key is None:
            return False
        await session.delete(key)
        await session.flush()
        return True
