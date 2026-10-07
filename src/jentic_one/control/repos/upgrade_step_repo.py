"""Repository for the post-migration upgrade-step ledger (control DB).

One row per completed step in ``upgrade_steps`` (for a repeatable step, its
latest completed run); the cross-process run lock serialising the steps lives
in ``control/services/run_lock.py``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.upgrade_steps import UpgradeStep


class UpgradeStepRepository:
    """Control-DB ledger reads/writes — flush-only, never commits."""

    @staticmethod
    async def get(session: AsyncSession, name: str) -> UpgradeStep | None:
        result = await session.execute(select(UpgradeStep).where(UpgradeStep.name == name))
        return result.scalar_one_or_none()

    @staticmethod
    async def list_names(session: AsyncSession) -> set[str]:
        """Names of every step the ledger records."""
        result = await session.execute(select(UpgradeStep.name))
        return {str(name) for name in result.scalars().all()}

    @staticmethod
    async def record(
        session: AsyncSession,
        *,
        name: str,
        tool_version: str,
        summary: dict[str, Any],
        created_by: str,
    ) -> UpgradeStep:
        """Insert the completion row; the unique name rejects a second recorder."""
        step = UpgradeStep(
            name=name, tool_version=tool_version, summary=summary, created_by=created_by
        )
        session.add(step)
        await session.flush()
        return step

    @staticmethod
    async def record_run(
        session: AsyncSession,
        *,
        name: str,
        tool_version: str,
        summary: dict[str, Any],
        created_by: str,
    ) -> UpgradeStep:
        """Record a repeatable step's latest run: insert its row, or overwrite it."""
        step = await UpgradeStepRepository.get(session, name)
        if step is None:
            return await UpgradeStepRepository.record(
                session,
                name=name,
                tool_version=tool_version,
                summary=summary,
                created_by=created_by,
            )
        step.tool_version = tool_version
        step.summary = summary
        await session.flush()
        return step
