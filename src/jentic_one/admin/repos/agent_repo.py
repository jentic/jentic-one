"""Repository for Agent CRUD."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.services.errors import AgentNotFoundError
from jentic_one.shared.models import ActorStatus


class AgentRepository:
    """Data access layer for Agent entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        name: str,
        owner_id: str,
        registered_by: str,
        description: str | None = None,
        parent_agent_id: str | None = None,
        created_by: str,
        status: ActorStatus = ActorStatus.PENDING,
    ) -> Agent:
        agent = Agent(
            name=name,
            owner_id=owner_id,
            registered_by=registered_by,
            description=description,
            parent_agent_id=parent_agent_id,
            created_by=created_by,
            status=status,
        )
        session.add(agent)
        await session.flush()
        return agent

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        agent_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> Agent | None:
        if filters is None:
            return await session.get(Agent, agent_id)
        stmt = select(Agent).where(Agent.id == agent_id)
        for f in filters:
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_owner(
        session: AsyncSession,
        owner_id: str,
        *,
        limit: int = 50,
        cursor: datetime | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[Agent]:
        stmt = (
            select(Agent)
            .where(Agent.owner_id == owner_id)
            .order_by(Agent.created_at.desc())
            .limit(limit)
        )
        if cursor is not None:
            stmt = stmt.where(Agent.created_at < cursor)
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        limit: int = 50,
        cursor: datetime | None = None,
        status: str | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[Agent]:
        stmt = select(Agent).order_by(Agent.created_at.desc()).limit(limit)
        if cursor is not None:
            stmt = stmt.where(Agent.created_at < cursor)
        if status is not None:
            stmt = stmt.where(Agent.status == status)
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_status(session: AsyncSession, agent_id: str, status: ActorStatus) -> Agent:
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        agent.status = status
        await session.flush()
        return agent

    @staticmethod
    async def set_approval(session: AsyncSession, agent_id: str, *, approved_by: str) -> Agent:
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        agent.status = ActorStatus.ACTIVE
        agent.approved_by = approved_by
        agent.approved_at = datetime.now(UTC)
        if agent.owner_id is None:
            agent.owner_id = approved_by
        # Invalidate RAT in the same flush — approval and RAT clearing are one
        # logical operation per RFC 7592 (single-use credential).
        agent.registration_access_token_hash = None
        agent.rat_expires_at = None
        await session.flush()
        return agent

    @staticmethod
    async def set_denial(
        session: AsyncSession, agent_id: str, *, reason: str, denied_by: str
    ) -> Agent:
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        agent.status = ActorStatus.REJECTED
        agent.denial_reason = reason
        agent.denied_by = denied_by
        await session.flush()
        return agent

    @staticmethod
    async def archive(session: AsyncSession, agent_id: str) -> Agent:
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        agent.status = ActorStatus.ARCHIVED
        await session.flush()
        return agent

    @staticmethod
    async def create_dcr(
        session: AsyncSession,
        *,
        name: str,
        jwks: dict[str, Any],
        rat_hash: str,
        rat_expires_at: datetime,
    ) -> Agent:
        agent = Agent(
            name=name,
            owner_id=None,
            registered_by="self",
            jwks=jwks,
            registration_access_token_hash=rat_hash,
            rat_expires_at=rat_expires_at,
            created_by="self",
        )
        session.add(agent)
        await session.flush()
        return agent

    _UPDATABLE_FIELDS = frozenset({"name", "description", "owner_id"})

    @staticmethod
    async def update_agent(
        session: AsyncSession,
        agent_id: str,
        **kwargs: Any,
    ) -> Agent:
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        safe_fields = {k: v for k, v in kwargs.items() if k in AgentRepository._UPDATABLE_FIELDS}
        for key, value in safe_fields.items():
            setattr(agent, key, value)
        await session.flush()
        return agent

    @staticmethod
    async def get_by_id_for_update(session: AsyncSession, agent_id: str) -> Agent | None:
        stmt = select(Agent).where(Agent.id == agent_id).with_for_update()
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
