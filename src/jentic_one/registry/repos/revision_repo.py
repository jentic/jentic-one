"""Repository for ApiRevision entities."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.shared.models import ApiRevisionSourceType, ApiRevisionState


class ApiRevisionRepository:
    """Data access layer for ApiRevision entities — flush-only, never commits."""

    @staticmethod
    async def create_draft(
        session: AsyncSession,
        *,
        api_id: uuid.UUID,
        spec_digest: str,
        source_type: ApiRevisionSourceType,
        source_url: str | None = None,
        source_filename: str | None = None,
        source_content_id: uuid.UUID | None = None,
        submitted_by: str | None = None,
        created_by: str,
    ) -> ApiRevision:
        revision = ApiRevision(
            api_id=api_id,
            state=ApiRevisionState.DRAFT,
            spec_digest=spec_digest,
            source_type=source_type,
            source_url=source_url,
            source_filename=source_filename,
            source_content_id=source_content_id,
            submitted_by=submitted_by,
            created_by=created_by,
        )
        session.add(revision)
        await session.flush()
        return revision

    @staticmethod
    async def create_imported(
        session: AsyncSession,
        *,
        api_id: uuid.UUID,
        origin: str,
        spec_digest: str,
        source_type: ApiRevisionSourceType,
        source_url: str | None = None,
        source_filename: str | None = None,
        source_content_id: uuid.UUID | None = None,
        submitted_by: str | None = None,
        created_by: str,
    ) -> ApiRevision:
        revision = ApiRevision(
            api_id=api_id,
            state=ApiRevisionState.IMPORTED,
            origin=origin,
            spec_digest=spec_digest,
            source_type=source_type,
            source_url=source_url,
            source_filename=source_filename,
            source_content_id=source_content_id,
            submitted_by=submitted_by,
            promoted_at=datetime.now(UTC),
            created_by=created_by,
        )
        session.add(revision)
        await session.flush()
        return revision

    @staticmethod
    async def archive_active_imported(
        session: AsyncSession,
        api_id: uuid.UUID,
        origin: str,
    ) -> None:
        """Archive any existing IMPORTED revision for an API + origin pair."""
        stmt = (
            select(ApiRevision)
            .where(
                ApiRevision.api_id == api_id,
                ApiRevision.state == ApiRevisionState.IMPORTED,
                ApiRevision.origin == origin,
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.state = ApiRevisionState.ARCHIVED
            existing.archived_at = datetime.now(UTC)
            await session.flush()

    @staticmethod
    async def archive_all_active_imported(
        session: AsyncSession,
        api_id: uuid.UUID,
    ) -> None:
        """Archive all IMPORTED revisions for an API regardless of origin."""
        now = datetime.now(UTC)
        await session.execute(
            update(ApiRevision)
            .where(
                ApiRevision.api_id == api_id,
                ApiRevision.state == ApiRevisionState.IMPORTED,
            )
            .values(state=ApiRevisionState.ARCHIVED, archived_at=now)
        )
        await session.flush()

    @staticmethod
    async def get_by_digest(
        session: AsyncSession, api_id: uuid.UUID, spec_digest: str
    ) -> ApiRevision | None:
        result = await session.execute(
            select(ApiRevision).where(
                ApiRevision.api_id == api_id, ApiRevision.spec_digest == spec_digest
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def reactivate_imported(
        session: AsyncSession,
        revision: ApiRevision,
        *,
        origin: str,
    ) -> ApiRevision:
        """Re-mark an existing revision as the active one for re-import.

        Re-importing an unchanged spec produces the same ``spec_digest``. Rather
        than inserting a duplicate (which violates the ``(api_id, spec_digest)``
        unique constraint and fails the job), we reuse the existing revision. The
        caller archives any *other* active imported revision first, so exactly one
        stays active. Makes re-import idempotent.

        A revision that a human already **published** is left published — a routine
        re-import of the same spec must not silently demote that promotion. An
        archived/imported revision is (re)activated to IMPORTED. Origin is
        refreshed and any archived marker cleared either way.
        """
        if revision.state != ApiRevisionState.PUBLISHED:
            revision.state = ApiRevisionState.IMPORTED
            revision.promoted_at = datetime.now(UTC)
        revision.origin = origin
        revision.archived_at = None
        await session.flush()
        return revision

    @staticmethod
    async def set_operation_count(
        session: AsyncSession, revision_id: uuid.UUID, count: int
    ) -> None:
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                update(ApiRevision)
                .where(ApiRevision.id == revision_id)
                .values(operation_count=count)
            ),
        )
        if result.rowcount == 0:
            msg = f"ApiRevision {revision_id} not found"
            raise ValueError(msg)
        await session.flush()

    @staticmethod
    async def list_page(
        session: AsyncSession,
        *,
        api_id: uuid.UUID,
        limit: int = 50,
        cursor_created_at: datetime | None = None,
        cursor_id: str | None = None,
        states: list[str] | None = None,
    ) -> list[ApiRevision]:
        stmt = (
            select(ApiRevision)
            .where(ApiRevision.api_id == api_id)
            .order_by(ApiRevision.created_at.desc(), ApiRevision.id.desc())
            .limit(limit)
        )
        if cursor_created_at is not None and cursor_id is not None:
            stmt = stmt.where(
                or_(
                    ApiRevision.created_at < cursor_created_at,
                    and_(
                        ApiRevision.created_at == cursor_created_at,
                        ApiRevision.id < cursor_id,
                    ),
                )
            )
        if states:
            stmt = stmt.where(ApiRevision.state.in_(states))
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_for_api(
        session: AsyncSession, api_id: uuid.UUID, revision_id: uuid.UUID
    ) -> ApiRevision | None:
        stmt = (
            select(ApiRevision)
            .where(ApiRevision.api_id == api_id, ApiRevision.id == revision_id)
            .options(selectinload(ApiRevision.servers))
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def set_state(
        session: AsyncSession,
        revision_id: uuid.UUID,
        state: ApiRevisionState,
        *,
        promoted_at: datetime | None = None,
        archived_at: datetime | None = None,
    ) -> int:
        values: dict[str, Any] = {"state": state}
        if promoted_at is not None:
            values["promoted_at"] = promoted_at
        if archived_at is not None:
            values["archived_at"] = archived_at
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                update(ApiRevision).where(ApiRevision.id == revision_id).values(**values)
            ),
        )
        await session.flush()
        return result.rowcount

    @staticmethod
    async def delete(session: AsyncSession, revision_id: uuid.UUID) -> int:
        result = cast(
            "CursorResult[Any]",
            await session.execute(delete(ApiRevision).where(ApiRevision.id == revision_id)),
        )
        await session.flush()
        return result.rowcount
