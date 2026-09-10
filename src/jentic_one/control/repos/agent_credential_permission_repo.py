"""Repository for AgentCredentialPermission CRUD operations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.agent_credential_permissions import (
    AgentCredentialPermission,
)


class AgentCredentialPermissionRepository:
    """Data access layer for AgentCredentialPermission — flush-only, never commits."""

    @staticmethod
    async def upsert(
        session: AsyncSession,
        *,
        agent_id: str,
        credential_id: str,
        rules: list[dict[str, str]],
        created_by: str | None = None,
    ) -> AgentCredentialPermission:
        """Create or replace the rule list for a (agent, credential) pair."""
        existing = await AgentCredentialPermissionRepository.get(
            session, agent_id=agent_id, credential_id=credential_id
        )
        if existing is not None:
            existing.rules = rules
            await session.flush()
            return existing
        row = AgentCredentialPermission(
            agent_id=agent_id,
            credential_id=credential_id,
            rules=rules,
            created_by=created_by,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get(
        session: AsyncSession, *, agent_id: str, credential_id: str
    ) -> AgentCredentialPermission | None:
        stmt = select(AgentCredentialPermission).where(
            AgentCredentialPermission.agent_id == agent_id,
            AgentCredentialPermission.credential_id == credential_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
