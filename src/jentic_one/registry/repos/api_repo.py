"""Repository for Api aggregate root CRUD operations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast
from urllib.parse import urlparse

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.security_schemes import SecurityScheme
from jentic_one.registry.core.schema.servers import Server


class ApiRepository:
    """Data access layer for Api entities — flush-only, never commits."""

    @staticmethod
    async def get_by_id(session: AsyncSession, api_id: uuid.UUID) -> Api | None:
        return await session.get(Api, api_id)

    @staticmethod
    async def get_by_identifier(
        session: AsyncSession, vendor: str, name: str, version: str
    ) -> Api | None:
        result = await session.execute(
            select(Api).where(Api.vendor == vendor, Api.name == name, Api.version == version)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_identifier_with_current_revision(
        session: AsyncSession, vendor: str, name: str, version: str
    ) -> Api | None:
        """Fetch an Api with its current revision's servers + security schemes eager-loaded.

        Avoids the post-hoc ``session.refresh`` round-trips the read path would
        otherwise incur to materialize the derived ``host`` / ``security_schemes``.
        """
        result = await session.execute(
            select(Api)
            .where(Api.vendor == vendor, Api.name == name, Api.version == version)
            .options(
                joinedload(Api.current_revision).selectinload(ApiRevision.security_schemes),
                joinedload(Api.current_revision).selectinload(ApiRevision.servers),
            )
            # Force eager-loaded relationships to overwrite any stale identity-map
            # state so a caller that mutated rows via bulk UPDATEs (e.g. promote)
            # never triggers an async lazy load on a cached instance. See #642.
            .execution_options(populate_existing=True)
        )
        return result.unique().scalar_one_or_none()

    @staticmethod
    async def list_by_vendor(session: AsyncSession, vendor: str) -> list[Api]:
        result = await session.execute(select(Api).where(Api.vendor == vendor))
        return list(result.scalars().all())

    @staticmethod
    async def resolve_ids(
        session: AsyncSession,
        *,
        vendor: str,
        name: str | None = None,
        version: str | None = None,
    ) -> list[uuid.UUID]:
        """Resolve a (possibly partial) ``vendor[/name[/version]]`` tuple to api ids."""
        stmt = select(Api.id).where(Api.vendor == vendor)
        if name is not None:
            stmt = stmt.where(Api.name == name)
        if version is not None:
            stmt = stmt.where(Api.version == version)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def upsert(
        session: AsyncSession,
        *,
        vendor: str,
        name: str,
        version: str,
        display_name: str | None = None,
        description: str | None = None,
        created_by: str,
    ) -> Api:
        result = await session.execute(
            select(Api).where(Api.vendor == vendor, Api.name == name, Api.version == version)
        )
        api = result.scalar_one_or_none()
        if api is not None:
            if display_name is not None:
                api.display_name = display_name
            if description is not None:
                api.description = description
        else:
            api = Api(
                vendor=vendor,
                name=name,
                version=version,
                display_name=display_name,
                description=description,
                created_by=created_by,
            )
            session.add(api)
        await session.flush()
        return api

    @staticmethod
    async def set_current_revision(
        session: AsyncSession, api_id: uuid.UUID, revision_id: uuid.UUID
    ) -> None:
        api = await session.get(Api, api_id)
        if api is None:
            msg = f"Api {api_id} not found"
            raise ValueError(msg)
        api.current_revision_id = revision_id
        await session.flush()

    @staticmethod
    async def clear_current_revision(session: AsyncSession, api_id: uuid.UUID) -> None:
        await session.execute(update(Api).where(Api.id == api_id).values(current_revision_id=None))
        await session.flush()

    @staticmethod
    async def apply_counts(
        session: AsyncSession,
        api_id: uuid.UUID,
        *,
        revision_count_delta: int = 0,
        operation_count: int | None = None,
    ) -> None:
        values: dict[str, object] = {
            "revision_count": func.coalesce(Api.revision_count, 0) + revision_count_delta,
        }
        if operation_count is not None:
            values["operation_count"] = operation_count
        result = cast(
            "CursorResult[Any]",
            await session.execute(update(Api).where(Api.id == api_id).values(**values)),
        )
        if result.rowcount == 0:
            msg = f"Api {api_id} not found"
            raise ValueError(msg)
        await session.flush()

    @staticmethod
    async def list_page(
        session: AsyncSession,
        *,
        limit: int = 50,
        cursor_created_at: datetime | None = None,
        cursor_id: str | None = None,
        vendor: str | None = None,
    ) -> list[Api]:
        stmt = select(Api).order_by(Api.created_at.desc(), Api.id.desc()).limit(limit)
        if cursor_created_at is not None and cursor_id is not None:
            stmt = stmt.where(
                or_(
                    Api.created_at < cursor_created_at,
                    and_(Api.created_at == cursor_created_at, Api.id < cursor_id),
                )
            )
        if vendor is not None:
            stmt = stmt.where(Api.vendor == vendor)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def load_security_scheme_types(
        session: AsyncSession, revision_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[str]]:
        if not revision_ids:
            return {}
        stmt = (
            select(SecurityScheme.revision_id, SecurityScheme.type)
            .where(SecurityScheme.revision_id.in_(revision_ids))
            .distinct()
        )
        result = await session.execute(stmt)
        mapping: dict[uuid.UUID, list[str]] = {}
        for row in result:
            mapping.setdefault(row.revision_id, []).append(row.type)
        return mapping

    @staticmethod
    async def load_server_hosts(
        session: AsyncSession, revision_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str | None]:
        if not revision_ids:
            return {}
        stmt = (
            select(Server.revision_id, Server.url)
            .where(Server.revision_id.in_(revision_ids), Server.operation_id.is_(None))
            .order_by(Server.revision_id, Server.created_at)
        )
        result = await session.execute(stmt)
        mapping: dict[uuid.UUID, str | None] = {}
        for row in result:
            if row.revision_id not in mapping:
                parsed = urlparse(row.url)
                mapping[row.revision_id] = parsed.hostname
        return mapping

    _UPDATABLE_FIELDS = frozenset({"display_name", "description", "icon_url"})

    @staticmethod
    async def update_presentation(
        session: AsyncSession, api_id: uuid.UUID, *, fields: dict[str, Any]
    ) -> int:
        safe_fields = {k: v for k, v in fields.items() if k in ApiRepository._UPDATABLE_FIELDS}
        if not safe_fields:
            return 0
        safe_fields["updated_at"] = func.now()
        result = cast(
            "CursorResult[Any]",
            await session.execute(update(Api).where(Api.id == api_id).values(**safe_fields)),
        )
        await session.flush()
        return result.rowcount

    @staticmethod
    async def delete(session: AsyncSession, api_id: uuid.UUID) -> int:
        await session.execute(update(Api).where(Api.id == api_id).values(current_revision_id=None))
        await session.flush()
        result = cast(
            "CursorResult[Any]",
            await session.execute(delete(Api).where(Api.id == api_id)),
        )
        await session.flush()
        return result.rowcount
