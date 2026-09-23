"""Repository for the post-migration upgrade-step ledger (control DB).

One row per completed one-shot step in ``upgrade_steps``; the cross-process
run lock serialising the steps lives in ``control/services/run_lock.py``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.toolkit_flattening_acks import ToolkitFlatteningAck
from jentic_one.control.core.schema.upgrade_steps import UpgradeStep


class UpgradeStepRepository:
    """Control-DB ledger reads/writes — flush-only, never commits."""

    @staticmethod
    async def get(session: AsyncSession, name: str) -> UpgradeStep | None:
        result = await session.execute(select(UpgradeStep).where(UpgradeStep.name == name))
        return result.scalar_one_or_none()

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
    async def count_flattening_acknowledgements(session: AsyncSession) -> int:
        result = await session.execute(select(func.count()).select_from(ToolkitFlatteningAck))
        return int(result.scalar_one())

    @staticmethod
    async def has_table(session: AsyncSession, table: str) -> bool:
        """Whether ``table`` exists in the session's (schema-scoped) database."""
        conn = await session.connection()
        return bool(await conn.run_sync(lambda sync: inspect(sync).has_table(table)))
