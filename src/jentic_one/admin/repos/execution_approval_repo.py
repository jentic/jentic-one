"""Repository for ExecutionApproval rows — flush-only, never commits."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.execution_approvals import ExecutionApproval

_PENDING = "pending"


class ExecutionApprovalRepository:
    """Data access for execution approvals."""

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        approval_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> ExecutionApproval | None:
        """The approval row, or None when missing or excluded by ``filters``."""
        stmt = select(ExecutionApproval).where(ExecutionApproval.id == approval_id)
        for f in filters or ():
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        state: str | None = None,
        agent_id: str | None = None,
        limit: int = 25,
        cursor_created_at: datetime | None = None,
        cursor_id: str | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[ExecutionApproval]:
        """Approvals newest first, keyset-paginated on ``(created_at, id)``."""
        stmt = select(ExecutionApproval)
        for f in filters or ():
            stmt = stmt.where(f)
        if state is not None:
            stmt = stmt.where(ExecutionApproval.state == state)
        if agent_id is not None:
            stmt = stmt.where(ExecutionApproval.agent_id == agent_id)
        if cursor_created_at is not None and cursor_id is not None:
            stmt = stmt.where(
                (ExecutionApproval.created_at < cursor_created_at)
                | (
                    (ExecutionApproval.created_at == cursor_created_at)
                    & (ExecutionApproval.id < cursor_id)
                )
            )
        stmt = stmt.order_by(ExecutionApproval.created_at.desc(), ExecutionApproval.id.desc())
        result = await session.execute(stmt.limit(limit))
        return list(result.scalars().all())

    @staticmethod
    async def decide(
        session: AsyncSession,
        approval_id: str,
        *,
        new_state: str,
        decided_by: str,
        decision_reason: str | None,
        now: datetime,
    ) -> ExecutionApproval | None:
        """Compare-and-set a pending, unexpired approval to ``new_state``.

        Returns the updated row, or None when the row is missing, already
        decided, or past ``expires_at`` — the first reviewer wins.
        """
        stmt = (
            update(ExecutionApproval)
            .where(
                ExecutionApproval.id == approval_id,
                ExecutionApproval.state == _PENDING,
                ExecutionApproval.expires_at > now,
            )
            .values(
                state=new_state,
                decided_at=now,
                decided_by=decided_by,
                decision_reason=decision_reason,
            )
            .returning(ExecutionApproval)
            .execution_options(synchronize_session=False)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def withdraw_by_job_id(session: AsyncSession, job_id: str, *, now: datetime) -> bool:
        """Move the job's pending approval to ``withdrawn``; True when one was pending."""
        stmt = (
            update(ExecutionApproval)
            .where(ExecutionApproval.job_id == job_id, ExecutionApproval.state == _PENDING)
            .values(state="withdrawn", decided_at=now)
            .returning(ExecutionApproval.id)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None
