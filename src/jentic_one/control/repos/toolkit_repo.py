"""Repository for Toolkit CRUD operations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.control.core.schema.toolkits import Toolkit


class ToolkitRepository:
    """Data access layer for Toolkit entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        name: str,
        description: str | None = None,
        active: bool = True,
        created_by: str,
    ) -> Toolkit:
        toolkit = Toolkit(
            name=name,
            description=description,
            active=active,
            created_by=created_by,
        )
        session.add(toolkit)
        await session.flush()
        return toolkit

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        toolkit_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> Toolkit | None:
        if filters is None:
            return await session.get(Toolkit, toolkit_id)
        stmt = select(Toolkit).where(Toolkit.id == toolkit_id)
        for f in filters:
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_with_relations(
        session: AsyncSession,
        toolkit_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> Toolkit | None:
        """Load a toolkit with keys and bindings eagerly populated."""
        stmt = (
            select(Toolkit)
            .options(selectinload(Toolkit.keys), selectinload(Toolkit.bindings))
            .where(Toolkit.id == toolkit_id)
        )
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        cursor: tuple[datetime, str] | None = None,
        limit: int = 50,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[Toolkit]:
        """List toolkits with keyset pagination (created_at, id)."""
        stmt = (
            select(Toolkit)
            .options(selectinload(Toolkit.keys), selectinload(Toolkit.bindings))
            .order_by(Toolkit.created_at.desc(), Toolkit.id.desc())
        )
        if cursor is not None:
            cursor_ts, cursor_id = cursor
            stmt = stmt.where(
                (Toolkit.created_at < cursor_ts)
                | ((Toolkit.created_at == cursor_ts) & (Toolkit.id < cursor_id))
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
        toolkit_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        active: bool | None = None,
    ) -> Toolkit | None:
        toolkit = await session.get(Toolkit, toolkit_id)
        if toolkit is None:
            return None
        if name is not None:
            toolkit.name = name
        if description is not None:
            toolkit.description = description
        if active is not None:
            toolkit.active = active
        await session.flush()
        stmt = (
            select(Toolkit)
            .options(selectinload(Toolkit.keys), selectinload(Toolkit.bindings))
            .where(Toolkit.id == toolkit_id)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_names_by_ids(session: AsyncSession, ids: list[str]) -> dict[str, str]:
        """Batch-resolve toolkit IDs to their human-readable names."""
        if not ids:
            return {}
        stmt = select(Toolkit.id, Toolkit.name).where(Toolkit.id.in_(ids))
        result = await session.execute(stmt)
        return {row.id: row.name for row in result}

    @staticmethod
    async def delete(session: AsyncSession, toolkit_id: str) -> bool:
        stmt = delete(Toolkit).where(Toolkit.id == toolkit_id)
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount) > 0  # type: ignore[attr-defined]
