"""Repository for AgentCredentialBinding CRUD."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.shared.db.locks import lock_agent_bindings, lock_credential_bindings


class AgentCredentialBindingRepository:
    """Data access layer for AgentCredentialBinding entities — flush-only, never commits."""

    @staticmethod
    async def bind(
        session: AsyncSession,
        *,
        agent_id: str,
        credential_id: str,
        created_by: str,
        expires_at: datetime | None = None,
        max_idle_period: int | None = None,
        idle_warning: bool = False,
        max_quota: int | None = None,
        after_expiry: str = "hold",
    ) -> AgentCredentialBinding:
        await lock_agent_bindings(session, agent_id)
        await lock_credential_bindings(session, credential_id)
        binding = AgentCredentialBinding(
            agent_id=agent_id,
            credential_id=credential_id,
            created_by=created_by,
            expires_at=expires_at,
            max_idle_period=max_idle_period,
            idle_warning=idle_warning,
            max_quota=max_quota,
            quota_remaining=max_quota,
            after_expiry=after_expiry,
            # Granting starts the idle clock.
            renewed_at=datetime.now(UTC),
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
        """Flip the reversible cut-off flag; returns False when no binding exists.

        Resuming clears ``suspended_reason`` (it lifts whatever suspension was
        in place). Suspending leaves it alone: an active binding never carries
        a reason, so a manual suspension stays ``NULL``, and re-suspending a
        binding the registry already suspended keeps its ``api_deleted``
        reason instead of erasing why it stopped working.
        """
        values: dict[str, object] = {"suspended": suspended}
        if not suspended:
            values["suspended_reason"] = None
        stmt = (
            update(AgentCredentialBinding)
            .where(AgentCredentialBinding.agent_id == agent_id)
            .where(AgentCredentialBinding.credential_id == credential_id)
            .values(**values)
        )
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount) > 0  # type: ignore[attr-defined]

    @staticmethod
    async def set_expires_at(
        session: AsyncSession, *, agent_id: str, credential_id: str, expires_at: datetime | None
    ) -> bool:
        """Set, move or clear the fixed expiry; returns False when no binding exists."""
        stmt = (
            update(AgentCredentialBinding)
            .where(AgentCredentialBinding.agent_id == agent_id)
            .where(AgentCredentialBinding.credential_id == credential_id)
            .values(expires_at=expires_at)
        )
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount) > 0  # type: ignore[attr-defined]

    @staticmethod
    async def renew(
        session: AsyncSession,
        *,
        agent_id: str,
        credential_id: str,
        policy: dict[str, Any],
        now: datetime,
    ) -> bool:
        """Re-certify a binding: apply ``policy`` changes and restart its idle clock.

        ``policy`` holds only the policy columns the renewal sets (a key with
        ``None`` clears that limit; an absent key leaves it alone). Every
        renewal sets ``renewed_at = now`` and restores the use count to
        ``max_quota`` (the new one, when the renewal sets it). Returns False
        when no binding exists.
        """
        uses = policy.get("max_quota", AgentCredentialBinding.max_quota)
        stmt = (
            update(AgentCredentialBinding)
            .where(AgentCredentialBinding.agent_id == agent_id)
            .where(AgentCredentialBinding.credential_id == credential_id)
            # A renewal restarts the idle period and may move the end date,
            # so both may warn again.
            .values(
                **policy,
                renewed_at=now,
                idle_warned_at=None,
                expiry_warned_at=None,
                quota_remaining=uses,
            )
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
    async def list_idle_warned(
        session: AsyncSession,
        *,
        min_idle_period: int,
        agent_filters: Sequence[ColumnElement[bool]] = (),
    ) -> list[tuple[AgentCredentialBinding, str]]:
        """Active bindings with the warning on that may be due one, with their agent's name.

        Due one: an idle limit of at least ``min_idle_period``, or an end date.

        ``agent_filters`` scope the agents (``build_access_filters`` on
        ``Agent``); whether each one is due is the caller's check.
        """
        stmt = (
            select(AgentCredentialBinding, Agent.name)
            .join(Agent, Agent.id == AgentCredentialBinding.agent_id)
            .where(AgentCredentialBinding.idle_warning.is_(True))
            .where(AgentCredentialBinding.suspended.is_(False))
            .where(
                or_(
                    AgentCredentialBinding.max_idle_period >= min_idle_period,
                    AgentCredentialBinding.expires_at.is_not(None),
                )
            )
        )
        for agent_filter in agent_filters:
            stmt = stmt.where(agent_filter)
        result = await session.execute(stmt)
        return [(binding, name) for binding, name in result.all()]

    @staticmethod
    async def delete_for_agent(session: AsyncSession, agent_id: str) -> int:
        stmt = delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id == agent_id)
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]
