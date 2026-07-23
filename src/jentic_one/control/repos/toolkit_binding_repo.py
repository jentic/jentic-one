"""Repository for toolkit credential binding operations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding


class ToolkitBindingRepository:
    """Data access layer for ToolkitCredentialBinding entities — flush-only, never commits."""

    @staticmethod
    async def bind(
        session: AsyncSession,
        *,
        toolkit_id: str,
        credential_id: str,
        created_by: str,
    ) -> ToolkitCredentialBinding:
        binding = ToolkitCredentialBinding(
            toolkit_id=toolkit_id,
            credential_id=credential_id,
            created_by=created_by,
        )
        session.add(binding)
        await session.flush()
        stmt = (
            select(ToolkitCredentialBinding)
            .options(selectinload(ToolkitCredentialBinding.credential))
            .where(ToolkitCredentialBinding.id == binding.id)
        )
        result = await session.execute(stmt)
        return result.scalar_one()

    @staticmethod
    async def get(
        session: AsyncSession,
        toolkit_id: str,
        credential_id: str,
    ) -> ToolkitCredentialBinding | None:
        stmt = select(ToolkitCredentialBinding).where(
            ToolkitCredentialBinding.toolkit_id == toolkit_id,
            ToolkitCredentialBinding.credential_id == credential_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_toolkit(
        session: AsyncSession,
        toolkit_id: str,
        *,
        cursor: tuple[datetime, str] | None = None,
        limit: int = 50,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[ToolkitCredentialBinding]:
        """List bindings for a toolkit with keyset pagination (bound_at, credential_id)."""
        stmt = (
            select(ToolkitCredentialBinding)
            .options(selectinload(ToolkitCredentialBinding.credential))
            .where(ToolkitCredentialBinding.toolkit_id == toolkit_id)
            .order_by(
                ToolkitCredentialBinding.bound_at.desc(),
                ToolkitCredentialBinding.credential_id.desc(),
            )
        )
        if cursor is not None:
            cursor_ts, cursor_id = cursor
            stmt = stmt.where(
                (ToolkitCredentialBinding.bound_at < cursor_ts)
                | (
                    (ToolkitCredentialBinding.bound_at == cursor_ts)
                    & (ToolkitCredentialBinding.credential_id < cursor_id)
                )
            )
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        stmt = stmt.limit(limit + 1)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_active_bound_credentials_for_api(
        session: AsyncSession,
        *,
        toolkit_id: str,
        api_vendor: str,
        api_name: str | None,
        api_version: str | None,
        exclude_credential_id: str | None = None,
    ) -> list[Credential]:
        """Return active credentials bound to a toolkit that match an API identity.

        Used to prevent binding a second active credential for the same API into
        one toolkit — a guaranteed-ambiguous state the broker resolver later
        refuses with ``409`` (issue #643). ``api_name`` / ``api_version`` match
        exactly when given; a ``None`` component matches any value, mirroring the
        resolver's identity matching.
        """
        stmt = (
            select(Credential)
            .join(
                ToolkitCredentialBinding,
                ToolkitCredentialBinding.credential_id == Credential.id,
            )
            .where(
                ToolkitCredentialBinding.toolkit_id == toolkit_id,
                Credential.active.is_(True),
                Credential.api_vendor == api_vendor,
            )
        )
        if api_name is not None:
            stmt = stmt.where(Credential.api_name == api_name)
        if api_version is not None:
            stmt = stmt.where(Credential.api_version == api_version)
        if exclude_credential_id is not None:
            stmt = stmt.where(Credential.id != exclude_credential_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def unbind(
        session: AsyncSession,
        toolkit_id: str,
        credential_id: str,
    ) -> bool:
        stmt = select(ToolkitCredentialBinding).where(
            ToolkitCredentialBinding.toolkit_id == toolkit_id,
            ToolkitCredentialBinding.credential_id == credential_id,
        )
        result = await session.execute(stmt)
        binding = result.scalar_one_or_none()
        if binding is None:
            return False
        await session.delete(binding)
        await session.flush()
        return True
