"""Repository for Credential CRUD operations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.shared.models.api_identity import slugify_api_field


class CredentialRepository:
    """Data access layer for Credential entities — flush-only, never commits."""

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        credential_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> Credential | None:
        if filters is None:
            return await session.get(Credential, credential_id)
        stmt = select(Credential).where(Credential.id == credential_id)
        for f in filters:
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        type: str,
        name: str,
        api_vendor: str,
        created_by: str,
        description: str | None = None,
        api_name: str | None = None,
        api_version: str | None = None,
        provider: str = "static",
        provider_account_ref: str | None = None,
        server_variables: dict[str, str] | None = None,
    ) -> Credential:
        credential = Credential(
            type=type,
            name=name,
            api_vendor=api_vendor,
            created_by=created_by,
            description=description,
            api_name=api_name,
            api_version=api_version,
            provider=provider,
            provider_account_ref=provider_account_ref,
            server_variables=server_variables,
        )
        session.add(credential)
        await session.flush()
        return credential

    @staticmethod
    async def list_by_vendor(session: AsyncSession, api_vendor: str) -> list[Credential]:
        """Fetch candidates for a vendor.

        Widen the prefilter to also match the caller's raw (un-slugified) input so
        historical rows persisted before vendor normalization still surface as
        candidates; the final identity match is normalized in the resolver.
        """
        normalized = slugify_api_field(api_vendor)
        result = await session.execute(
            select(Credential).where(Credential.api_vendor.in_({api_vendor, normalized}))
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        cursor: tuple[datetime, str] | None = None,
        limit: int = 50,
        vendor: str | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[Credential]:
        """List credentials with keyset pagination (created_at, id)."""
        stmt = select(Credential).order_by(Credential.created_at.desc(), Credential.id.desc())
        if vendor is not None:
            stmt = stmt.where(Credential.api_vendor == vendor)
        if cursor is not None:
            cursor_ts, cursor_id = cursor
            stmt = stmt.where(
                (Credential.created_at < cursor_ts)
                | ((Credential.created_at == cursor_ts) & (Credential.id < cursor_id))
            )
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        stmt = stmt.limit(limit + 1)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_header(
        session: AsyncSession,
        credential_id: str,
        *,
        name: str | None = None,
        provider: str | None = None,
        provider_account_ref: str | None = None,
        active: bool | None = None,
        server_variables: dict[str, str] | None = None,
    ) -> Credential | None:
        credential = await session.get(Credential, credential_id)
        if credential is None:
            return None
        if name is not None:
            credential.name = name
        if provider is not None:
            credential.provider = provider
        if provider_account_ref is not None:
            credential.provider_account_ref = provider_account_ref
        if active is not None:
            credential.active = active
        if server_variables is not None:
            credential.server_variables = server_variables
        await session.flush()
        return credential

    @staticmethod
    async def get_names_by_ids(session: AsyncSession, ids: list[str]) -> dict[str, str]:
        """Batch-resolve credential IDs to their human-readable names."""
        if not ids:
            return {}
        stmt = select(Credential.id, Credential.name).where(Credential.id.in_(ids))
        result = await session.execute(stmt)
        return {row.id: row.name for row in result}

    @staticmethod
    async def delete(session: AsyncSession, credential_id: str) -> bool:
        credential = await session.get(Credential, credential_id)
        if credential is None:
            return False
        await session.delete(credential)
        await session.flush()
        return True
