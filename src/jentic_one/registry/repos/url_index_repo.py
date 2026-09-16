"""Repository for OperationURLIndex entities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.url_index import URLIndexEntry
from jentic_one.shared.schemas import APIReference


@dataclass(frozen=True, slots=True)
class OperationContext:
    """A resolved operation's API identity plus its spec path template + method."""

    api: APIReference
    path: str
    method: str


class UrlIndexRepository:
    """Data access layer for OperationURLIndex entities — flush-only, never commits."""

    @staticmethod
    async def delete_for_revision(session: AsyncSession, revision_id: uuid.UUID) -> None:
        await session.execute(
            delete(OperationURLIndex).where(OperationURLIndex.revision_id == revision_id)
        )
        await session.flush()

    @staticmethod
    async def get_operation_context(
        session: AsyncSession, operation_id: str
    ) -> OperationContext | None:
        """Return the operation's API identity + path/method, or ``None`` if unknown.

        The API ``name`` falls back to the API's ``display_name`` when set,
        mirroring the Registry inspect service's API-identity derivation. Joins
        operations → api_revisions → apis; the operation's own ``path`` +
        ``method`` ride along so the resolver returns the human-readable
        operation identity in the same single query.
        """
        stmt = (
            select(Api.vendor, Api.display_name, Api.name, Api.version)
            .add_columns(Operation.path, Operation.method)
            .select_from(Operation)
            .join(ApiRevision, ApiRevision.id == Operation.revision_id)
            .join(Api, Api.id == ApiRevision.api_id)
            .where(Operation.id == operation_id)
        )
        row = (await session.execute(stmt)).one_or_none()
        if row is None:
            return None
        vendor, display_name, name, version, path, method = row
        return OperationContext(
            api=APIReference(vendor=vendor, name=display_name or name, version=version),
            path=path,
            method=method,
        )

    @staticmethod
    async def upsert_entry(
        session: AsyncSession,
        *,
        revision_id: uuid.UUID,
        operation_id: str,
        method: str,
        entry: URLIndexEntry,
        created_by: str,
    ) -> None:
        stmt = insert(OperationURLIndex).values(
            operation_id=operation_id,
            revision_id=revision_id,
            method=method,
            host=entry.host_pattern,
            host_regex=entry.host_regex.pattern,
            path_template=entry.path_pattern,
            path_regex=entry.path_regex.pattern,
            param_names=entry.param_names,
            segment_count=entry.segment_count,
            created_by=created_by,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_operation_url_index_lookup",
            set_={
                "operation_id": stmt.excluded.operation_id,
                "revision_id": stmt.excluded.revision_id,
                "host_regex": stmt.excluded.host_regex,
                "path_regex": stmt.excluded.path_regex,
                "param_names": stmt.excluded.param_names,
                "segment_count": stmt.excluded.segment_count,
            },
        )
        await session.execute(stmt)
        await session.flush()

    @staticmethod
    async def lookup_by_host(
        session: AsyncSession,
        *,
        revision_id: uuid.UUID,
        method: str,
        host: str,
        segment_count: int,
    ) -> list[OperationURLIndex]:
        """Find URL index entries matching exact host, method, and segment count."""
        stmt = select(OperationURLIndex).where(
            and_(
                OperationURLIndex.revision_id == revision_id,
                OperationURLIndex.method == method,
                OperationURLIndex.host == host,
                OperationURLIndex.segment_count == segment_count,
            )
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def lookup_by_host_any_revision(
        session: AsyncSession,
        *,
        method: str,
        host: str,
        segment_count: int,
    ) -> list[OperationURLIndex]:
        """Find URL index entries matching host, method, and segment count across all revisions."""
        stmt = select(OperationURLIndex).where(
            and_(
                OperationURLIndex.method == method,
                OperationURLIndex.host == host,
                OperationURLIndex.segment_count == segment_count,
            )
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def lookup_by_host_any_method(
        session: AsyncSession,
        *,
        revision_id: uuid.UUID,
        host: str,
        segment_count: int,
    ) -> list[OperationURLIndex]:
        """Find URL index entries matching host and segment count (any method)."""
        stmt = select(OperationURLIndex).where(
            and_(
                OperationURLIndex.revision_id == revision_id,
                OperationURLIndex.host == host,
                OperationURLIndex.segment_count == segment_count,
            )
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def lookup_by_host_regex(
        session: AsyncSession,
        *,
        revision_id: uuid.UUID,
        method: str,
        segment_count: int,
    ) -> list[OperationURLIndex]:
        """Find regex-host entries matching method and segment count."""
        stmt = select(OperationURLIndex).where(
            and_(
                OperationURLIndex.revision_id == revision_id,
                OperationURLIndex.method == method,
                OperationURLIndex.host.is_(None),
                OperationURLIndex.host_regex.isnot(None),
                OperationURLIndex.segment_count == segment_count,
            )
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def lookup_by_host_regex_any_revision(
        session: AsyncSession,
        *,
        method: str,
        segment_count: int,
    ) -> list[OperationURLIndex]:
        """Find regex-host entries matching method and segment count across all revisions."""
        stmt = select(OperationURLIndex).where(
            and_(
                OperationURLIndex.method == method,
                OperationURLIndex.host.is_(None),
                OperationURLIndex.host_regex.isnot(None),
                OperationURLIndex.segment_count == segment_count,
            )
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def lookup_by_host_regex_any_method(
        session: AsyncSession,
        *,
        revision_id: uuid.UUID,
        segment_count: int,
    ) -> list[OperationURLIndex]:
        """Find regex-host entries matching segment count (any method)."""
        stmt = select(OperationURLIndex).where(
            and_(
                OperationURLIndex.revision_id == revision_id,
                OperationURLIndex.host.is_(None),
                OperationURLIndex.host_regex.isnot(None),
                OperationURLIndex.segment_count == segment_count,
            )
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
