"""Repository for ExecutionApproval CRUD."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.execution_approvals import ExecutionApproval


class ExecutionApprovalRepository:
    """Data access layer for ExecutionApproval entities — flush-only, never commits."""

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        approval_id: str,
        filters: list[ColumnElement[Any]] | None = None,
    ) -> ExecutionApproval | None:
        """Return the approval row or None.  ``filters`` are AND-appended for scoping."""
        if not filters:
            return await session.get(ExecutionApproval, approval_id)
        stmt = select(ExecutionApproval).where(ExecutionApproval.id == approval_id)
        for f in filters:
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_state(
        session: AsyncSession,
        *,
        state: str | None = None,
        agent_id: str | None = None,
        limit: int = 25,
        cursor_created_at: datetime | None = None,
        cursor_id: str | None = None,
        extra_filters: list[ColumnElement[Any]] | None = None,
    ) -> list[ExecutionApproval]:
        """List approvals ordered by created_at desc, with optional state/agent filter."""
        stmt = select(ExecutionApproval)
        if extra_filters:
            for f in extra_filters:
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
        stmt = stmt.order_by(
            ExecutionApproval.created_at.desc(), ExecutionApproval.id.desc()
        ).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def count_pending_by_agent(session: AsyncSession, agent_id: str) -> int:
        """Return the number of pending approval rows for the given agent."""
        stmt = (
            select(func.count())
            .select_from(ExecutionApproval)
            .where(
                ExecutionApproval.agent_id == agent_id,
                ExecutionApproval.state == "pending",
            )
        )
        result = await session.execute(stmt)
        return int(result.scalar_one() or 0)

    @staticmethod
    async def get_pending_by_fingerprint(
        session: AsyncSession, fingerprint: str
    ) -> ExecutionApproval | None:
        """Return a pending approval row with the given request fingerprint, or None."""
        stmt = select(ExecutionApproval).where(
            ExecutionApproval.request_fingerprint == fingerprint,
            ExecutionApproval.state == "pending",
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_approved_by_job_id(
        session: AsyncSession, job_id: str
    ) -> ExecutionApproval | None:
        """Return the approved execution_approvals row for a job, or None."""
        stmt = select(ExecutionApproval).where(
            ExecutionApproval.job_id == job_id,
            ExecutionApproval.state == "approved",
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def decide(
        session: AsyncSession,
        approval_id: str,
        *,
        new_state: str,
        decided_by: str,
        decision_reason: str | None,
    ) -> ExecutionApproval | None:
        """Transition a pending approval to approved or denied; returns the updated row."""
        now = datetime.now(UTC)
        stmt = (
            update(ExecutionApproval)
            .where(ExecutionApproval.id == approval_id, ExecutionApproval.state == "pending")
            .values(
                state=new_state,
                decided_at=now,
                decided_by=decided_by,
                decision_reason=decision_reason,
            )
            .returning(ExecutionApproval)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def set_execution_id(session: AsyncSession, approval_id: str, execution_id: str) -> None:
        """Back-fill execution_id once the approved job completes."""
        stmt = (
            update(ExecutionApproval)
            .where(ExecutionApproval.id == approval_id)
            .values(execution_id=execution_id)
        )
        await session.execute(stmt)

    @staticmethod
    async def withdraw_by_job_id(session: AsyncSession, job_id: str) -> bool:
        """Transition a pending approval for the given job to withdrawn.

        Returns True when a pending row was found and updated, False otherwise.
        """
        now = datetime.now(UTC)
        stmt = (
            update(ExecutionApproval)
            .where(
                ExecutionApproval.job_id == job_id,
                ExecutionApproval.state == "pending",
            )
            .values(state="withdrawn", decided_at=now)
        )
        result = await session.execute(stmt)
        return int(result.rowcount) > 0  # type: ignore[attr-defined]

    @staticmethod
    async def expire_batch(session: AsyncSession, ids: Sequence[str], decided_at: datetime) -> int:
        """Mark a batch of pending approvals as expired. Returns the count."""
        if not ids:
            return 0
        stmt = (
            update(ExecutionApproval)
            .where(ExecutionApproval.id.in_(ids), ExecutionApproval.state == "pending")
            .values(state="expired", decided_at=decided_at)
        )
        result = await session.execute(stmt)
        return int(result.rowcount)  # type: ignore[attr-defined]
