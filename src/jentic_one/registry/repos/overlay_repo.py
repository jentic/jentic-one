"""Repository for Overlay entities."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum, auto
from typing import Any, cast

from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from jentic_one.registry.core.schema.overlays import Overlay
from jentic_one.shared.models import OverlayStatus


class _Unset(Enum):
    TOKEN = auto()


_UNSET = _Unset.TOKEN


class OverlayRepository:
    """Data access layer for Overlay entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        api_id: uuid.UUID,
        document: dict[str, Any],
        target_revision_id: uuid.UUID | None = None,
        contributed_by: str | None = None,
        created_by: str,
    ) -> Overlay:
        overlay = Overlay(
            api_id=api_id,
            document=document,
            target_revision_id=target_revision_id,
            contributed_by=contributed_by,
            created_by=created_by,
        )
        session.add(overlay)
        await session.flush()
        return overlay

    @staticmethod
    async def get_for_api(
        session: AsyncSession, api_id: uuid.UUID, overlay_id: str
    ) -> Overlay | None:
        result = await session.execute(
            select(Overlay).where(Overlay.api_id == api_id, Overlay.id == overlay_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_page(
        session: AsyncSession,
        *,
        api_id: uuid.UUID,
        limit: int = 50,
        cursor_created_at: datetime | None = None,
        cursor_id: str | None = None,
        status: str | None = None,
    ) -> list[Overlay]:
        stmt = (
            select(Overlay)
            .where(Overlay.api_id == api_id)
            .order_by(Overlay.created_at.desc(), Overlay.id.desc())
            .limit(limit)
        )
        if cursor_created_at is not None and cursor_id is not None:
            stmt = stmt.where(
                or_(
                    Overlay.created_at < cursor_created_at,
                    and_(Overlay.created_at == cursor_created_at, Overlay.id < cursor_id),
                )
            )
        if status is not None:
            stmt = stmt.where(Overlay.status == status)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_fields(
        session: AsyncSession,
        overlay_id: str,
        *,
        document: dict[str, Any] | None = None,
        target_revision_id: uuid.UUID | None | _Unset = _UNSET,
    ) -> int:
        values: dict[str, Any] = {"updated_at": func.now()}
        if document is not None:
            values["document"] = document
        if not isinstance(target_revision_id, _Unset):
            values["target_revision_id"] = target_revision_id
        result = cast(
            "CursorResult[Any]",
            await session.execute(update(Overlay).where(Overlay.id == overlay_id).values(**values)),
        )
        await session.flush()
        return result.rowcount

    @staticmethod
    async def set_status(
        session: AsyncSession,
        overlay_id: str,
        status: OverlayStatus,
        *,
        confirmed_at: datetime | None = None,
        confirmed_by_execution_id: str | None = None,
        deprecated_at: datetime | None = None,
        expected_status: OverlayStatus | None = None,
    ) -> int:
        """Set an overlay's status, returning the number of rows updated.

        When ``expected_status`` is given the UPDATE is guarded by ``status =
        expected_status`` (a compare-and-swap), so a caller can detect that it lost a
        race (``rowcount == 0``) instead of blindly overwriting a concurrent
        transition (e.g. two confirms, or confirm vs deprecate).
        """
        values: dict[str, Any] = {"status": status, "updated_at": func.now()}
        if confirmed_at is not None:
            values["confirmed_at"] = confirmed_at
        if confirmed_by_execution_id is not None:
            values["confirmed_by_execution_id"] = confirmed_by_execution_id
        if deprecated_at is not None:
            values["deprecated_at"] = deprecated_at
        stmt = update(Overlay).where(Overlay.id == overlay_id)
        if expected_status is not None:
            stmt = stmt.where(Overlay.status == expected_status)
        result = cast(
            "CursorResult[Any]",
            await session.execute(stmt.values(**values)),
        )
        await session.flush()
        return result.rowcount

    @staticmethod
    async def set_confirmed_revision(
        session: AsyncSession,
        overlay_id: str,
        confirmed_revision_id: uuid.UUID,
    ) -> int:
        """Record the revision produced by materializing this overlay.

        Written by the ingest job after a confirm's re-ingest succeeds, so the
        overlay points at the concrete revision now serving the overlaid spec.
        """
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                update(Overlay)
                .where(Overlay.id == overlay_id)
                .values(confirmed_revision_id=confirmed_revision_id, updated_at=func.now())
            ),
        )
        await session.flush()
        return result.rowcount
