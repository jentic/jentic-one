"""Repository for ConnectSessionOutcome rows (append-only terminal records)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.connect_session_outcomes import ConnectSessionOutcome
from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.shared.db.ids import generate_ksuid


class ConnectSessionOutcomeRepository:
    """Data access layer for connect-session outcomes — flush-only, never commits."""

    @staticmethod
    async def record(
        session: AsyncSession,
        *,
        row: ConnectSession,
        outcome: str,
        error_code: str | None,
        ended_at: datetime,
        credential_id: str | None = None,
    ) -> bool:
        """Record how ``row`` ended; a second record for the same session is a no-op.

        Written in the caller's terminal-transition transaction. Insert-or-ignore
        on ``session_id`` so a racing finaliser can never fail that transaction
        over the outcome row. Returns True when this call wrote the row.
        """
        values = {
            "id": generate_ksuid("cso"),
            "created_by": row.initiator_actor_id,
            "session_id": row.id,
            "agent_id": row.agent_id,
            "target_kind": row.target_kind,
            "vendor": row.vendor,
            "api_name": row.api_name,
            "api_version": row.api_version,
            "resolved_flow": row.resolved_flow,
            "outcome": outcome,
            "error_code": error_code,
            "credential_id": credential_id,
            "poll_token_hash": row.poll_token_hash,
            "ended_at": ended_at,
            "created_at": ended_at,
        }
        insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
        stmt = (
            insert(ConnectSessionOutcome)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["session_id"])
        )
        result: CursorResult[tuple[()]] = await session.execute(stmt)  # type: ignore[assignment]
        await session.flush()
        return bool(result.rowcount)

    @staticmethod
    async def get_by_session_id(
        session: AsyncSession, session_id: str
    ) -> ConnectSessionOutcome | None:
        stmt = select(ConnectSessionOutcome).where(ConnectSessionOutcome.session_id == session_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def latest_ended_at(
        session: AsyncSession,
        *,
        agent_id: str,
        outcome: str,
        target_kind: str,
        vendor: str,
        api_name: str | None,
        api_version: str | None,
        since: datetime,
    ) -> datetime | None:
        """When the agent's most recent ``outcome`` for this target ended, if after ``since``."""
        stmt = select(func.max(ConnectSessionOutcome.ended_at)).where(
            ConnectSessionOutcome.agent_id == agent_id,
            ConnectSessionOutcome.outcome == outcome,
            ConnectSessionOutcome.target_kind == target_kind,
            ConnectSessionOutcome.vendor == vendor,
            ConnectSessionOutcome.ended_at >= since,
        )
        stmt = stmt.where(
            ConnectSessionOutcome.api_name.is_(None)
            if api_name is None
            else ConnectSessionOutcome.api_name == api_name
        )
        stmt = stmt.where(
            ConnectSessionOutcome.api_version.is_(None)
            if api_version is None
            else ConnectSessionOutcome.api_version == api_version
        )
        result = await session.execute(stmt)
        value: datetime | None = result.scalar_one_or_none()
        return value

    @staticmethod
    async def delete_ended_before(
        session: AsyncSession, *, older_than: datetime, limit: int
    ) -> int:
        """Delete up to ``limit`` outcomes that ended before ``older_than`` (oldest first)."""
        oldest = (
            select(ConnectSessionOutcome.id)
            .where(ConnectSessionOutcome.ended_at < older_than)
            .order_by(ConnectSessionOutcome.ended_at.asc())
            .limit(limit)
            .scalar_subquery()
        )
        stmt = delete(ConnectSessionOutcome).where(ConnectSessionOutcome.id.in_(oldest))
        result: CursorResult[tuple[()]] = await session.execute(stmt)  # type: ignore[assignment]
        await session.flush()
        return int(result.rowcount or 0)
