"""Repository for Agent CRUD."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.users import User
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
    async def get_status_by_ids(session: AsyncSession, agent_ids: Sequence[str]) -> dict[str, str]:
        """Batch-fetch ``{agent_id: status}`` for the given ids.

        One query for a page of rows that need agent-status annotation
        (#1233: the grant listings surface the bound agent's lifecycle state
        so a dormant grant on a disabled agent is never mistaken for a
        working connection). Missing ids are simply absent from the result.
        """
        if not agent_ids:
            return {}
        stmt = select(Agent.id, Agent.status).where(Agent.id.in_(agent_ids))
        result = await session.execute(stmt)
        return {row[0]: row[1] for row in result.all()}

    @staticmethod
    async def list_ids_by_owner(session: AsyncSession, owner_id: str) -> list[str]:
        """Ids of every agent ``owner_id`` owns, whatever the agent's status.

        An owned agent's effective status follows its owner's account, so a
        change to the owner's account changes what the broker must answer for
        each of these agents.

        Unscoped: for internal fan-out (such as broker cache eviction) after
        the caller has authorized access to ``owner_id``. Never use it to build
        a response payload.
        """
        stmt = select(Agent.id).where(Agent.owner_id == owner_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())

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
        # Lock the row: approval sets owner_id only when still unowned, and a
        # concurrent `:claim` (which locks + commits owner_id) can interleave
        # under READ COMMITTED. Taking the same row lock serializes the two, so
        # the `owner_id is None` check below sees a just-committed claim and does
        # not overwrite the claimant — the exact "operator silently becomes
        # owner" outcome the claim flow exists to prevent.
        stmt = select(Agent).where(Agent.id == agent_id).with_for_update()
        agent = (await session.execute(stmt)).scalar_one_or_none()
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
    async def delete(session: AsyncSession, agent_id: str) -> int:
        """Hard-delete the agent row; returns the number of rows removed (0 or 1).

        ``agent_credentials`` go with it (``ON DELETE CASCADE``) and any child
        agent's ``parent_agent_id`` is nulled (``ON DELETE SET NULL``). Every
        other agent-keyed row carries a plain id with no FK, so the caller
        sweeps those explicitly.
        """
        result = await session.execute(delete(Agent).where(Agent.id == agent_id))
        await session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]

    @staticmethod
    async def create_dcr(
        session: AsyncSession,
        *,
        name: str,
        jwks: dict[str, Any],
        rat_hash: str,
        rat_expires_at: datetime,
        claim_token_hash: str | None = None,
        claim_expires_at: datetime | None = None,
    ) -> Agent:
        agent = Agent(
            name=name,
            owner_id=None,
            registered_by="self",
            jwks=jwks,
            registration_access_token_hash=rat_hash,
            rat_expires_at=rat_expires_at,
            claim_token_hash=claim_token_hash,
            claim_expires_at=claim_expires_at,
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

    @staticmethod
    async def get_with_owner_active(
        session: AsyncSession, agent_id: str, *, for_update: bool = False
    ) -> tuple[Agent, bool] | None:
        """The agent and whether its owner may currently act, in one statement.

        The owner flag is:

        - ``True`` for an ownerless agent;
        - ``users.active and users.deleted_at is None`` for an owned agent (a
          deleted row is refused even if ``active`` still reads true);
        - ``False`` when the owner row is missing, so callers that gate on the
          flag fail closed.

        ``None`` means the agent row itself is missing. ``for_update`` locks
        only the agent row, never the owner's.
        """
        stmt = (
            select(Agent, User.active, User.deleted_at)
            .outerjoin(User, User.id == Agent.owner_id)
            .where(Agent.id == agent_id)
        )
        if for_update:
            stmt = stmt.with_for_update(of=Agent)
        row = (await session.execute(stmt)).one_or_none()
        if row is None:
            return None
        agent, owner_active, owner_deleted_at = row
        return agent, agent.owner_id is None or (bool(owner_active) and owner_deleted_at is None)

    @staticmethod
    async def set_owner_from_claim(session: AsyncSession, agent: Agent, *, owner_id: str) -> Agent:
        """Assign ``owner_id`` to a claimant and consume the claim token.

        Caller (service) is responsible for locking the row, verifying the token
        hash + expiry, and checking the agent is still unowned. This method just
        performs the mutation: set the owner and null the single-use claim token
        so it cannot be replayed.
        """
        agent.owner_id = owner_id
        agent.claim_token_hash = None
        agent.claim_expires_at = None
        await session.flush()
        return agent
