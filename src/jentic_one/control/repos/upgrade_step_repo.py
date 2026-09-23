"""Repository for the post-migration upgrade-step ledger and its run lock.

Two concerns, both control-DB:

- the ``upgrade_steps`` ledger (one row per completed one-shot step);
- a **session-level** Postgres advisory lock that serialises whole job runs
  spanning several transactions and both databases (the upgrade steps, and the
  toolkit-key retirement that also runs at boot and from the CLI). A
  transaction-scoped lock would release at the first commit, halfway through a
  run. The lock lives on the connection of the dedicated session passed in:
  the caller keeps that session open, **uncommitted**, for the whole run (a
  commit would hand the connection — and the lock — back to the pool) and
  releases explicitly; a crashed process releases it when its connection
  closes.

SQLite has no advisory locks. Its writers are serialised per transaction by
``BEGIN IMMEDIATE`` (see ``DatabaseSession.transaction``), and the job writes
are idempotent via natural keys, so the lock is a no-op there.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.toolkit_flattening_acks import ToolkitFlatteningAck
from jentic_one.control.core.schema.upgrade_steps import UpgradeStep

#: Advisory-lock keys (``pg_advisory_lock(bigint)``). Fixed constants rather
#: than ``hashtext(...)`` so an operator can find the holder in ``pg_locks``.
UPGRADE_STEPS_LOCK_KEY = 0x6A6F_5550_4752  # "joUPGR"
KEY_RETIREMENT_LOCK_KEY = 0x6A6F_4B52_5452  # "joKRTR"


def _is_postgres(session: AsyncSession) -> bool:
    return session.get_bind().dialect.name == "postgresql"


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
    async def acquire_run_lock(session: AsyncSession, key: int) -> None:
        """Block until this session holds the run lock ``key`` (Postgres only)."""
        if _is_postgres(session):
            await session.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})

    @staticmethod
    async def release_run_lock(session: AsyncSession, key: int) -> None:
        if _is_postgres(session):
            await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
