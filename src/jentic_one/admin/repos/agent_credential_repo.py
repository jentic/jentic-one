"""Repository for AgentCredential CRUD."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.agent_credentials import AgentCredential


class AgentCredentialRepository:
    """Data access layer for AgentCredential entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        agent_id: str,
        client_secret_hash: str | None = None,
        api_key_hash: str | None = None,
        created_by: str,
    ) -> AgentCredential:
        cred = AgentCredential(
            agent_id=agent_id,
            client_secret_hash=client_secret_hash,
            api_key_hash=api_key_hash,
            created_by=created_by,
        )
        session.add(cred)
        await session.flush()
        return cred

    @staticmethod
    async def get_by_agent_id(session: AsyncSession, agent_id: str) -> AgentCredential | None:
        stmt = select(AgentCredential).where(AgentCredential.agent_id == agent_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def set_client_secret_hash(
        session: AsyncSession,
        agent_id: str,
        *,
        client_secret_hash: str,
        created_by: str,
    ) -> AgentCredential:
        stmt = select(AgentCredential).where(AgentCredential.agent_id == agent_id)
        result = await session.execute(stmt)
        cred = result.scalar_one_or_none()
        if cred is None:
            cred = AgentCredential(
                agent_id=agent_id,
                client_secret_hash=client_secret_hash,
                created_by=created_by,
            )
            session.add(cred)
        else:
            cred.client_secret_hash = client_secret_hash
            cred.rotated_at = datetime.now(UTC)
        await session.flush()
        return cred

    @staticmethod
    async def set_api_key_hash(
        session: AsyncSession,
        agent_id: str,
        *,
        api_key_hash: str,
        created_by: str,
    ) -> AgentCredential:
        stmt = select(AgentCredential).where(AgentCredential.agent_id == agent_id)
        result = await session.execute(stmt)
        cred = result.scalar_one_or_none()
        if cred is None:
            cred = AgentCredential(
                agent_id=agent_id,
                api_key_hash=api_key_hash,
                created_by=created_by,
            )
            session.add(cred)
        else:
            cred.api_key_hash = api_key_hash
            cred.rotated_at = datetime.now(UTC)
        await session.flush()
        return cred

    @staticmethod
    async def clear_api_key_hash(session: AsyncSession, agent_id: str) -> bool:
        """Nullify the API key hash for an agent. Returns True if a key was revoked."""
        stmt = select(AgentCredential).where(AgentCredential.agent_id == agent_id)
        result = await session.execute(stmt)
        cred = result.scalar_one_or_none()
        if cred is None or cred.api_key_hash is None:
            return False
        cred.api_key_hash = None
        cred.rotated_at = datetime.now(UTC)
        await session.flush()
        return True

    @staticmethod
    async def has_api_key(session: AsyncSession, agent_id: str) -> bool:
        """Check whether the agent has an active API key."""
        stmt = select(AgentCredential).where(AgentCredential.agent_id == agent_id)
        result = await session.execute(stmt)
        cred = result.scalar_one_or_none()
        return cred is not None and cred.api_key_hash is not None

    @staticmethod
    async def agent_ids_with_api_key(session: AsyncSession, agent_ids: Iterable[str]) -> set[str]:
        """Return the subset of ``agent_ids`` that have an active API key.

        The batch form of :meth:`has_api_key`: one query for a whole page of
        agents. An empty input returns an empty set without querying.
        """
        ids = list(dict.fromkeys(agent_ids))
        if not ids:
            return set()
        stmt = (
            select(AgentCredential.agent_id)
            .where(
                AgentCredential.agent_id.in_(ids),
                AgentCredential.api_key_hash.is_not(None),
            )
            .distinct()
        )
        result = await session.execute(stmt)
        return set(result.scalars().all())
