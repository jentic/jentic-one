"""Repository for AgentCredentialBinding CRUD."""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding


class AgentCredentialBindingRepository:
    """Data access layer for AgentCredentialBinding entities — flush-only, never commits."""

    @staticmethod
    async def bind(
        session: AsyncSession,
        *,
        agent_id: str,
        credential_id: str,
        created_by: str,
    ) -> AgentCredentialBinding:
        binding = AgentCredentialBinding(
            agent_id=agent_id, credential_id=credential_id, created_by=created_by
        )
        session.add(binding)
        await session.flush()
        return binding

    @staticmethod
    async def get(
        session: AsyncSession, *, agent_id: str, credential_id: str
    ) -> AgentCredentialBinding | None:
        stmt = (
            select(AgentCredentialBinding)
            .where(AgentCredentialBinding.agent_id == agent_id)
            .where(AgentCredentialBinding.credential_id == credential_id)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def set_suspended(
        session: AsyncSession, *, agent_id: str, credential_id: str, suspended: bool
    ) -> bool:
        """Flip the reversible cut-off flag; returns False when no binding exists."""
        stmt = (
            update(AgentCredentialBinding)
            .where(AgentCredentialBinding.agent_id == agent_id)
            .where(AgentCredentialBinding.credential_id == credential_id)
            .values(suspended=suspended)
        )
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount) > 0  # type: ignore[attr-defined]

    @staticmethod
    async def purge(session: AsyncSession, *, agent_id: str, credential_id: str) -> bool:
        """Delete the binding row outright (explicit destructive unbind)."""
        stmt = (
            delete(AgentCredentialBinding)
            .where(AgentCredentialBinding.agent_id == agent_id)
            .where(AgentCredentialBinding.credential_id == credential_id)
        )
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount) > 0  # type: ignore[attr-defined]

    @staticmethod
    async def list_for_agent(session: AsyncSession, agent_id: str) -> list[AgentCredentialBinding]:
        stmt = (
            select(AgentCredentialBinding)
            .where(AgentCredentialBinding.agent_id == agent_id)
            .order_by(AgentCredentialBinding.bound_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def delete_for_agent(session: AsyncSession, agent_id: str) -> int:
        stmt = delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id == agent_id)
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]
