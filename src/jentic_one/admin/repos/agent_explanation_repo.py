"""Repository for AgentExplanation rows — flush-only, never commits."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.agent_explanations import AgentExplanation


class AgentExplanationRepository:
    """Data access for agents' explanations of their own records."""

    @staticmethod
    async def get_text(session: AsyncSession, subject_type: str, subject_id: str) -> str | None:
        """The explanation of one subject, or None when it has none."""
        result = await session.execute(
            select(AgentExplanation.text).where(
                AgentExplanation.subject_type == subject_type,
                AgentExplanation.subject_id == subject_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def texts_for(
        session: AsyncSession, subject_type: str, subject_ids: Sequence[str]
    ) -> dict[str, str]:
        """``subject_id`` → explanation for those of ``subject_ids`` that have one."""
        if not subject_ids:
            return {}
        result = await session.execute(
            select(AgentExplanation.subject_id, AgentExplanation.text).where(
                AgentExplanation.subject_type == subject_type,
                AgentExplanation.subject_id.in_(list(subject_ids)),
            )
        )
        return {row[0]: row[1] for row in result.all()}

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        subject_type: str,
        subject_id: str,
        agent_id: str,
        text: str,
    ) -> AgentExplanation | None:
        """Insert one explanation, or None when the subject already has one.

        The insert runs in a savepoint, so a lost race on the subject's unique
        index leaves the caller's transaction usable.
        """
        row = AgentExplanation(
            subject_type=subject_type,
            subject_id=subject_id,
            agent_id=agent_id,
            text=text,
            created_by=agent_id,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            return None
        return row
