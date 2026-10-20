"""Repository for CliInvocation rows (vendor CLI invocation tokens)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.cli_invocations import CliInvocation


class CliInvocationRepository:
    """Data access layer for CliInvocation entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        token_hash: str,
        parent_token_hash: str,
        actor_id: str,
        actor_type: str,
        vendor: str,
        credential_name: str | None,
        trace_id: str,
        command_title: str | None,
        expires_at: datetime,
        hard_expires_at: datetime,
    ) -> CliInvocation:
        row = CliInvocation(
            token_hash=token_hash,
            parent_token_hash=parent_token_hash,
            actor_id=actor_id,
            actor_type=actor_type,
            vendor=vendor,
            credential_name=credential_name,
            trace_id=trace_id,
            command_title=command_title,
            expires_at=expires_at,
            hard_expires_at=hard_expires_at,
            created_by=actor_id,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get_for_actor_for_update(
        session: AsyncSession, invocation_id: str, *, actor_id: str
    ) -> CliInvocation | None:
        """Lock one actor's invocation row, or ``None`` if it is not theirs (or absent)."""
        stmt = (
            select(CliInvocation)
            .where(CliInvocation.id == invocation_id, CliInvocation.actor_id == actor_id)
            .with_for_update()
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def set_expires_at(
        session: AsyncSession, row: CliInvocation, expires_at: datetime
    ) -> CliInvocation:
        row.expires_at = expires_at
        await session.flush()
        return row

    @staticmethod
    async def revoke(session: AsyncSession, row: CliInvocation, revoked_at: datetime) -> None:
        row.revoked_at = revoked_at
        await session.flush()

    @staticmethod
    async def delete_for_actor(session: AsyncSession, actor_id: str) -> int:
        """Delete every invocation row an actor minted (the agent purge). Returns the count."""
        result = await session.execute(
            delete(CliInvocation).where(CliInvocation.actor_id == actor_id)
        )
        await session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]
